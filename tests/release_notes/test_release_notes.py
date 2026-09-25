import importlib.util
import html
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts/release_notes.py")
release_notes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_notes)


def generate_env(**overrides):
    env = {
        "GH_REPOSITORY": "sahad7793/agetic-sdlc-demo",
        "RUN_URL": "https://example.test/run/1",
        "ACTOR": "octocat",
        "INPUT_TAG_NAME": "v0.1.0",
        "INPUT_PREVIOUS_TAG_NAME": "",
        "INPUT_TARGET_COMMITISH": "main",
    }
    env.update(overrides)
    return env


class RequireRefTests(unittest.TestCase):
    def test_accepts_typical_tag(self):
        release_notes.require_ref("v0.1.0", "tag_name")  # does not raise

    def test_accepts_branch_with_slash(self):
        release_notes.require_ref("release/1.0", "target_commitish")

    def test_rejects_blank(self):
        with self.assertRaisesRegex(ValueError, "tag_name must be"):
            release_notes.require_ref("", "tag_name")

    def test_rejects_leading_dash(self):
        with self.assertRaisesRegex(ValueError, "tag_name must be"):
            release_notes.require_ref("-x", "tag_name")

    def test_rejects_whitespace(self):
        with self.assertRaisesRegex(ValueError, "tag_name must be"):
            release_notes.require_ref("v0.1.0 rc", "tag_name")

    def test_rejects_oversized(self):
        with self.assertRaisesRegex(ValueError, "tag_name must be"):
            release_notes.require_ref("v" + "0" * 200, "tag_name")


class ReadGenerateInputsTests(unittest.TestCase):
    def test_reads_and_defaults_target_commitish(self):
        env = generate_env(INPUT_TARGET_COMMITISH="")
        with patch.dict(os.environ, env, clear=False):
            inputs = release_notes.read_generate_inputs()
        self.assertEqual(inputs, {"tag_name": "v0.1.0", "previous_tag_name": "",
                                   "target_commitish": "main"})

    def test_accepts_blank_previous_tag_name(self):
        with patch.dict(os.environ, generate_env(), clear=False):
            inputs = release_notes.read_generate_inputs()
        self.assertEqual(inputs["previous_tag_name"], "")

    def test_rejects_invalid_previous_tag_name(self):
        env = generate_env(INPUT_PREVIOUS_TAG_NAME="bad ref")
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(ValueError, "previous_tag_name must be"):
                release_notes.read_generate_inputs()


