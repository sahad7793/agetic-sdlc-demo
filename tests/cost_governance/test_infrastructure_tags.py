from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"
TEMPLATES = (
    "environment.bicep",
    "shared.bicep",
    "observability.bicep",
    "workbook.bicep",
)
UNTAGGABLE_RESOURCES = {
    "stagingAcrPush": "Microsoft.Authorization/roleAssignments",
    "allowAzureServices": "Microsoft.Sql/servers/firewallRules",
}
REQUIRED_TAGS = {
    "application": "'taskmanagement'",
    "costCenter": "costCenter",
    "owner": "owner",
}


def resource_blocks(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*resource (\w+) '([^']+)' = \{", line)
        if not match:
            continue

        depth = 0
        body = []
        for body_line in lines[index:]:
            depth += body_line.count("{") - body_line.count("}")
            body.append(body_line)
            if depth == 0:
                break
        yield match.group(1), match.group(2).split("@", 1)[0], "\n".join(body)


class InfrastructureTagTests(unittest.TestCase):
    def test_every_taggable_resource_uses_the_enforced_tag_bundle(self):
        discovered_untaggable = {}
        for template in TEMPLATES:
            for name, resource_type, body in resource_blocks(INFRA / template):
                if name in UNTAGGABLE_RESOURCES:
                    discovered_untaggable[name] = resource_type
                    continue
                with self.subTest(template=template, resource=name):
                    self.assertRegex(body, r"\btags:\s*(?:resourceTags|union\(resourceTags,)")

        self.assertEqual(discovered_untaggable, UNTAGGABLE_RESOURCES)

    def test_each_template_enforces_the_report_required_tags(self):
        for template in TEMPLATES:
            text = (INFRA / template).read_text(encoding="utf-8")
            with self.subTest(template=template):
                self.assertIn("var resourceTags = union(tags, {", text)
                for key, value in REQUIRED_TAGS.items():
                    self.assertRegex(
                        text,
                        rf"\b{key}:\s*{re.escape(value)}(?=\s|$)",
                    )
                environment_value = "'shared'" if template == "shared.bicep" or template == "workbook.bicep" else "environmentName"
                self.assertRegex(
                    text,
                    rf"\benvironment:\s*{re.escape(environment_value)}(?=\s|$)",
                )
                self.assertRegex(text, r"param costCenter string\s")
                self.assertRegex(text, r"param owner string\s")

    def test_bootstrap_resource_groups_receive_all_required_tags(self):
        script = (ROOT / "scripts" / "provision-infrastructure.sh").read_text(encoding="utf-8")
        for environment in ("shared", "staging", "production"):
            with self.subTest(environment=environment):
                self.assertRegex(
                    script,
                    rf"--tags application=taskmanagement environment={environment} "
                    r'costCenter="\$cost_center_tag" owner="\$owner_tag"',
                )


if __name__ == "__main__":
    unittest.main()
