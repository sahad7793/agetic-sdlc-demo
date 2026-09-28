from pathlib import Path
import re
import unittest


WORKFLOW = (Path(__file__).resolve().parents[2]
            / ".github" / "workflows" / "sdlc-metrics.yml")


def permission_blocks(text):
    """Return {indent: {name: access}} for every `permissions:` mapping."""
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

    def test_jobs_request_exact_permissions(self):
        job_blocks = [block for indent, block in self.blocks if indent > 0]
        self.assertEqual(job_blocks, [
            {
                "contents": "read",
                "actions": "read",
                "issues": "read",
                "pull-requests": "read",
                "security-events": "read",
                "vulnerability-alerts": "read",
            },
            {
                "contents": "read",
                "issues": "write",
            },
        ])

    def test_collector_requests_no_write_permissions(self):
        job_blocks = [block for indent, block in self.blocks if indent > 0]
        self.assertTrue(job_blocks)
        self.assertTrue(all(access != "write" for access in job_blocks[0].values()))


if __name__ == "__main__":
    unittest.main()