class GenerateTests(unittest.TestCase):
    def test_generate_writes_notes_and_audit_without_previous_tag(self):
        calls = []
        response = {"name": "v0.1.0", "body": "## Other changes\n\n* Add widget by @octocat\n"}

        def fake_command(*args, **kwargs):
            calls.append((args, kwargs.get("input_text")))
            if args[:3] == ("gh", "api", "--method"):
                return json.dumps(response)
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            with patch.dict(os.environ, generate_env(), clear=False), \
                    patch.object(release_notes, "command", side_effect=fake_command):
                release_notes.generate(audit_path)

            audit = json.loads(audit_path.read_text())
            notes = release_notes.notes_path_for(audit_path).read_text()

        self.assertTrue(audit["outcome"].startswith("Succeeded"))
        self.assertIn("no tag or release created", audit["outcome"])
        self.assertEqual(audit["generated_name"], "v0.1.0")
        self.assertEqual(notes, response["body"])

        api_calls = [call for call, _ in calls if call[:3] == ("gh", "api", "--method")]
        self.assertEqual(len(api_calls), 1)
        body_sent = json.loads([body for _, body in calls if body][0])
        self.assertEqual(body_sent, {"tag_name": "v0.1.0", "target_commitish": "main"})
        self.assertNotIn("previous_tag_name", body_sent)

    def test_generate_includes_previous_tag_name_when_given(self):
        response = {"name": "v0.2.0", "body": "notes"}

        def fake_command(*args, **kwargs):
            if args[:3] == ("gh", "api", "--method"):
                return json.dumps(response)
            return ""

        env = generate_env(INPUT_TAG_NAME="v0.2.0", INPUT_PREVIOUS_TAG_NAME="v0.1.0")
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            with patch.dict(os.environ, env, clear=False), \
                    patch.object(release_notes, "command", side_effect=fake_command) as mocked:
                release_notes.generate(audit_path)
            calls = mocked.call_args_list
        body_sent = json.loads(calls[0].kwargs["input_text"])
        self.assertEqual(body_sent["previous_tag_name"], "v0.1.0")

    def test_generate_does_not_drop_unlabeled_or_uncategorized_changes(self):
        """The catch-all category lives in .github/release.yml; this script must not
        special-case or filter the body GitHub returns, whatever it contains."""
        response = {"name": "v0.1.0",
                    "body": "## Other changes\n\n* Untagged fix by @someone\n"}

        def fake_command(*args, **kwargs):
            if args[:3] == ("gh", "api", "--method"):
                return json.dumps(response)
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            with patch.dict(os.environ, generate_env(), clear=False), \
                    patch.object(release_notes, "command", side_effect=fake_command):
                release_notes.generate(audit_path)
            notes = release_notes.notes_path_for(audit_path).read_text()
        self.assertIn("Other changes", notes)
        self.assertIn("Untagged fix", notes)

    def test_generate_records_failure_without_raising_out_of_audit(self):
        env = generate_env(INPUT_TAG_NAME="")
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaises(ValueError):
                    release_notes.generate(audit_path)
            audit = json.loads(audit_path.read_text())
        self.assertTrue(audit["outcome"].startswith("Failed:"))
        self.assertIn("tag_name must be", audit["error"])

    def test_generate_propagates_gh_api_failure(self):
        def fake_command(*args, **kwargs):
            if args[:3] == ("gh", "api", "--method"):
                raise ValueError("gh failed (1): tag not found")
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            with patch.dict(os.environ, generate_env(), clear=False), \
                    patch.object(release_notes, "command", side_effect=fake_command):
                with self.assertRaises(ValueError):
                    release_notes.generate(audit_path)
            audit = json.loads(audit_path.read_text())
        self.assertTrue(audit["outcome"].startswith("Failed:"))
        self.assertIn("tag not found", audit["error"])


class SummaryTests(unittest.TestCase):
    def test_summary_includes_draft_banner_and_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            release_notes.save_audit(audit_path, {
                "outcome": "Succeeded: release notes generated (draft content only; no tag "
                           "or release created)",
                "tag_name": "v0.1.0", "previous_tag_name": "", "target_commitish": "main",
                "generated_name": "v0.1.0", "actor": "octocat",
                "generated_at": "2026-09-25 12:00:00 UTC",
            })
            release_notes.notes_path_for(audit_path).write_text("## Other changes\n\n* x\n")
            summary_path = Path(tmp) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}, clear=False):
                release_notes.summary(audit_path)
            text = summary_path.read_text()
        self.assertIn("DRAFT", text)
        self.assertIn("nothing published, no tag created", text)
        self.assertIn(html.escape("Succeeded: release notes generated"), text)
        self.assertIn("Other changes", text)


class ReleaseExistsTests(unittest.TestCase):
    def test_true_when_gh_release_view_succeeds(self):
        with patch.object(release_notes, "command", return_value="{}"):
            self.assertTrue(release_notes.release_exists("v0.1.0"))

    def test_false_when_gh_release_view_fails(self):
        def fake_command(*args, **kwargs):
            raise ValueError("gh failed (1): release not found")

        with patch.object(release_notes, "command", side_effect=fake_command):
            self.assertFalse(release_notes.release_exists("v0.1.0"))


