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
        self.assertIn("alert_fingerprint:", text)
        self.assertIn('default: ""', text)

    def _job(self, name, following):
        text = WORKFLOW.read_text(encoding="utf-8")
        body = text.split(f"  {name}:\n", 1)[1]
        return body.split(f"  {following}:\n", 1)[0] if following else body

    def _permissions(self, job):
        block = re.search(
            r"(?m)^    permissions:\n(?P<body>(?:      [a-z-]+: [a-z]+\n)+)", job
        )
        self.assertIsNotNone(block)
        return dict(re.findall(r"(?m)^      ([a-z-]+): ([a-z]+)$", block.group("body")))

    def test_permissions_are_minimal_and_scoped_to_jobs(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("permissions: {}", text)
        self.assertNotIn("\n  poll:\n", text)
        self.assertEqual(
            self._permissions(self._job("poll-publish", None)),
            {"contents": "read", "id-token": "write", "issues": "write"},
        )
        self.assertEqual(
            self._permissions(self._job("poll-dry-run", "poll-publish")),
            {"contents": "read", "id-token": "write"},
        )
        configuration_job = self._job("configuration", "poll-dry-run")
        self.assertEqual(self._permissions(configuration_job), {"contents": "read"})
        self.assertIn("persist-credentials: false", text)

    def test_fixture_path_never_publishes_and_live_poll_uses_oidc(self):
        fixture_job = self._job("fixture", "configuration")
        self.assertIn("--fixture tests/azure_alert_to_issue/fixtures/fired_alert.json", fixture_job)
        self.assertNotIn("--publish", fixture_job)
        self.assertNotIn("azure/login", fixture_job)
        self.assertNotIn("environment:", fixture_job)
        for job in (self._job("poll-dry-run", "poll-publish"), self._job("poll-publish", None)):
            self.assertIn("uses: azure/login@v2", job)
            self.assertIn("environment: azure-alerts", job)
            self.assertNotIn("secrets.", job)

    def test_schedule_requires_independent_publish_opt_in(self):
        condition = self._job("configuration", "poll-dry-run").split("runs-on:", 1)[0]
        self.assertIn("vars.AZURE_ALERTS_ENABLED == 'true' &&", condition)
        self.assertIn(
            "(github.event_name == 'schedule' &&\n"
            "        vars.AZURE_ALERTS_SCHEDULE_ENABLED == 'true' &&\n"
            "        vars.AZURE_ALERTS_PUBLISH_ENABLED == 'true')",
            condition,
        )
        self.assertIn("(github.event_name == 'workflow_dispatch' && inputs.mode == 'poll')", condition)
        configuration_job = self._job("configuration", "poll-dry-run")
        self.assertIn("ALERT_TRIGGER: ${{ github.event_name }}", configuration_job)
        self.assertIn(
            "ALERT_PUBLISH_REQUESTED: ${{ github.event_name == 'workflow_dispatch' && inputs.publish }}",
            configuration_job,
        )
        self.assertIn(
            "ALERT_FINGERPRINT: ${{ github.event_name == 'workflow_dispatch' && inputs.alert_fingerprint || '' }}",
            configuration_job,
        )
        self.assertIn(
            "AZURE_ALERTS_SCHEDULE_ENABLED: ${{ vars.AZURE_ALERTS_SCHEDULE_ENABLED }}",
            configuration_job,
        )
        self.assertIn("publish: ${{ steps.config.outputs.publish }}", configuration_job)

    def test_dry_run_job_cannot_create_issues(self):
        dry_run = self._job("poll-dry-run", "poll-publish")
        self.assertIn("needs.configuration.outputs.publish == 'false'", dry_run)
        self.assertNotIn("--publish", dry_run)
        self.assertNotIn("issues: write", dry_run)
        self.assertNotIn("GH_TOKEN", dry_run)
        self.assertNotIn("github.token", dry_run)
        self.assertNotIn("AZURE_ALERTS_PUBLISH_ENABLED", dry_run)

    def test_publish_job_requires_publish_plan_and_opt_in(self):
        publish = self._job("poll-publish", None)
        self.assertIn("needs.configuration.outputs.publish == 'true'", publish)
        self.assertIn("vars.AZURE_ALERTS_PUBLISH_ENABLED == 'true'", publish)
        self.assertIn(
            "AZURE_ALERTS_PUBLISH_ENABLED: ${{ vars.AZURE_ALERTS_PUBLISH_ENABLED }}", publish
        )
        self.assertIn(
            "AZURE_ALERTS_SCHEDULE_ENABLED: ${{ vars.AZURE_ALERTS_SCHEDULE_ENABLED }}", publish
        )
        self.assertIn("ALERT_TRIGGER: ${{ github.event_name }}", publish)
        self.assertIn(
            "ALERT_FINGERPRINT: ${{ github.event_name == 'workflow_dispatch' && inputs.alert_fingerprint || '' }}",
            publish,
        )
        self.assertIn("python3 scripts/azure_alert_to_issue.py poll --publish", publish)
        self.assertNotIn("inputs.publish", publish)


if __name__ == "__main__":
    unittest.main()
