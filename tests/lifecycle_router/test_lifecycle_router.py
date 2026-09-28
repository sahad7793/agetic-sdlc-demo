import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "lifecycle_router.py"
SPEC = importlib.util.spec_from_file_location("lifecycle_router", SCRIPT)
lifecycle_router = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lifecycle_router
SPEC.loader.exec_module(lifecycle_router)
STAGES = tuple(label["name"] for label in lifecycle_router.load_label_definitions())


class TransitionTests(unittest.TestCase):
    def test_initial_transition_only_allows_needs_spec(self):
        transition = lifecycle_router.decide_transition(
            [], "stage:needs-spec", "maintainer", None, STAGES
        )
        self.assertTrue(transition.valid)
        self.assertEqual(lifecycle_router.allowed_next_stages(None, STAGES),
                         ("stage:needs-spec",))

    def test_forward_transition_and_return_to_intake_are_allowed(self):
        for current, requested in (
            ("stage:needs-spec", "stage:spec-ready"),
            ("stage:spec-ready", "stage:spec-approved"),
            ("stage:spec-approved", "stage:plan-ready"),
            ("stage:plan-ready", "stage:plan-approved"),
            ("stage:plan-approved", "stage:in-progress"),
            ("stage:in-progress", "stage:in-review"),
            ("stage:in-review", "stage:done"),
            ("stage:in-review", "stage:needs-spec"),
        ):
            with self.subTest(current=current, requested=requested):
                transition = lifecycle_router.decide_transition(
                    [current], requested, "maintainer", "write", STAGES
                )
                self.assertTrue(transition.valid)

    def test_skipped_or_repeated_stages_are_rejected_with_allowed_next_stages(self):
        transition = lifecycle_router.decide_transition(
            ["stage:needs-spec"], "stage:plan-ready", "maintainer", "write", STAGES
        )
        self.assertFalse(transition.valid)
        self.assertEqual(transition.allowed_stages, ("stage:spec-ready",))
        repeated = lifecycle_router.decide_transition(
            ["stage:needs-spec"], "stage:needs-spec", "maintainer", "write", STAGES
        )
        self.assertFalse(repeated.valid)


class ApprovalGateTests(unittest.TestCase):
    def test_approval_stages_require_human_write_or_higher_permission(self):
        preceding_stages = {
            "stage:spec-approved": "stage:spec-ready",
            "stage:plan-approved": "stage:plan-ready",
        }
        for stage, current in preceding_stages.items():
            with self.subTest(stage=stage):
                for permission in ("write", "maintain", "admin"):
                    self.assertTrue(lifecycle_router.decide_transition(
                        [current], stage, "human", permission, STAGES
                    ).valid)
                for permission in ("read", "triage", None):
                    transition = lifecycle_router.decide_transition(
                        [current], stage, "human", permission, STAGES
                    )
                    self.assertEqual(
                        transition.reason, "approval-without-write-permission"
                    )

    def test_approval_by_bot_is_rejected_even_with_write_permission(self):
        transition = lifecycle_router.decide_transition(
            ["stage:plan-ready"],
            "stage:plan-approved",
            "maintainer[bot]",
            "admin",
            STAGES,
        )
        self.assertFalse(transition.valid)
        self.assertEqual(transition.reason, "approval-by-bot")


class BotDetectionTests(unittest.TestCase):
    def test_detects_github_and_copilot_bots_case_insensitively(self):
        for actor in (
            "service[bot]", "github-actions", "GitHub-Actions[bot]",
            "Copilot", "copilot[bot]", "copilot-swe-agent[bot]",
        ):
            with self.subTest(actor=actor):
                self.assertTrue(lifecycle_router.is_bot_actor(actor))

    def test_does_not_mistake_human_logins_for_bots(self):
        self.assertFalse(lifecycle_router.is_bot_actor("maintainer"))
        self.assertFalse(lifecycle_router.is_bot_actor("copilot-fan"))


class IssueFixtureTests(unittest.TestCase):
    def test_fixture_contains_a_single_added_stage_label(self):
        fixture = ROOT / "tests" / "lifecycle_router" / "fixtures" / "initial_stage_event.json"
        event = json.loads(fixture.read_text(encoding="utf-8"))
        current_stages = lifecycle_router._stage_labels(event["issue"]["labels"])
        self.assertEqual(current_stages, ["stage:needs-spec"])
        self.assertEqual(event["label"]["name"], "stage:needs-spec")


