from pathlib import Path
import re
import unittest


WORKFLOW = (Path(__file__).resolve().parents[2]
            / ".github" / "workflows" / "dependency-governance-report.yml")


def permission_blocks(text):
    """Return {indent: {name: access}} for every `permissions:` mapping in the workflow."""
    blocks = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*)permissions:\s*$", line)
        if not match:
            continue
        indent = len(match.group(1))
        block = {}
        for entry in lines[index + 1:]:
            entry_match = re.match(r"^(\s*)([a-z-]+):\s*([a-z-]+)\s*(#.*)?$", entry)
            if not entry_match or len(entry_match.group(1)) <= indent:
                break
            block[entry_match.group(2)] = entry_match.group(3)
        blocks.append((indent, block))
    return blocks


class WorkflowPermissionTests(unittest.TestCase):
    def setUp(self):
        self.blocks = permission_blocks(WORKFLOW.read_text(encoding="utf-8"))

    def test_collect_job_can_read_dependabot_alerts(self):
        # security-events covers code scanning only; Dependabot alerts need vulnerability-alerts.
        job_blocks = [block for indent, block in self.blocks if indent > 0]
        self.assertEqual(len(job_blocks), 1)
        self.assertEqual(job_blocks[0], {
            "contents": "read",
            "security-events": "read",
            "vulnerability-alerts": "read",
            "pull-requests": "read",
        })

    def test_workflow_requests_no_write_permissions(self):
        self.assertTrue(self.blocks)
        for _, block in self.blocks:
            self.assertTrue(all(access in {"read", "none"} for access in block.values()), block)


if __name__ == "__main__":
    unittest.main()
