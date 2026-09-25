import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("perf_gate", ROOT / "scripts/perf_gate.py")
perf_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(perf_gate)


def make_summary(*, reads=True, lifecycle=True, run_seconds=10.0, reads_p95=100.0, reads_error_rate=0.0,
                  lifecycle_p95=150.0, lifecycle_error_rate=0.0, reads_count=300, lifecycle_count=100):
    metrics = {}
    if reads:
        metrics["reads_duration"] = {
            "values": {"med": reads_p95 / 2, "p(95)": reads_p95, "p(99)": reads_p95 * 1.1, "count": reads_count},
        }
        metrics["reads_errors"] = {"values": {"rate": reads_error_rate}}
    if lifecycle:
        metrics["lifecycle_duration"] = {
            "values": {"med": lifecycle_p95 / 2, "p(95)": lifecycle_p95, "p(99)": lifecycle_p95 * 1.1,
                       "count": lifecycle_count},
        }
        metrics["lifecycle_errors"] = {"values": {"rate": lifecycle_error_rate}}
    return {
        "metrics": metrics,
        "state": {"testRunDurationMs": run_seconds * 1000},
    }


def unset_baseline():
    return {"status": "unset", "tolerance": dict(perf_gate.DEFAULT_TOLERANCE), "scenarios": {}}


def set_baseline(**overrides):
    baseline = {
        "status": "set",
        "tolerance": dict(perf_gate.DEFAULT_TOLERANCE),
        "scenarios": {
            "reads": {"p95_ms": 100.0, "p99_ms": 110.0, "error_rate": 0.0, "requests_per_s": 30.0},
            "lifecycle": {"p95_ms": 150.0, "p99_ms": 165.0, "error_rate": 0.0, "requests_per_s": 10.0},
        },
    }
    baseline.update(overrides)
    return baseline


class ExtractMetricsTests(unittest.TestCase):
    def test_extracts_both_scenarios(self):
        observed = perf_gate.extract_metrics(make_summary())
        self.assertEqual(set(observed), {"reads", "lifecycle"})
        self.assertEqual(observed["reads"]["p95_ms"], 100.0)
        self.assertEqual(observed["reads"]["count"], 300)
        self.assertAlmostEqual(observed["reads"]["requests_per_s"], 30.0)

    def test_missing_scenario_is_skipped_not_fabricated(self):
        observed = perf_gate.extract_metrics(make_summary(lifecycle=False))
        self.assertEqual(set(observed), {"reads"})

    def test_missing_test_run_duration_yields_no_throughput(self):
        summary = make_summary()
        del summary["state"]["testRunDurationMs"]
        observed = perf_gate.extract_metrics(summary)
        self.assertIsNone(observed["reads"]["requests_per_s"])

    def test_rejects_non_k6_json(self):
        with self.assertRaisesRegex(perf_gate.GateError, "no 'metrics'"):
            perf_gate.extract_metrics({"not": "a k6 summary"})


class EvaluateTests(unittest.TestCase):
    def test_unset_baseline_reports_no_baseline_for_every_row(self):
        observed = perf_gate.extract_metrics(make_summary())
        rows = perf_gate.evaluate(observed, unset_baseline())
        self.assertTrue(rows)
        self.assertTrue(all(row["status"] == "no-baseline" for row in rows))

    def test_set_baseline_passes_when_within_tolerance(self):
        observed = perf_gate.extract_metrics(make_summary(reads_p95=110.0, lifecycle_p95=160.0))
        rows = perf_gate.evaluate(observed, set_baseline())
        statuses = {(row["scenario"], row["metric"]): row["status"] for row in rows}
        self.assertEqual(statuses[("reads", "p95_ms")], "pass")
        self.assertEqual(statuses[("lifecycle", "p95_ms")], "pass")

    def test_set_baseline_fails_when_latency_regresses_beyond_multiplier(self):
        # Baseline p95 is 100ms, multiplier 1.5x -> allowed threshold 150ms.
        observed = perf_gate.extract_metrics(make_summary(reads_p95=200.0))
        rows = perf_gate.evaluate(observed, set_baseline())
        statuses = {(row["scenario"], row["metric"]): row["status"] for row in rows}
        self.assertEqual(statuses[("reads", "p95_ms")], "fail")

    def test_set_baseline_fails_when_throughput_drops_below_multiplier(self):
        # Baseline throughput is 30 req/s, min multiplier 0.7 -> allowed floor 21 req/s.
        observed = perf_gate.extract_metrics(make_summary(reads_count=60, run_seconds=10.0))
        rows = perf_gate.evaluate(observed, set_baseline())
        statuses = {(row["scenario"], row["metric"]): row["status"] for row in rows}
        self.assertEqual(statuses[("reads", "requests_per_s")], "fail")

    def test_error_rate_above_cap_fails(self):
        observed = perf_gate.extract_metrics(make_summary(reads_error_rate=0.05))
        rows = perf_gate.evaluate(observed, set_baseline())
        statuses = {(row["scenario"], row["metric"]): row["status"] for row in rows}
        self.assertEqual(statuses[("reads", "error_rate")], "fail")

    def test_scenario_missing_from_baseline_is_no_baseline_not_fail(self):
        observed = perf_gate.extract_metrics(make_summary())
        baseline = set_baseline(scenarios={"reads": set_baseline()["scenarios"]["reads"]})
        rows = perf_gate.evaluate(observed, baseline)
        statuses = {(row["scenario"], row["metric"]): row["status"] for row in rows}
        self.assertEqual(statuses[("lifecycle", "p95_ms")], "no-baseline")


