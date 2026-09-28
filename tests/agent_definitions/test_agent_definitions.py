from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = ROOT / ".github" / "agents"
EXPECTED_AGENTS = {
    "architect",
    "ops-investigator",
    "product-analyst",
    "release-manager",
    "reviewer",
    "security-reviewer",
    "test-engineer",
}
NAME_PATTERN = re.compile(r"^name:\s*([a-z][a-z0-9-]*)\s*$")
DESCRIPTION_PATTERN = re.compile(r'^description:\s*(?:"[^"]+"|[^#\s].*)\s*$')
TOOL_PATTERN = re.compile(r"^  - (read|search|edit)$")


class AgentDefinitionTests(unittest.TestCase):
    def test_all_expected_agent_definitions_have_valid_frontmatter_and_boundaries(self):
        agent_files = sorted(AGENTS_DIR.glob("*.agent.md"))
        self.assertEqual({path.stem.removesuffix(".agent") for path in agent_files},
                         EXPECTED_AGENTS)

        for path in agent_files:
            with self.subTest(agent=path.name):
                content = path.read_text(encoding="utf-8")
                lines = content.splitlines()
                self.assertGreaterEqual(len(lines), 5)
                self.assertEqual(lines[0], "---")
                self.assertIn("---", lines[1:], "frontmatter must have a closing delimiter")
                closing_index = lines.index("---", 1)
                frontmatter = lines[1:closing_index]
                self.assertTrue(all(
                    line in ("tools:",) or line.startswith("name:")
                    or line.startswith("description:") or TOOL_PATTERN.fullmatch(line)
                    for line in frontmatter
                ), "frontmatter contains an unsupported or malformed field")
                names = [line for line in frontmatter if line.startswith("name:")]
                descriptions = [line for line in frontmatter if line.startswith("description:")]
                self.assertEqual(len(names), 1)
                self.assertRegex(names[0], NAME_PATTERN)
                self.assertEqual(len(descriptions), 1)
                self.assertRegex(descriptions[0], DESCRIPTION_PATTERN)

                tools_index = next(
                    (index for index, line in enumerate(frontmatter) if line == "tools:"),
                    None,
                )
                if tools_index is not None:
                    tool_lines = [
                        line for line in frontmatter[tools_index + 1:]
                        if line.startswith("  - ")
                    ]
                    self.assertTrue(tool_lines)
                    self.assertTrue(all(TOOL_PATTERN.fullmatch(line) for line in tool_lines))

                body = "\n".join(lines[closing_index + 1:])
                self.assertRegex(body, r"(?im)^## Forbidden actions\s*$")
                self.assertRegex(body, r"(?im)^## Lifecycle stage\s*$")
                self.assertRegex(body, r"(?im)^## Inputs\s*$")
                self.assertRegex(body, r"(?im)^## Outputs\s*$")
                self.assertRegex(body, r"(?im)^## Human handoff\s*$")


if __name__ == "__main__":
    unittest.main()