class DraftTests(unittest.TestCase):
    def _succeeded_audit(self, tmp, **overrides):
        audit_path = Path(tmp) / "audit.json"
        audit = {
            "outcome": "Succeeded: release notes generated (draft content only; no tag "
                       "or release created)",
            "tag_name": "v0.1.0", "target_commitish": "main", "generated_name": "v0.1.0",
        }
        audit.update(overrides)
        release_notes.save_audit(audit_path, audit)
        release_notes.notes_path_for(audit_path).write_text("notes\n")
        return audit_path

    def test_creates_when_release_missing_and_always_passes_draft_flag(self):
        calls = []

        def fake_command(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("gh", "release", "view") and args[-1] == "tagName":
                raise ValueError("gh failed (1): release not found")
            if args[:3] == ("gh", "release", "view"):
                return json.dumps({"url": "https://github.com/o/r/releases/tag/v0.1.0",
                                    "isDraft": True, "tagName": "v0.1.0"})
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = self._succeeded_audit(tmp)
            with patch.object(release_notes, "command", side_effect=fake_command):
                release_notes.draft(audit_path)
            audit = json.loads(audit_path.read_text())

        create_calls = [call for call in calls if call[:3] == ("gh", "release", "create")]
        self.assertEqual(len(create_calls), 1)
        self.assertIn("--draft", create_calls[0])
        self.assertIn("--target", create_calls[0])
        self.assertTrue(audit["draft_outcome"].startswith("Succeeded"))
        self.assertEqual(audit["draft_url"], "https://github.com/o/r/releases/tag/v0.1.0")

    def test_edits_existing_draft_without_target(self):
        calls = []

        def fake_command(*args, **kwargs):
            calls.append(args)
            if args[:3] == ("gh", "release", "view"):
                return json.dumps({"url": "https://github.com/o/r/releases/tag/v0.1.0",
                                    "isDraft": True, "tagName": "v0.1.0"})
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = self._succeeded_audit(tmp)
            with patch.object(release_notes, "command", side_effect=fake_command):
                release_notes.draft(audit_path)

        edit_calls = [call for call in calls if call[:3] == ("gh", "release", "edit")]
        self.assertEqual(len(edit_calls), 1)
        self.assertIn("--draft", edit_calls[0])
        self.assertNotIn("--target", edit_calls[0])

    def test_refuses_when_saved_release_is_not_draft(self):
        def fake_command(*args, **kwargs):
            if args[:3] == ("gh", "release", "view") and args[-1] == "tagName":
                raise ValueError("gh failed (1): release not found")
            if args[:3] == ("gh", "release", "view"):
                return json.dumps({"url": "https://github.com/o/r/releases/tag/v0.1.0",
                                    "isDraft": False, "tagName": "v0.1.0"})
            return ""

        with tempfile.TemporaryDirectory() as tmp:
            audit_path = self._succeeded_audit(tmp)
            with patch.object(release_notes, "command", side_effect=fake_command):
                with self.assertRaises(ValueError):
                    release_notes.draft(audit_path)
            audit = json.loads(audit_path.read_text())
        self.assertTrue(audit["draft_outcome"].startswith("Failed"))

    def test_refuses_when_generation_did_not_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            release_notes.save_audit(audit_path, {"outcome": "Failed: something"})
            with self.assertRaisesRegex(ValueError, "Cannot draft a release"):
                release_notes.draft(audit_path)


class DraftSummaryTests(unittest.TestCase):
    def test_reports_draft_url_and_publish_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            release_notes.save_audit(audit_path, {
                "draft_outcome": "Succeeded: draft release saved",
                "draft_url": "https://github.com/o/r/releases/tag/v0.1.0",
            })
            summary_path = Path(tmp) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}, clear=False):
                release_notes.draft_summary(audit_path)
            text = summary_path.read_text()
        self.assertIn("https://github.com/o/r/releases/tag/v0.1.0", text)
        self.assertIn("Publish release", text)

    def test_reports_skipped_when_no_draft_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            summary_path = Path(tmp) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}, clear=False):
                release_notes.draft_summary(audit_path)
            text = summary_path.read_text()
        self.assertIn("skipped", text)


if __name__ == "__main__":
    unittest.main()