class RoutingInvariantTests(unittest.TestCase):
    def test_non_lifecycle_labels_are_ignored(self):
        api = Mock()
        event = {
            "issue": {"number": 123},
            "label": {"name": "bug"},
            "sender": {"login": "human"},
        }
        result = lifecycle_router.route_issue(api, event, lifecycle_router.load_label_definitions())
        self.assertEqual(result, "ignored")
        api.issue.assert_not_called()
        api.remove_issue_label.assert_not_called()
        api.comment.assert_not_called()

    def test_valid_transition_removes_other_stage_labels_and_comments_owner(self):
        api = Mock()
        api.issue.return_value = {
            "labels": [{"name": "stage:needs-spec"}, {"name": "stage:spec-ready"}]
        }
        event = {
            "issue": {"number": 123},
            "label": {"name": "stage:spec-ready"},
            "sender": {"login": "human"},
        }
        result = lifecycle_router.route_issue(api, event, lifecycle_router.load_label_definitions())
        self.assertEqual(result, "accepted")
        api.remove_issue_label.assert_called_once_with(123, "stage:needs-spec")
        api.comment.assert_called_once()
        self.assertIn("maintainer (human approval", api.comment.call_args.args[1])

    def test_approval_gate_checks_live_collaborator_permission(self):
        api = Mock()
        api.issue.return_value = {
            "labels": [{"name": "stage:spec-ready"}, {"name": "stage:spec-approved"}]
        }
        api.collaborator_permission.return_value = "triage"
        event = {
            "issue": {"number": 123},
            "label": {"name": "stage:spec-approved"},
            "sender": {"login": "human"},
        }
        result = lifecycle_router.route_issue(api, event, lifecycle_router.load_label_definitions())
        self.assertEqual(result, "rejected")
        api.collaborator_permission.assert_called_once_with("human")
        api.remove_issue_label.assert_called_once_with(123, "stage:spec-approved")
        self.assertIn("requires repository write", api.comment.call_args.args[1])

    def test_rejected_transition_removes_only_the_attempted_label(self):
        api = Mock()
        api.issue.return_value = {
            "labels": [{"name": "stage:needs-spec"}, {"name": "stage:plan-ready"}]
        }
        event = {
            "issue": {"number": 123},
            "label": {"name": "stage:plan-ready"},
            "sender": {"login": "human"},
        }
        result = lifecycle_router.route_issue(api, event, lifecycle_router.load_label_definitions())
        self.assertEqual(result, "rejected")
        api.remove_issue_label.assert_called_once_with(123, "stage:plan-ready")
        self.assertIn("stage:spec-ready", api.comment.call_args.args[1])

    def test_multiple_existing_stage_labels_fail_closed(self):
        api = Mock()
        api.issue.return_value = {
            "labels": [
                {"name": "stage:needs-spec"},
                {"name": "stage:spec-ready"},
                {"name": "stage:spec-approved"},
            ]
        }
        event = {
            "issue": {"number": 123},
            "label": {"name": "stage:spec-approved"},
            "sender": {"login": "human"},
        }
        lifecycle_router.route_issue(api, event, lifecycle_router.load_label_definitions())
        api.remove_issue_label.assert_called_once_with(123, "stage:spec-approved")
        self.assertIn("one `stage:*` label", api.comment.call_args.args[1])

    def test_stale_label_event_does_not_mutate_issue(self):
        api = Mock()
        api.issue.return_value = {"labels": [{"name": "stage:needs-spec"}]}
        event = {
            "issue": {"number": 123},
            "label": {"name": "stage:spec-ready"},
            "sender": {"login": "human"},
        }
        result = lifecycle_router.route_issue(api, event, lifecycle_router.load_label_definitions())
        self.assertEqual(result, "stale-event")
        api.remove_issue_label.assert_not_called()
        api.comment.assert_not_called()


class WorkflowContractTests(unittest.TestCase):
    def test_router_is_issue_label_only_with_minimal_permissions_and_pinned_checkout(self):
        workflow = (ROOT / ".github" / "workflows" / "lifecycle-router.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("issues:\n    types: [labeled]", workflow)
        self.assertIn("permissions:\n  contents: read\n  issues: write", workflow)
        self.assertIn(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            workflow,
        )
        self.assertIn("concurrency:", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("pull_request:", workflow)

    def test_label_sync_workflow_is_manual_only(self):
        workflow = (ROOT / ".github" / "workflows" / "lifecycle-label-sync.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("on:\n  workflow_dispatch:", workflow)
        self.assertNotIn("on:\n  issues:", workflow)
        self.assertIn("issues: write", workflow)


class LabelSyncTests(unittest.TestCase):
    def test_sync_updates_existing_definitions_and_creates_missing_labels(self):
        api = Mock()
        api.labels.return_value = [{"name": "stage:needs-spec"}]
        definitions = lifecycle_router.load_label_definitions()
        lifecycle_router.sync_labels(api, definitions)
        api.update_label.assert_called_once_with(definitions[0])
        api.create_label.assert_any_call({
            "name": "stage:spec-ready",
            "color": definitions[1]["color"],
            "description": definitions[1]["description"],
        })


if __name__ == "__main__":
    unittest.main()
