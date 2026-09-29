from pathlib import Path
import re
import unittest


WORKFLOW = (Path(__file__).resolve().parents[2]
            / ".github" / "workflows" / "ci.yml")


class CiWorkflowPermissionTests(unittest.TestCase):
    def test_ci_grants_only_required_permissions(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(
            r"(?m)^permissions:\n(?P<body>(?:  [a-z-]+: [a-z]+\n)+)",
            text,
        )
        self.assertIsNotNone(match)
        permissions = dict(re.findall(
            r"(?m)^  ([a-z-]+): ([a-z]+)$", match.group("body")
        ))
        self.assertEqual(
            permissions,
            {
                "actions": "read",
                "contents": "read",
                "security-events": "write",
            },
        )


if __name__ == "__main__":
    unittest.main()