class DecideExitCodeTests(unittest.TestCase):
    def test_advisory_mode_always_exits_zero_even_with_failures(self):
        rows = [{"status": "fail"}]
        self.assertEqual(perf_gate.decide_exit_code(rows, "advisory"), 0)

    def test_strict_mode_exits_nonzero_on_failure(self):
        rows = [{"status": "pass"}, {"status": "fail"}]
        self.assertEqual(perf_gate.decide_exit_code(rows, "strict"), 1)

    def test_strict_mode_exits_zero_when_no_failures(self):
        rows = [{"status": "pass"}, {"status": "no-baseline"}]
        self.assertEqual(perf_gate.decide_exit_code(rows, "strict"), 0)


class RenderReportTests(unittest.TestCase):
    def test_flags_bootstrap_baseline(self):
        observed = perf_gate.extract_metrics(make_summary())
        rows = perf_gate.evaluate(observed, unset_baseline())
        report = perf_gate.render_report(rows, unset_baseline(), "advisory")
        self.assertIn("No reviewed baseline yet", report)
        self.assertIn("| reads | p95_ms |", report)

    def test_does_not_warn_once_baseline_is_set(self):
        observed = perf_gate.extract_metrics(make_summary())
        rows = perf_gate.evaluate(observed, set_baseline())
        report = perf_gate.render_report(rows, set_baseline(), "strict")
        self.assertNotIn("No reviewed baseline yet", report)
        self.assertIn("Mode: `strict`", report)


class RunCompareIntegrationTests(unittest.TestCase):
    def test_compare_writes_report_and_exits_zero_in_advisory_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            baseline_path = tmp_path / "baseline.json"
            report_path = tmp_path / "gate-report.md"
            summary_path.write_text(json.dumps(make_summary(reads_p95=500.0)))
            baseline_path.write_text(json.dumps(set_baseline()))

            args = perf_gate.build_parser().parse_args([
                "compare", "--summary", str(summary_path), "--baseline", str(baseline_path),
                "--report-out", str(report_path), "--mode", "advisory",
            ])
            exit_code = args.func(args)

            self.assertEqual(exit_code, 0)
            self.assertIn("❌ fail", report_path.read_text())

    def test_compare_exits_nonzero_in_strict_mode_on_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            baseline_path = tmp_path / "baseline.json"
            report_path = tmp_path / "gate-report.md"
            summary_path.write_text(json.dumps(make_summary(reads_p95=500.0)))
            baseline_path.write_text(json.dumps(set_baseline()))

            args = perf_gate.build_parser().parse_args([
                "compare", "--summary", str(summary_path), "--baseline", str(baseline_path),
                "--report-out", str(report_path), "--mode", "strict",
            ])
            exit_code = args.func(args)

            self.assertEqual(exit_code, 1)

    def test_compare_raises_gate_error_for_missing_file(self):
        with self.assertRaises(perf_gate.GateError):
            perf_gate.load_json(Path("/nonexistent/does-not-exist.json"))


class CaptureBaselineTests(unittest.TestCase):
    def test_writes_set_baseline_preserving_existing_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            baseline_path = tmp_path / "baseline.json"
            summary_path.write_text(json.dumps(make_summary()))
            baseline_path.write_text(json.dumps(unset_baseline()))

            args = perf_gate.build_parser().parse_args([
                "capture-baseline", "--from", str(summary_path), "--out", str(baseline_path),
                "--captured-by", "test-user", "--git-sha", "deadbeef",
            ])
            exit_code = args.func(args)

            self.assertEqual(exit_code, 0)
            written = json.loads(baseline_path.read_text())
            self.assertEqual(written["status"], "set")
            self.assertEqual(written["captured_by"], "test-user")
            self.assertEqual(written["git_sha"], "deadbeef")
            self.assertEqual(written["tolerance"], perf_gate.DEFAULT_TOLERANCE)
            self.assertIn("reads", written["scenarios"])
            self.assertIn("lifecycle", written["scenarios"])

    def test_raises_when_summary_has_no_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path = tmp_path / "summary.json"
            summary_path.write_text(json.dumps({"metrics": {}, "state": {}}))

            args = perf_gate.build_parser().parse_args([
                "capture-baseline", "--from", str(summary_path), "--out", str(tmp_path / "baseline.json"),
            ])
            with self.assertRaises(perf_gate.GateError):
                args.func(args)


if __name__ == "__main__":
    unittest.main()
