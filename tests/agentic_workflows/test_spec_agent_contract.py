from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "spec-agent.md"
LOCK = ROOT / ".github" / "workflows" / "spec-agent.lock.yml"


def output_block(frontmatter, name):
    match = re.search(
        rf"(?m)^  {re.escape(name)}:\n(?P<body>(?:^    .*\n)+)",
        frontmatter,
    )
    if not match:
        raise AssertionError(f"Missing safe output: {name}")
    return match.group("body")


class SpecAgentWorkflowContractTests(unittest.TestCase):
    def setUp(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.frontmatter = text.split("---", 2)[1]

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
        self.assertRegex(add_labels, r"(?m)^    allowed: \[stage:spec-ready\]$")
        self.assertNotIn("required-labels:", add_labels)
        self.assertRegex(add_labels, r"(?m)^    max: 1$")
        self.assertRegex(add_labels, r"(?m)^    target: triggering$")

        remove_labels = output_block(self.frontmatter, "remove-labels")
        self.assertRegex(remove_labels, r"(?m)^    allowed: \[stage:needs-spec\]$")
        self.assertRegex(remove_labels, r"(?m)^    required-labels: \[stage:needs-spec\]$")
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
        self.assertIn("label_command:", self.frontmatter)
        self.assertIn("names: [stage:needs-spec]", self.frontmatter)
        self.assertIn("events: [issues]", self.frontmatter)
        self.assertIn("remove_label: false", self.frontmatter)
        self.assertIn("engine: copilot", self.frontmatter)
        self.assertIn("toolsets: [repos, issues]", self.frontmatter)
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("maintainer", text)
        self.assertIn("untrusted input", text)
        self.assertIn("Given", text)
        self.assertIn("When", text)
        self.assertIn("Then", text)
        self.assertIn("Do not mention labels, claim a", text)
        self.assertNotIn("End with this maintainer handoff", text)

    def test_approval_labels_are_not_writable(self):
        self.assertNotIn("stage:spec-approved", output_block(self.frontmatter, "add-labels"))
        self.assertNotIn("stage:plan-approved", output_block(self.frontmatter, "add-labels"))

    def test_compiled_activation_is_filtered_to_the_source_label(self):
        lock = LOCK.read_text(encoding="utf-8")
        self.assertRegex(
            lock,
            r"needs\.pre_activation\.outputs\.activated == 'true' && "
            r"\(github\.event_name == 'issues' && "
            r"github\.event\.label\.name == 'stage:needs-spec'\)",
        )
        self.assertRegex(lock, r"(?m)^  agent:\n    needs: activation\n")
        self.assertIn(
            "    if: needs.activation.outputs.daily_ai_credits_exceeded != 'true'",
            lock,
        )


if __name__ == "__main__":
    unittest.main()
