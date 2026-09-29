import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "azure_alert_to_issue", ROOT / "scripts/azure_alert_to_issue.py"
)
alert_to_issue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(alert_to_issue)
FIXTURE = ROOT / "tests/azure_alert_to_issue/fixtures/fired_alert.json"
NOW = datetime(2026, 9, 29, 7, 0, tzinfo=timezone.utc)


def fixture_items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["value"]


class ConfigurationTests(unittest.TestCase):
    def test_parses_only_allowlisted_environments_and_safe_resource_group_names(self):
        scopes = alert_to_issue.parse_scopes(
            "staging=rg-taskmanagement-staging,production=rg-taskmanagement-production"
        )
        self.assertEqual(
            scopes,
            [
                {"environment": "staging", "resource_group": "rg-taskmanagement-staging"},
                {"environment": "production", "resource_group": "rg-taskmanagement-production"},
            ],
        )

    def test_rejects_duplicate_and_invalid_scope_entries(self):
        scopes = alert_to_issue.parse_scopes("staging=rg-a,staging=rg-b")
        self.assertEqual(len(scopes), 2)
        with self.assertRaisesRegex(ValueError, "repeat"):
            alert_to_issue.parse_scopes("staging=rg-a,staging=RG-A")
        with self.assertRaisesRegex(ValueError, "invalid resource group"):
            alert_to_issue.parse_scopes("production=rg;az rest")

    def test_configuration_requires_explicit_enable_and_oidc_ids(self):
        environment = {
            "AZURE_ALERTS_ENABLED": "true",
            "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
            "AZURE_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
            "AZURE_TENANT_ID": "22222222-2222-2222-2222-222222222222",
            "AZURE_ALERT_SCOPES": "staging=rg-taskmanagement-staging",
        }
        self.assertEqual(len(alert_to_issue.validate_configuration(environment)[1]), 1)
        environment["AZURE_ALERTS_ENABLED"] = "false"
        with self.assertRaisesRegex(ValueError, "not explicitly enabled"):
            alert_to_issue.validate_configuration(environment)


class RunPlanTests(unittest.TestCase):
    def environment(self, **overrides):
        values = {
            "AZURE_ALERTS_ENABLED": "true",
            "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
            "AZURE_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
            "AZURE_TENANT_ID": "22222222-2222-2222-2222-222222222222",
            "AZURE_ALERT_SCOPES": "staging=rg-taskmanagement-staging",
            "ALERT_TRIGGER": "workflow_dispatch",
            "ALERT_PUBLISH_REQUESTED": "false",
        }
        values.update(overrides)
        return {key: value for key, value in values.items() if value is not None}

    def test_manual_dry_run_needs_only_polling_opt_in(self):
        self.assertEqual(alert_to_issue.run_plan(self.environment()), (True, False, None))

    def test_manual_publish_requires_independent_publish_opt_in(self):
        with self.assertRaisesRegex(ValueError, "publication is not explicitly enabled"):
            alert_to_issue.run_plan(self.environment(ALERT_PUBLISH_REQUESTED="true"))
        ready, publish, fingerprint = alert_to_issue.run_plan(self.environment(
            ALERT_PUBLISH_REQUESTED="true", AZURE_ALERTS_PUBLISH_ENABLED="true",
            ALERT_FINGERPRINT_TARGET="a" * 64
        ))
        self.assertEqual((ready, publish), (True, True))

    def test_schedule_is_skipped_without_schedule_opt_in(self):
        for value in (None, "", "false", "yes"):
            ready, publish, fingerprint = alert_to_issue.run_plan(self.environment(
                ALERT_TRIGGER="schedule", AZURE_ALERTS_SCHEDULE_ENABLED=value,
                AZURE_ALERTS_PUBLISH_ENABLED="true"
            ))
            self.assertEqual((ready, publish, fingerprint), (False, False, None))
        ready, publish, fingerprint = alert_to_issue.run_plan(self.environment(
            ALERT_TRIGGER="schedule", AZURE_ALERTS_SCHEDULE_ENABLED="true",
            AZURE_ALERTS_PUBLISH_ENABLED="false"
        ))
        self.assertEqual((ready, publish, fingerprint), (True, True, None))

    def test_invalid_configuration_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Azure client ID is missing or invalid"):
            alert_to_issue.run_plan(self.environment(AZURE_CLIENT_ID=None))
        with self.assertRaisesRegex(ValueError, "polling is not explicitly enabled"):
            alert_to_issue.run_plan(self.environment(AZURE_ALERTS_ENABLED="false"))

    def test_unknown_trigger_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "schedule or manual dispatch"):
            alert_to_issue.run_plan(self.environment(ALERT_TRIGGER="push"))

    def test_configuration_output_records_ready_and_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            alert_to_issue.write_configuration(output, self.environment())
            self.assertEqual(output.read_text(encoding="utf-8"), "ready=true\npublish=false\n")

    def test_publish_poll_refuses_without_publish_opt_in(self):
        with patch.dict(alert_to_issue.os.environ, self.environment(), clear=True), \
                patch.object(alert_to_issue.sys, "argv", ["poller", "poll", "--publish"]), \
                patch.object(alert_to_issue, "AzureCli") as azure_cli, \
                patch.object(alert_to_issue, "process") as process:
            self.assertEqual(alert_to_issue.main(), 1)
        azure_cli.assert_not_called()
        process.assert_not_called()


