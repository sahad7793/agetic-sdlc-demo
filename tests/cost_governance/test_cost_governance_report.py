import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "cost_governance_report",
    Path(__file__).resolve().parents[2] / "scripts" / "cost_governance_report.py",
)
governance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governance)


class FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def resource(name="res", type_="Microsoft.App/containerApps", tags=None):
    return {"name": name, "type": type_, "tags": tags}


COMPLIANT_TAGS = {
    "application": "taskmanagement",
    "environment": "staging",
    "costCenter": "cc-1",
    "owner": "team-a",
}


class AzureCliTests(unittest.TestCase):
    def test_run_json_parses_stdout(self):
        with patch("subprocess.run", return_value=FakeCompletedProcess(stdout="[1, 2]")):
            self.assertEqual(governance.AzureCli().run_json(["resource", "list"]), [1, 2])

    def test_run_json_treats_empty_stdout_as_empty_list(self):
        with patch("subprocess.run", return_value=FakeCompletedProcess(stdout="")):
            self.assertEqual(governance.AzureCli().run_json(["consumption", "budget", "list"]), [])

    def test_run_json_raises_on_nonzero_exit(self):
        with patch("subprocess.run", return_value=FakeCompletedProcess(stderr="denied", returncode=1)):
            with self.assertRaises(governance.AzCliError):
                governance.AzureCli().run_json(["resource", "list"])


class ResourceGroupInventoryTests(unittest.TestCase):
    def test_counts_fully_tagged_resources_as_compliant(self):
        az = unittest.mock.Mock()
        az.run_json.return_value = [
            resource(tags=COMPLIANT_TAGS),
            resource(name="res2", tags={"application": "taskmanagement"}),
        ]
        result = governance.resource_group_inventory(az, "rg-example")
        self.assertEqual(result["total_resources"], 2)
        self.assertEqual(result["tag_compliant_resources"], 1)
        self.assertEqual(result["resource_types"], {"Microsoft.App/containerApps": 2})

    def test_missing_tags_object_is_not_compliant(self):
        az = unittest.mock.Mock()
        az.run_json.return_value = [resource(tags=None)]
        result = governance.resource_group_inventory(az, "rg-example")
        self.assertEqual(result["tag_compliant_resources"], 0)

    def test_empty_resource_group_reports_zero_not_error(self):
        az = unittest.mock.Mock()
        az.run_json.return_value = []
        result = governance.resource_group_inventory(az, "rg-empty")
        self.assertEqual(result["total_resources"], 0)
        self.assertEqual(result["tag_compliant_resources"], 0)


class BudgetAndActionGroupExistenceTests(unittest.TestCase):
    def test_budget_existence_reports_count_only(self):
        az = unittest.mock.Mock()
        az.run_json.return_value = [{"name": "b1", "amount": 500}]
        result = governance.budget_existence(az, "rg-example")
        self.assertEqual(result, {"resource_group": "rg-example", "budget_count": 1})
        self.assertNotIn("amount", result)

    def test_no_budgets_reports_zero(self):
        az = unittest.mock.Mock()
        az.run_json.return_value = []
        self.assertEqual(
            governance.budget_existence(az, "rg-example"),
            {"resource_group": "rg-example", "budget_count": 0},
        )

    def test_action_group_existence_reports_count_only(self):
        az = unittest.mock.Mock()
        az.run_json.return_value = [{"name": "ag1"}, {"name": "ag2"}]
        self.assertEqual(
            governance.action_group_existence(az, "rg-example"),
            {"resource_group": "rg-example", "action_group_count": 2},
        )


class TagCompliancePercentTests(unittest.TestCase):
    def test_computes_rounded_percentage(self):
        evidence = {
            "resource_groups": [
                {"total_resources": 3, "tag_compliant_resources": 1},
                {"total_resources": 3, "tag_compliant_resources": 2},
            ]
        }
        self.assertEqual(governance.tag_compliance_percent(evidence), 50.0)

    def test_no_resources_is_not_a_fabricated_percentage(self):
        evidence = {"resource_groups": [{"total_resources": 0, "tag_compliant_resources": 0}]}
        self.assertIsNone(governance.tag_compliance_percent(evidence))


class CollectTests(unittest.TestCase):
    def test_collect_aggregates_all_resource_groups(self):
        az = unittest.mock.Mock()

        def run_json(args):
            if args[:2] == ["resource", "list"]:
                return [resource(tags=COMPLIANT_TAGS)]
            if args[:2] == ["consumption", "budget"]:
                return [{"name": "b1", "amount": 999}]
            if args[:2] == ["monitor", "action-group"]:
                return []
            raise AssertionError(f"Unexpected az invocation: {args}")

        az.run_json.side_effect = run_json
        evidence = governance.collect(az, ["rg-a", "rg-b"], "2026-09-25T00:00:00Z")
        self.assertEqual(evidence["schema_version"], governance.SCHEMA_VERSION)
        self.assertEqual(len(evidence["resource_groups"]), 2)
        self.assertEqual(evidence["budgets"][0]["budget_count"], 1)
        self.assertEqual(evidence["action_groups"][0]["action_group_count"], 0)
        self.assertEqual(evidence["required_tags"], list(governance.REQUIRED_TAGS))


class RenderAndReportTests(unittest.TestCase):
    def evidence(self):
        return {
            "schema_version": 1,
            "collected_at": "2026-09-25T00:00:00Z",
            "required_tags": list(governance.REQUIRED_TAGS),
            "resource_groups": [
                {"resource_group": "rg-a", "total_resources": 2,
                 "tag_compliant_resources": 1, "resource_types": {}},
            ],
            "budgets": [{"resource_group": "rg-a", "budget_count": 1}],
            "action_groups": [{"resource_group": "rg-a", "action_group_count": 0}],
        }

    def test_build_report_excludes_resource_type_breakdown_by_default_fields(self):
        report = governance.build_report(self.evidence())
        self.assertEqual(report["tag_compliance_percent"], 50.0)
        self.assertEqual(report["schema_version"], 1)

    def test_render_contains_no_dollar_signs(self):
        report = governance.build_report(self.evidence())
        markdown = governance.render(report)
        self.assertNotIn("$", markdown)
        self.assertNotIn("500", markdown)
        self.assertIn("50.0%", markdown)
        self.assertIn("rg-a", markdown)

    def test_render_reports_na_when_no_resources_observed(self):
        evidence = self.evidence()
        evidence["resource_groups"] = [
            {"resource_group": "rg-empty", "total_resources": 0,
             "tag_compliant_resources": 0, "resource_types": {}},
        ]
        report = governance.build_report(evidence)
        markdown = governance.render(report)
        self.assertIn("N/A", markdown)


if __name__ == "__main__":
    unittest.main()
