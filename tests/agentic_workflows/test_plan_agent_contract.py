from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "plan-agent.md"


def output_block(frontmatter, name):
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(?P<body>(?:^    .*\n)+)",
        frontmatter,
    )
    if not match:
        raise AssertionError(f"Missing safe output: {name}")
    return match.group("body")


class PlanAgentWorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.frontmatter = self.text.split("---", 2)[1]

    def test_agent_permissions_are_read_only(self):
        match = re.search(
            r"(?m)^permissions:\n(?P<body>(?:  [a-z-]+: [a-z]+\n)+)",
            self.frontmatter,
        )
        self.assertIsNotNone(match)
        permissions = dict(re.findall(
            r"(?m)^  ([a-z-]+): ([a-z]+)$", match.group("body")
        ))
        self.assertEqual(permissions, {"contents": "read", "issues": "read"})
        self.assertNotIn("write", permissions.values())

    def test_label_outputs_are_exactly_scoped_and_trigger_only(self):
        add_labels = output_block(self.frontmatter, "add-labels")
        self.assertRegex(add_labels, r"(?m)^    allowed: \[stage:plan-ready\]$")
        self.assertRegex(add_labels, r"(?m)^    required-labels: \[stage:spec-approved\]$")
        self.assertRegex(add_labels, r"(?m)^    max: 1$")
        self.assertRegex(add_labels, r"(?m)^    target: triggering$")

        remove_labels = output_block(self.frontmatter, "remove-labels")
        self.assertRegex(remove_labels, r"(?m)^    allowed: \[stage:spec-approved\]$")
        self.assertRegex(remove_labels, r"(?m)^    required-labels: \[stage:spec-approved\]$")
        self.assertRegex(remove_labels, r"(?m)^    max: 1$")
        self.assertRegex(remove_labels, r"(?m)^    target: triggering$")

    def test_only_comment_and_scoped_label_safe_outputs_are_configured(self):
        safe_outputs = self.frontmatter.split("safe-outputs:\n", 1)[1]
        names = re.findall(r"(?m)^  ([a-z-]+):$", safe_outputs)
        self.assertEqual(names, ["add-comment", "add-labels", "remove-labels"])
        comment = output_block(self.frontmatter, "add-comment")
        self.assertRegex(comment, r"(?m)^    max: 1$")
        self.assertRegex(comment, r"(?m)^    target: triggering$")

    def test_workflow_is_label_triggered_and_human_approval_is_explicit(self):
        self.assertIn("types: [labeled]", self.frontmatter)
        self.assertIn("engine: copilot", self.frontmatter)
        self.assertIn("toolsets: [repos, issues]", self.frontmatter)
        self.assertIn("newly added label is", self.text)
        self.assertIn("exactly one", self.text)
        self.assertIn("gh-aw-workflow-call-id: .../spec-agent", self.text)
        self.assertIn("untrusted", self.text)
        self.assertIn("stage:plan-approved", self.text)
        self.assertIn("stage:needs-spec", self.text)
        self.assertIn("stage:plan-ready", self.text)

    def test_plan_covers_required_review_sections(self):
        for section in (
            "### Implementation approach",
            "### Ordered task breakdown",
            "### Dependencies and sequence",
            "### Design impact",
            "### Risks and rollback considerations",
            "### Complexity estimate",
            "### Resolved and open questions",
        ):
            with self.subTest(section=section):
                self.assertIn(section, self.text)


if __name__ == "__main__":
    unittest.main()