class MappingTests(unittest.TestCase):
    def test_maps_fired_severity_and_uses_stable_hashed_identity(self):
        mapped = alert_to_issue.map_alert(
            fixture_items()[0], "staging", "rg-taskmanagement-staging"
        )
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["severity_tag"], "Sev2")
        self.assertEqual(mapped["severity_name"], "High")
        self.assertEqual(mapped["label"], "sev2")
        self.assertEqual(mapped["fired_at"], NOW.replace(hour=6))
        self.assertEqual(
            mapped["fingerprint"],
            alert_to_issue.fingerprint(fixture_items()[0]["id"]),
        )
        self.assertNotIn("fixture-alert-01", mapped["fingerprint"])

    def test_ignores_resolved_malformed_and_unknown_severity_alerts(self):
        items = fixture_items()
        self.assertIsNone(alert_to_issue.map_alert(items[1], "staging", "fixture"))
        missing_identity = json.loads(json.dumps(items[0]))
        del missing_identity["id"]
        self.assertIsNone(alert_to_issue.map_alert(missing_identity, "staging", "fixture"))
        unknown_severity = json.loads(json.dumps(items[0]))
        unknown_severity["properties"]["essentials"]["severity"] = "Sev5"
        self.assertIsNone(alert_to_issue.map_alert(unknown_severity, "staging", "fixture"))

    def test_issue_body_omits_untrusted_context_and_handles_hostile_rule_text(self):
        item = json.loads(json.dumps(fixture_items()[0]))
        item["properties"]["essentials"]["alertRule"] = (
            "Bad\n```\nInjected user@example.test token=do-not-copy"
        )
        item["properties"]["essentials"]["description"] = "person@example.test secret=never-copy"
        item["properties"]["alertContext"] = {"secret": "never-copy"}
        mapped = alert_to_issue.map_alert(item, "production", "rg-prod")
        body = alert_to_issue.build_issue(mapped)
        self.assertEqual(
            mapped["alert_rule"], "Bad ``` Injected REDACTED token REDACTED"
        )
        self.assertNotIn("person@example.test", body)
        self.assertNotIn("user@example.test", body)
        self.assertNotIn("do-not-copy", body)
        self.assertNotIn("never-copy", body)
        self.assertNotIn("Bad", body)
        self.assertNotIn("Injected", body)
        self.assertIn(alert_to_issue.marker_for(mapped["fingerprint"]), body)

    def test_omits_alert_rule_urls_and_resource_ids(self):
        item = json.loads(json.dumps(fixture_items()[0]))
        essentials = item["properties"]["essentials"]
        essentials["alertRule"] = "https://portal.example.test/alerts?tenant=private"
        mapped = alert_to_issue.map_alert(item, "staging", "fixture")
        self.assertEqual(mapped["alert_rule"], "Not provided")
        essentials["alertRule"] = "/subscriptions/private/resourceGroups/secret/providers/test/rules/rule"
        mapped = alert_to_issue.map_alert(item, "staging", "fixture")
        self.assertEqual(mapped["alert_rule"], "Not provided")

    def test_fingerprint_is_deterministic_and_does_not_reveal_identity(self):
        first = alert_to_issue.fingerprint("/subscriptions/private/alerts/secret-id")
        self.assertEqual(
            first, alert_to_issue.fingerprint("/subscriptions/private/alerts/secret-id")
        )
        self.assertNotIn("secret-id", first)


