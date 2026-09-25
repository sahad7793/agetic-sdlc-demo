import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("incident_report", ROOT / "scripts/incident_report.py")
incident_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(incident_report)

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)


def base_inputs(**overrides):
    inputs = {
        "environment": "staging",
        "severity": "Sev1 - Critical",
        "alert_source": "Azure Monitor alert (forwarded)",
        "alert_title": "Elevated HTTP 5xx",
        "alert_summary": "5xx rate alert fired at 11:55 UTC.",
        "correlation_id": "",
        "log_query_link": "",
        "azure_alert_link": "",
    }
    inputs.update(overrides)
    return inputs


def env_for(inputs):
    return {
        "INPUT_ENVIRONMENT": inputs["environment"],
        "INPUT_SEVERITY": inputs["severity"],
        "INPUT_ALERT_SOURCE": inputs["alert_source"],
        "INPUT_ALERT_TITLE": inputs["alert_title"],
        "INPUT_ALERT_SUMMARY": inputs["alert_summary"],
        "INPUT_CORRELATION_ID": inputs["correlation_id"],
        "INPUT_LOG_QUERY_LINK": inputs["log_query_link"],
        "INPUT_AZURE_ALERT_LINK": inputs["azure_alert_link"],
    }


class ReadInputsTests(unittest.TestCase):
    def test_valid_inputs_round_trip(self):
        inputs = base_inputs()
        with patch.dict(os.environ, env_for(inputs), clear=False):
            self.assertEqual(incident_report.read_inputs(), inputs)

    def test_rejects_unknown_environment(self):
        inputs = base_inputs(environment="prod-typo")
        with patch.dict(os.environ, env_for(inputs), clear=False):
            with self.assertRaisesRegex(ValueError, "Invalid environment"):
                incident_report.read_inputs()

    def test_rejects_unknown_severity(self):
        inputs = base_inputs(severity="Sev0 - Made up")
        with patch.dict(os.environ, env_for(inputs), clear=False):
            with self.assertRaisesRegex(ValueError, "Invalid severity"):
                incident_report.read_inputs()

    def test_rejects_blank_title(self):
        inputs = base_inputs(alert_title="   ")
        with patch.dict(os.environ, env_for(inputs), clear=False):
            with self.assertRaisesRegex(ValueError, "alert title"):
                incident_report.read_inputs()

    def test_rejects_oversized_title(self):
        inputs = base_inputs(alert_title="x" * 201)
        with patch.dict(os.environ, env_for(inputs), clear=False):
            with self.assertRaisesRegex(ValueError, "alert title"):
                incident_report.read_inputs()

    def test_rejects_blank_summary(self):
        inputs = base_inputs(alert_summary="")
        with patch.dict(os.environ, env_for(inputs), clear=False):
            with self.assertRaisesRegex(ValueError, "alert summary"):
                incident_report.read_inputs()


class TitleTests(unittest.TestCase):
    def test_build_title_includes_short_severity_tag_env_and_title(self):
        title = incident_report.build_title("production", "Sev2 - High", "Latency spike")
        self.assertEqual(title, "[INCIDENT][Sev2][production] Latency spike")


