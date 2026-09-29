from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/azure-alert-to-issue.yml"


class AzureAlertWorkflowContractTests(unittest.TestCase):
    def test_runner_temp_uses_the_default_runner_environment(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("RUNNER_TEMP: ${{ runner.temp }}", text)

    def test_relevant_pushes_create_an_offline_validation_check(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        push_triggers = text.split("  push:\n", 1)[1].split("  schedule:\n", 1)[0]
        self.assertIn('.github/workflows/azure-alert-to-issue.yml', push_triggers)
        self.assertIn('scripts/azure_alert_to_issue.py', push_triggers)
        self.assertIn('tests/azure_alert_to_issue/**', push_triggers)
        validation_job = text.split("  validate-push:\n", 1)[1].split("  fixture:\n", 1)[0]
        self.assertIn("if: github.event_name == 'push'", validation_job)
        self.assertIn("permissions:\n      contents: read", validation_job)
        self.assertIn("python3 -m unittest discover -s tests/azure_alert_to_issue -v", validation_job)
        self.assertIn("--fixture tests/azure_alert_to_issue/fixtures/fired_alert.json", validation_job)
        self.assertNotIn("azure/login", validation_job)
        self.assertNotIn("--publish", validation_job)

    def test_workflow_is_scheduled_but_requires_explicit_configuration(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('cron: "17 */2 * * *"', text)
        self.assertIn("AZURE_ALERTS_ENABLED", text)
        self.assertIn("needs: configuration", text)
        self.assertIn("needs.configuration.outputs.ready == 'true'", text)
        self.assertIn("ready: ${{ steps.config.outputs.ready }}", text)
        self.assertIn("vars.AZURE_ALERTS_ENABLED == 'true'", text)
        self.assertIn("options: [fixture, poll]", text)
        self.assertIn("default: false", text)

    def test_permissions_are_minimal_and_scoped_to_jobs(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions: {}", text)
        poll_job = text.split("  poll:\n", 1)[1]
        permission_block = re.search(
            r"(?m)^    permissions:\n(?P<body>(?:      [a-z-]+: [a-z]+\n)+)",
            poll_job,
        )
        self.assertIsNotNone(permission_block)
        permissions = dict(re.findall(
            r"(?m)^      ([a-z-]+): ([a-z]+)$", permission_block.group("body")
        ))
        self.assertEqual(
            permissions,
            {"contents": "read", "id-token": "write", "issues": "write"},
        )
        configuration_job = text.split("  configuration:\n", 1)[1].split("  poll:\n", 1)[0]
        self.assertIn("permissions:\n      contents: read", configuration_job)
        self.assertNotIn("id-token: write", configuration_job)
        self.assertNotIn("issues: write", configuration_job)
        self.assertIn("persist-credentials: false", text)

    def test_fixture_path_never_publishes_and_live_poll_uses_oidc(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        fixture_job = text.split("  fixture:\n", 1)[1].split("  configuration:\n", 1)[0]
        self.assertIn("--fixture tests/azure_alert_to_issue/fixtures/fired_alert.json", fixture_job)
        self.assertNotIn("--publish", fixture_job)
        self.assertIn("uses: azure/login@v2", text)
        self.assertIn("PUBLISH_ALERTS:", text)


if __name__ == "__main__":
    unittest.main()