class CollectionTests(unittest.TestCase):
    def test_follows_only_alerts_management_pagination(self):
        expected_path = (
            "/subscriptions/00000000-0000-0000-0000-000000000000/"
            "resourceGroups/rg-a/providers/Microsoft.AlertsManagement/alerts"
        )
        next_link = f"https://management.azure.com{expected_path}?skip=1"
        self.assertEqual(
            alert_to_issue.validate_next_link(next_link, expected_path), next_link
        )
        self.assertEqual(
            alert_to_issue.validate_next_link(
                next_link.replace("management.azure.com/", "management.azure.com:443/"),
                expected_path,
            ),
            next_link.replace("management.azure.com/", "management.azure.com:443/"),
        )
        with self.assertRaisesRegex(ValueError, "pagination link"):
            alert_to_issue.validate_next_link("https://attacker.test/steal", expected_path)

    def test_fails_closed_when_alert_count_exceeds_run_limit(self):
        item = fixture_items()[0]

        class Azure:
            def run_json(self, url):
                return {"value": [item] * (alert_to_issue.MAX_ALERTS_PER_RUN + 1)}

        with self.assertRaisesRegex(ValueError, "per-run limit"):
            alert_to_issue.collect_alerts(
                Azure(),
                "00000000-0000-0000-0000-000000000000",
                [{"environment": "staging", "resource_group": "rg-a"}],
            )

    def test_alert_request_filters_to_fired_minimal_essentials(self):
        urls = []

        class Azure:
            def run_json(self, url):
                urls.append(url)
                return {"value": []}

        alert_to_issue.collect_alerts(
            Azure(),
            "00000000-0000-0000-0000-000000000000",
            [{"environment": "staging", "resource_group": "rg-a"}],
        )
        self.assertEqual(len(urls), 1)
        self.assertIn("monitorCondition=Fired", urls[0])
        self.assertIn("includeContext=false", urls[0])
        self.assertIn("includeEgressConfig=false", urls[0])
        self.assertIn("select=severity,monitorCondition,alertRule,startDateTime", urls[0])

    def test_fixture_processing_is_dry_run(self):
        alerts = [(item, "staging", "synthetic-fixture") for item in fixture_items()]
        counts = alert_to_issue.process(alerts, now=NOW, enforce_freshness=False)
        self.assertEqual(counts, {
            "observed": 2, "fired": 1, "skipped": 1, "created": 0, "duplicates": 0,
        })


class FingerprintValidationTests(unittest.TestCase):
    def test_rejects_invalid_fingerprint_formats(self):
        with self.assertRaisesRegex(ValueError, "64 hexadecimal characters"):
            alert_to_issue.validate_fingerprint_target("not-a-hash")
        with self.assertRaisesRegex(ValueError, "64 hexadecimal characters"):
            alert_to_issue.validate_fingerprint_target("aabbccdd" * 7)  # 56 chars
        with self.assertRaisesRegex(ValueError, "64 hexadecimal characters"):
            alert_to_issue.validate_fingerprint_target("aabbccdd" * 8 + "00")  # 66 chars
        with self.assertRaisesRegex(ValueError, "64 hexadecimal characters"):
            alert_to_issue.validate_fingerprint_target("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")

    def test_accepts_valid_64_hex_fingerprint(self):
        valid = "a" * 64
        self.assertEqual(alert_to_issue.validate_fingerprint_target(valid), valid)
        self.assertEqual(
            alert_to_issue.validate_fingerprint_target("A" * 64),
            "a" * 64  # Lowercase normalization
        )

    def test_accepts_empty_fingerprint_for_scheduled_runs(self):
        self.assertIsNone(alert_to_issue.validate_fingerprint_target(""))
        self.assertIsNone(alert_to_issue.validate_fingerprint_target(None))
        self.assertIsNone(alert_to_issue.validate_fingerprint_target("   "))


class ManualAlertSelectionTests(unittest.TestCase):
    def environment(self, **overrides):
        values = {
            "AZURE_ALERTS_ENABLED": "true",
            "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
            "AZURE_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
            "AZURE_TENANT_ID": "22222222-2222-2222-2222-222222222222",
            "AZURE_ALERT_SCOPES": "staging=rg-taskmanagement-staging",
            "ALERT_TRIGGER": "workflow_dispatch",
            "ALERT_PUBLISH_REQUESTED": "false",
        }
        values.update(overrides)
        return {key: value for key, value in values.items() if value is not None}

    def test_manual_dry_run_does_not_require_fingerprint(self):
        ready, publish, fingerprint = alert_to_issue.run_plan(self.environment())
        self.assertEqual((ready, publish, fingerprint), (True, False, None))

    def test_manual_publish_requires_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "target alert fingerprint"):
            alert_to_issue.run_plan(self.environment(
                ALERT_PUBLISH_REQUESTED="true",
                AZURE_ALERTS_PUBLISH_ENABLED="true"
            ))

    def test_manual_publish_with_valid_fingerprint(self):
        ready, publish, fingerprint = alert_to_issue.run_plan(self.environment(
            ALERT_PUBLISH_REQUESTED="true",
            AZURE_ALERTS_PUBLISH_ENABLED="true",
            ALERT_FINGERPRINT_TARGET="a" * 64
        ))
        self.assertEqual((ready, publish), (True, True))
        self.assertEqual(fingerprint, "a" * 64)

    def test_process_filters_alerts_by_fingerprint(self):
        item = fixture_items()[0]
        mapped = alert_to_issue.map_alert(item, "staging", "fixture")
        target_fingerprint = mapped["fingerprint"]
        
        # With matching fingerprint
        alerts = [(item, "staging", "fixture")]
        counts = alert_to_issue.process(
            alerts, publish=False, fingerprint_target=target_fingerprint, now=NOW, enforce_freshness=False
        )
        self.assertEqual(counts["fired"], 1)
        
        # With non-matching fingerprint
        wrong_fingerprint = "b" * 64
        with self.assertRaisesRegex(ValueError, "No alert matched"):
            alert_to_issue.process(
                alerts, publish=False, fingerprint_target=wrong_fingerprint, now=NOW, enforce_freshness=False
            )

    def test_process_fails_on_multiple_matching_fingerprints(self):
        item = fixture_items()[0]
        mapped = alert_to_issue.map_alert(item, "staging", "fixture")
        target_fingerprint = mapped["fingerprint"]
        
        # Two alerts with same fingerprint should fail
        alerts = [(item, "staging", "fixture"), (item, "production", "fixture")]
        with self.assertRaisesRegex(ValueError, "Multiple alerts matched"):
            alert_to_issue.process(
                alerts, publish=False, fingerprint_target=target_fingerprint, now=NOW, enforce_freshness=False
            )


