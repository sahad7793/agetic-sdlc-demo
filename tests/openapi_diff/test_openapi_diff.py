import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("openapi_diff", ROOT / "scripts/openapi_diff.py")
openapi_diff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(openapi_diff)


class OpenApiDiffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.baseline = self.root / "baseline.json"
        self.current = self.root / "current.json"
        self.report = self.root / "report.md"
        self.document = {"openapi": "3.0.1", "paths": {"/api/tasks": {"get": {"responses": {"200": {}}}}}}
        self.baseline.write_text(json.dumps(self.document), encoding="utf-8")
        self.current.write_text(json.dumps(self.document), encoding="utf-8")

    def test_compare_reports_breaking_and_consumer_changes_without_failing(self):
        fake = self.root / "oasdiff"
        fake.write_text(
            "#!/bin/sh\n"
            'case "$1" in breaking) echo "GET /api/tasks removed" ;;'
            ' changelog) echo "GET /api/tasks changed" ;; esac\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)
        summary = self.root / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}):
            openapi_diff.compare(self.baseline, self.current, fake, self.report)

        content = self.report.read_text(encoding="utf-8")
        self.assertIn("GET /api/tasks removed", content)
        self.assertIn("GET /api/tasks changed", content)
        self.assertEqual(summary.read_text(encoding="utf-8"), content)

    def test_comparator_failure_is_not_reported_as_success(self):
        fake = self.root / "oasdiff"
        fake.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
        fake.chmod(0o755)

        with self.assertRaises(subprocess.CalledProcessError):
            openapi_diff.compare(self.baseline, self.current, fake, self.report)
        self.assertFalse(self.report.exists())

    def test_stale_pr_snapshot_is_reported(self):
        fake = self.root / "oasdiff"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        snapshot = self.root / openapi_diff.BASELINE
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text('{"outdated":true}', encoding="utf-8")
        with patch.object(openapi_diff, "ROOT", self.root):
            openapi_diff.compare(self.baseline, self.current, fake, self.report, bootstrap=True)
        report = self.report.read_text(encoding="utf-8")
        self.assertIn("Bootstrap:", report)
        self.assertIn("snapshot differs from the generated contract", report)

    def test_base_revision_is_used_even_if_pr_snapshot_changes(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        committed = self.root / openapi_diff.BASELINE
        committed.parent.mkdir(parents=True)
        committed.write_bytes(self.baseline.read_bytes())
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "commit", "-qm", "Baseline"],
            cwd=self.root, check=True,
        )
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        committed.write_text('{"openapi":"3.0.1","paths":{"/new":{}}}', encoding="utf-8")
        with patch.object(openapi_diff, "ROOT", self.root):
            self.assertFalse(openapi_diff.baseline_from_revision(revision, self.root / "resolved.json"))
        self.assertEqual((self.root / "resolved.json").read_bytes(), self.baseline.read_bytes())

    def test_bootstrap_only_when_base_lacks_snapshot(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "README").write_text("Initial", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "commit", "-qm", "Initial"],
            cwd=self.root, check=True,
        )
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        snapshot = self.root / openapi_diff.BASELINE
        snapshot.parent.mkdir(parents=True)
        snapshot.write_bytes(self.baseline.read_bytes())
        with patch.object(openapi_diff, "ROOT", self.root):
            self.assertTrue(openapi_diff.baseline_from_revision(revision, self.root / "resolved.json"))
        self.assertEqual((self.root / "resolved.json").read_bytes(), self.baseline.read_bytes())
        with self.assertRaises(ValueError):
            openapi_diff.baseline_from_revision("HEAD", self.root / "invalid.json")

    def test_actual_comparator_detects_removed_operation_and_required_request_field(self):
        binary = os.environ.get("OASDIFF_BIN")
        if not binary:
            self.skipTest("Set OASDIFF_BIN to run the pinned CLI integration case")
        document = json.loads((ROOT / openapi_diff.BASELINE).read_text(encoding="utf-8"))
        self.baseline.write_text(json.dumps(document), encoding="utf-8")
        document["paths"]["/api/tasks"].pop("get")
        document["components"]["schemas"]["CreateTaskRequest"]["properties"]["newRequiredField"] = {
            "type": "string"
        }
        document["components"]["schemas"]["CreateTaskRequest"]["required"].append("newRequiredField")
        self.current.write_text(json.dumps(document), encoding="utf-8")

        openapi_diff.compare(self.baseline, self.current, Path(binary), self.report)
        report = self.report.read_text(encoding="utf-8")
        breaking = report.split("## Breaking changes", 1)[1].split("## Consumer-facing changes", 1)[0]
        self.assertIn("GET", breaking)
        self.assertIn("/api/tasks", breaking)
        self.assertIn("newRequiredField", breaking)

    def test_workflow_uses_read_only_base_and_verified_comparator(self):
        workflow = (ROOT / ".github/workflows/openapi-diff.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read\n", workflow)
        self.assertIn("github.event.pull_request.base.sha", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("sha256sum --check", workflow)
        self.assertIn("GITHUB_STEP_SUMMARY", (ROOT / "scripts/openapi_diff.py").read_text())
        self.assertNotIn("--fail-on ", workflow)
        self.assertNotIn("pull_request_target:", workflow)


if __name__ == "__main__":
    unittest.main()