class BodyTests(unittest.TestCase):
    def test_optional_fields_render_placeholder_when_missing(self):
        inputs = base_inputs()
        body = incident_report.build_body(inputs, NOW, "https://example.test/run/1", "octocat")
        self.assertIn(incident_report.NOT_PROVIDED, body)
        self.assertIn("5xx rate alert fired at 11:55 UTC.", body)

    def test_optional_fields_are_echoed_verbatim_when_present(self):
        inputs = base_inputs(correlation_id="abc-123", log_query_link="https://logs.example/q",
                              azure_alert_link="https://portal.azure.com/alert/1")
        body = incident_report.build_body(inputs, NOW, "https://example.test/run/1", "octocat")
        self.assertIn("abc-123", body)
        self.assertIn("https://logs.example/q", body)
        self.assertIn("https://portal.azure.com/alert/1", body)
        self.assertNotIn(incident_report.NOT_PROVIDED, body)

    def test_sla_targets_are_computed_from_creation_time_not_measured(self):
        inputs = base_inputs(severity="Sev1 - Critical")
        body = incident_report.build_body(inputs, NOW, "https://example.test/run/1", "octocat")
        self.assertIn("Acknowledge by:** 2026-09-25 12:15 UTC (target: 15 min)", body)
        self.assertIn("Mitigate by:** 2026-09-25 13:00 UTC (target: 60 min)", body)
        self.assertIn("not a measurement", body.lower())

    def test_sla_targets_differ_by_severity(self):
        inputs = base_inputs(severity="Sev4 - Low")
        body = incident_report.build_body(inputs, NOW, "https://example.test/run/1", "octocat")
        self.assertIn("Acknowledge by:** 2026-09-25 20:00 UTC (target: 480 min)", body)

    def test_body_never_recommends_auto_remediation(self):
        inputs = base_inputs()
        body = incident_report.build_body(inputs, NOW, "https://example.test/run/1", "octocat")
        self.assertIn("dispatch the **Rollback** workflow", body)
        self.assertNotIn("automatically roll back", body.lower())

    def test_provenance_records_actor_and_run_url(self):
        inputs = base_inputs()
        body = incident_report.build_body(inputs, NOW, "https://example.test/run/42", "octocat")
        self.assertIn("Filed by:** @octocat", body)
        self.assertIn("https://example.test/run/42", body)


class EnsureLabelsTests(unittest.TestCase):
    def test_creates_only_missing_labels(self):
        calls = []

        def fake_command(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("gh", "label", "list"):
                return json.dumps([{"name": "incident"}])
            return ""

        with patch.object(incident_report, "command", side_effect=fake_command):
            incident_report.ensure_labels({"incident", "sev1"})

        create_calls = [call for call in calls if call[:3] == ("gh", "label", "create")]
        self.assertEqual(len(create_calls), 1)
        self.assertEqual(create_calls[0][3], "sev1")

    def test_no_create_calls_when_all_labels_present(self):
        calls = []

        def fake_command(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("gh", "label", "list"):
                return json.dumps([{"name": "incident"}, {"name": "sev1"}])
            return ""

        with patch.object(incident_report, "command", side_effect=fake_command):
            incident_report.ensure_labels({"incident", "sev1"})

        create_calls = [call for call in calls if call[:3] == ("gh", "label", "create")]
        self.assertEqual(create_calls, [])


class PerformTests(unittest.TestCase):
    def test_perform_creates_issue_and_records_audit(self):
        inputs = base_inputs()
        env = env_for(inputs)
        env.update({"ACTOR": "octocat", "RUN_URL": "https://example.test/run/1"})
        calls = []

        def fake_command(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("gh", "label", "list"):
                return json.dumps([{"name": "incident"}, {"name": "sev1"}])
            if args[:3] == ("gh", "issue", "create"):
                return "https://github.com/example/repo/issues/99\n"
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            with patch.dict(os.environ, env, clear=False), \
                    patch.object(incident_report, "command", side_effect=fake_command):
                incident_report.perform(audit_path)

            audit = json.loads(audit_path.read_text())
        self.assertEqual(audit["outcome"], "Succeeded: incident issue created")
        self.assertEqual(audit["issue_url"], "https://github.com/example/repo/issues/99")
        issue_create_calls = [call for call in calls if call[:3] == ("gh", "issue", "create")]
        self.assertEqual(len(issue_create_calls), 1)
        self.assertIn("octocat", issue_create_calls[0])

    def test_perform_records_failure_without_raising_out_of_audit(self):
        inputs = base_inputs(alert_title="")
        env = env_for(inputs)
        env.update({"ACTOR": "octocat", "RUN_URL": "https://example.test/run/1"})

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaises(ValueError):
                    incident_report.perform(audit_path)

            audit = json.loads(audit_path.read_text())
        self.assertTrue(audit["outcome"].startswith("Failed:"))
        self.assertIn("alert title", audit["error"])


if __name__ == "__main__":
    unittest.main()