class ScheduledVsManualControlTests(unittest.TestCase):
    def environment(self, **overrides):
        values = {
            "AZURE_ALERTS_ENABLED": "true",
            "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
            "AZURE_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
            "AZURE_TENANT_ID": "22222222-2222-2222-2222-222222222222",
            "AZURE_ALERT_SCOPES": "staging=rg-taskmanagement-staging",
            "ALERT_TRIGGER": "schedule",
        }
        values.update(overrides)
        return {key: value for key, value in values.items() if value is not None}

    def test_legacy_publish_flag_does_not_enable_schedule(self):
        ready, publish, fingerprint = alert_to_issue.run_plan(self.environment(
            AZURE_ALERTS_PUBLISH_ENABLED="true"
        ))
        self.assertEqual((ready, publish, fingerprint), (False, False, None))

    def test_schedule_can_use_new_schedule_enabled_flag(self):
        ready, publish, fingerprint = alert_to_issue.run_plan(self.environment(
            AZURE_ALERTS_SCHEDULE_ENABLED="true",
            AZURE_ALERTS_PUBLISH_ENABLED="false"
        ))
        self.assertEqual((ready, publish, fingerprint), (True, True, None))

    def test_scheduled_publish_gate_uses_only_schedule_flag(self):
        alert_to_issue.require_publishing_enabled(self.environment(
            AZURE_ALERTS_SCHEDULE_ENABLED="true",
            AZURE_ALERTS_PUBLISH_ENABLED="false"
        ))
        with self.assertRaisesRegex(ValueError, "Scheduled Azure alert issue publication"):
            alert_to_issue.require_publishing_enabled(self.environment(
                AZURE_ALERTS_SCHEDULE_ENABLED="false",
                AZURE_ALERTS_PUBLISH_ENABLED="true"
            ))

    def test_schedule_skipped_without_schedule_flag(self):
        ready, publish, fingerprint = alert_to_issue.run_plan(self.environment())
        self.assertEqual((ready, publish, fingerprint), (False, False, None))



    def test_publish_skips_existing_fingerprint_and_creates_only_once(self):
        alerts = [(fixture_items()[0], "staging", "fixture")]
        mapped = alert_to_issue.map_alert(fixture_items()[0], "staging", "fixture")
        issue_list = json.dumps([{"body": alert_to_issue.build_issue(mapped)}])
        with patch.object(alert_to_issue, "command", return_value=issue_list) as command:
            counts = alert_to_issue.process(alerts, publish=True, now=NOW)
        self.assertEqual(counts["duplicates"], 1)
        self.assertEqual(counts["created"], 0)
        self.assertEqual(command.call_count, 1)

    def test_publish_records_fingerprint_after_first_occurrence_in_same_poll(self):
        alerts = [(fixture_items()[0], "staging", "fixture")] * 2
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"RUNNER_TEMP": directory}), \
                patch.object(alert_to_issue, "ensure_labels"), \
                patch.object(alert_to_issue, "command", side_effect=[
                    "[]", "https://github.com/example/repo/issues/1\n",
                ]) as command:
            counts = alert_to_issue.process(alerts, publish=True, now=NOW)
        self.assertEqual(counts["created"], 1)
        self.assertEqual(counts["duplicates"], 1)
        self.assertEqual(command.call_count, 2)

    def test_issue_scan_fails_closed_at_limit(self):
        payload = json.dumps([{"body": ""}] * alert_to_issue.MAX_EXISTING_ISSUES)
        with patch.object(alert_to_issue, "command", return_value=payload):
            with self.assertRaisesRegex(ValueError, "deduplication scan limit"):
                alert_to_issue.existing_fingerprints()


if __name__ == "__main__":
    unittest.main()
