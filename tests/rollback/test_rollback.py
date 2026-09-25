import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("rollback", ROOT / "scripts/rollback.py")
rollback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rollback)
REGISTRY = "example.azurecr.io"
REPOSITORY = "example/repo"
APP = "task-api-stage"
PREFIX = f"{REGISTRY}/taskmanagement-api"
A, B, C = [f"{PREFIX}@sha256:{letter * 64}" for letter in "abc"]


def at(hour):
    return f"2026-09-25T{hour:02}:00:00Z"


def record(hour, image=B, success=True, environment="staging"):
    step = {"name": rollback.STEPS[environment], "started_at": at(hour),
            "completed_at": at(hour + 1), "status": "completed",
            "conclusion": "success" if success else "failure"}
    return {
        "job": {"id": hour, "name": rollback.JOBS[environment], "steps": [step],
                "conclusion": "success" if success else "failure"},
        "step": step, "run": hour, "attempt": 1,
        "start": rollback.timestamp(at(hour)), "end": rollback.timestamp(at(hour + 1)),
        "success": success, "image": image, "app": APP, "url": f"https://github.com/job/{hour}",
    }


def deploy_log(hour, image=B):
    lines = [
        "##[group]Run az containerapp registry set \\",
        'az containerapp update --image "$IMAGE"',
        "shell: /usr/bin/bash -e {0}", "env:",
        f"  ACR_LOGIN_SERVER: {REGISTRY}", f"  CONTAINER_APP_NAME: {APP}",
        f"  IMAGE: {image}", "##[endgroup]",
        "application output IMAGE: untrusted",
    ]
    return "\n".join(f"2026-09-25T{hour:02}:00:00.1234567Z {line}" for line in lines)


def build_log(hour, tag):
    lines = ["##[group]Run az acr login --name \"$ACR_NAME\"",
             'docker push "$ACR_LOGIN_SERVER/taskmanagement-api:$IMAGE_TAG"',
             "shell: /usr/bin/bash -e {0}", "env:",
             f"  ACR_LOGIN_SERVER: {REGISTRY}", f"  IMAGE_TAG: {tag}", "##[endgroup]"]
    return "\n".join(f"2026-09-25T{hour:02}:00:00.1234567Z {line}" for line in lines)


def state(image=B):
    return {"desired": image, "serving": image, "latest": "rev-2", "ready": "rev-2",
            "active": True, "healthy": True, "provisioning": "Succeeded",
            "fqdn": "example.azurecontainerapps.io", "traffic": []}


class EvidenceTests(unittest.TestCase):
    def previous(self, records, current=B):
        return rollback.previous_image(records, current, REPOSITORY, REGISTRY, APP)

    def test_previous_uses_failed_current_anchor_and_skips_same_image(self):
        target, source, anchor = self.previous([
            record(12, B, False), record(10, B), record(8, C, False), record(6, A)])
        self.assertEqual(target, A)
        self.assertEqual(source["run"], 6)
        self.assertEqual(anchor["run"], 12)

    def test_uses_image_anchor_not_latest_success(self):
        target, source, _ = self.previous([record(12, C), record(10, B), record(8, A)])
        self.assertEqual((target, source["run"]), (A, 8))

    def test_no_history_no_match_and_no_prior_fail_clearly(self):
        for rows, message in [([], "absent"), ([record(10, A)], "absent"),
                              ([record(10, B)], "No previous"),
                              ([record(10, B), record(8, B)], "No previous")]:
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, message):
                self.previous(rows)

    def test_overlap_does_not_guess_order(self):
        older = record(8, A)
        older["end"] = rollback.timestamp(at(11))
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.previous([record(10, B), older])
        oldest = record(6, C)
        oldest["end"] = rollback.timestamp(at(9))
        with self.assertRaisesRegex(ValueError, "ordering"):
            self.previous([record(10, B), record(8, A), oldest])
        with self.assertRaisesRegex(ValueError, "ordering"):
            self.previous([record(10, B), record(8, A), record(8, C)])

    def test_other_app_evidence_rejected(self):
        other = record(10, B)
        other["app"] = "other-app"
        with self.assertRaisesRegex(ValueError, "different Container App"):
            self.previous([other])

    def test_exact_runner_header_extracts_digest_ignores_application_output(self):
        item = record(10)
        del item["image"]
        with patch.object(rollback, "command", return_value=deploy_log(10)):
            self.assertEqual(rollback.image_evidence(item, REPOSITORY, REGISTRY), B)

    def test_missing_duplicate_out_of_step_or_foreign_headers_rejected(self):
        for log in ["IMAGE: " + A, deploy_log(10) + "\n" + deploy_log(10),
                    deploy_log(8), deploy_log(10).replace(REGISTRY, "other.azurecr.io"),
                    deploy_log(10).replace('--image "$IMAGE"', "--image latest"),
                    deploy_log(10).replace("  IMAGE:", "  WRONG:")]:
            item = record(10)
            del item["image"]
            with self.subTest(log=log), patch.object(rollback, "command", return_value=log):
                with self.assertRaises(ValueError):
                    rollback.image_evidence(item, REPOSITORY, REGISTRY)

    def test_log_download_failure_does_not_skip_to_older_candidate(self):
        missing = record(8, A)
        del missing["image"]
        with patch.object(rollback, "command", side_effect=ValueError("GitHub 410 expired")):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.previous([record(10, B), missing, record(6, C)])

    def test_pages_collect_all_and_reject_incomplete_or_duplicate_history(self):
        first = {"total_count": 101, "rows": [{"id": number} for number in range(100)]}
        last = {"total_count": 101, "rows": [{"id": 100}]}
        with patch.object(rollback, "api", side_effect=[first, last]) as api:
            self.assertEqual(len(rollback.pages("endpoint", "rows")), 101)
            self.assertEqual(api.call_args.args[0], "endpoint?per_page=100&page=2")
        for payload, message in [
            ({"total_count": 2, "rows": [{"id": 1}]}, "Incomplete"),
            ({"total_count": 2, "rows": [{"id": 1}, {"id": 1}]}, "Duplicate")]:
            with patch.object(rollback, "api", return_value=payload):
                with self.assertRaisesRegex(ValueError, message):
                    rollback.pages("endpoint", "rows")

    def test_history_is_attempt_specific_environment_scoped_and_deduplicated(self):
        run = {"id": 20, "event": "workflow_run", "head_branch": "main", "run_attempt": 2,
               "conclusion": None}  # Production waiting must not erase staging success.
        staging = record(10)["job"]
        production = record(12, C, environment="production")["job"]
        production["steps"][0].update(status="pending", conclusion=None, started_at=None)
        with patch.object(rollback, "pages", side_effect=[[run], [staging, production],
                                                        [staging, production]]) as pages:
            result = rollback.history(REPOSITORY, "staging")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["success"])
        self.assertEqual(result[0]["attempt"], 1)
        self.assertIn("/attempts/2/jobs", pages.call_args.args[0])
        with patch.object(rollback, "pages", side_effect=[[run], [staging, production],
                                                        [staging, production]]):
            self.assertEqual(rollback.history(REPOSITORY, "production"), [])

    def test_in_progress_deploy_blocks_resolution(self):
        run = {"id": 20, "event": "workflow_run", "head_branch": "main", "run_attempt": 1}
        job = record(10)["job"]
        job["steps"][0].update(status="in_progress", conclusion=None)
        with patch.object(rollback, "pages", side_effect=[[run], [job]]):
            with self.assertRaisesRegex(ValueError, "in progress"):
                rollback.history(REPOSITORY, "staging")

    def test_explicit_digest_needs_no_github_or_registry_permissions(self):
        with patch.object(rollback, "history") as history:
            self.assertEqual(rollback.explicit_image(A, REPOSITORY, REGISTRY), (A, None))
            self.assertEqual(rollback.explicit_image("sha256:" + "a" * 64, REPOSITORY, REGISTRY),
                             (A, None))
            history.assert_not_called()

    def test_invalid_digest_reference_or_shell_input_rejected(self):
        for value in ["", "$(touch /tmp/bad)", "--debug", "other.azurecr.io/api:tag",
                      A.replace(REGISTRY, "other.azurecr.io"), A[:-1], A + "\n", "sha256:123"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                rollback.explicit_image(value, REPOSITORY, REGISTRY)

    def tag_record(self, hour, image=A, tag="release"):
        item = record(hour, image)
        item["job"]["steps"].append({
            "name": rollback.BUILD_STEP, "conclusion": "success",
            "started_at": at(hour - 1), "completed_at": at(hour)})
        item["log"] = build_log(hour - 1, tag) + "\n" + deploy_log(hour, image)
        return item

    def test_tag_resolves_retained_mapping_not_current_mutable_registry_tag(self):
        item = self.tag_record(10)
        with patch.object(rollback, "history", return_value=[item]):
            self.assertEqual(rollback.explicit_image("release", REPOSITORY, REGISTRY), (A, item))
            self.assertEqual(rollback.explicit_image(PREFIX + ":release", REPOSITORY, REGISTRY),
                             (A, item))

    def test_missing_and_rebuilt_tags_fail_closed(self):
        for records, message in [([], "no retained"), ([self.tag_record(10, A),
                                                       self.tag_record(8, B)], "different digests")]:
            with patch.object(rollback, "history", return_value=records):
                with self.assertRaisesRegex(ValueError, message):
                    rollback.explicit_image("release", REPOSITORY, REGISTRY)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audit = Path(self.directory.name) / "audit.json"
        self.summary = Path(self.directory.name) / "summary.md"
        env = {"TARGET_ENVIRONMENT": "production", "TARGET_MODE": "explicit image",
               "TARGET_IMAGE": A, "ROLLBACK_REASON": "$(echo unsafe)\n</pre><script>bad</script>",
               "GITHUB_ACTOR": "operator", "GITHUB_TRIGGERING_ACTOR": "rerunner",
               "GITHUB_REPOSITORY": REPOSITORY, "ACR_LOGIN_SERVER": REGISTRY,
               "CONTAINER_APP_NAME": APP, "AZURE_RESOURCE_GROUP": "rg-test",
               "GITHUB_STEP_SUMMARY": str(self.summary)}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_success_changes_only_image_and_writes_escaped_audit(self):
        before = state()
        before["serving"] = C
        with patch.object(rollback, "live_state", return_value=before), \
                patch.object(rollback, "azure") as azure, patch.object(rollback, "check_health") as health:
            rollback.perform(self.audit)
        azure.assert_called_once_with("update", "--container-name", "taskmanagement-api",
                                      "--image", A, timeout=600)
        health.assert_called_once_with(A, before["fqdn"])
        audit = json.loads(self.audit.read_text())
        self.assertEqual((audit["from"], audit["serving_before"], audit["to"]), (B, C, A))
        self.assertEqual(audit["triggering_actor"], "rerunner")
        self.assertTrue(audit["outcome"].startswith("Succeeded"))
        rollback.summary(self.audit)
        self.assertIn("&lt;script&gt;", self.summary.read_text())
        self.assertNotIn("<script>", self.summary.read_text())

    def test_state_drift_and_noop_make_no_update(self):
        for requested, states, message in [(A, [state(), state(C)], "changed"),
                                            (B, [state()], "equals")]:
            with patch.dict(os.environ, TARGET_IMAGE=requested), \
                    patch.object(rollback, "live_state", side_effect=states), \
                    patch.object(rollback, "azure") as azure:
                with self.assertRaisesRegex(ValueError, message):
                    rollback.perform(self.audit)
                azure.assert_not_called()

    def test_no_prior_history_audits_failure_before_mutation(self):
        with patch.dict(os.environ, TARGET_MODE="previous successful deployment", TARGET_IMAGE=""), \
                patch.object(rollback, "live_state", return_value=state()), \
                patch.object(rollback, "history", return_value=[]), \
                patch.object(rollback, "azure") as azure:
            with self.assertRaisesRegex(ValueError, "absent"):
                rollback.perform(self.audit)
            azure.assert_not_called()
        self.assertIn("Failed", json.loads(self.audit.read_text())["outcome"])

    def test_azure_failure_or_health_failure_never_triggers_second_update(self):
        for failure in ("azure", "check_health"):
            with self.subTest(failure=failure), \
                    patch.object(rollback, "live_state", return_value=state()), \
                    patch.object(rollback, "azure") as azure, \
                    patch.object(rollback, "check_health") as health:
                (azure if failure == "azure" else health).side_effect = ValueError("service failure")
                with self.assertRaisesRegex(ValueError, "service failure"):
                    rollback.perform(self.audit)
                self.assertEqual(azure.call_count, 1)
                audit = json.loads(self.audit.read_text())
                self.assertIn("Failed", audit["outcome"])
                self.assertEqual(audit["error"], "service failure")

    def test_invalid_reason_and_environment_fail_before_azure(self):
        for env in [{"ROLLBACK_REASON": "  "}, {"ROLLBACK_REASON": "x" * 2001},
                    {"TARGET_ENVIRONMENT": "unknown"}]:
            with patch.dict(os.environ, env), patch.object(rollback, "live_state") as live:
                with self.assertRaises(ValueError):
                    rollback.perform(self.audit)
                live.assert_not_called()

    def test_login_failure_still_has_summary(self):
        rollback.summary(self.audit)
        text = self.summary.read_text()
        self.assertIn("operator", text)
        self.assertIn("checkout/login", text)
        self.assertIn("Not resolved", text)

    def test_command_failures_include_diagnostic_and_use_no_shell(self):
        with patch.object(rollback.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 1, "", "Access denied")) as run:
            with self.assertRaisesRegex(ValueError, "Access denied"):
                rollback.command("az", "show")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_live_state_reads_ready_revision_not_desired_template(self):
        app = {"properties": {
            "configuration": {"activeRevisionsMode": "Single", "ingress": {"fqdn": "test.example"}},
            "template": {"containers": [{"name": "taskmanagement-api", "image": B}]},
            "latestRevisionName": "new", "latestReadyRevisionName": "old",
            "provisioningState": "Failed"}}
        revision = {"properties": {"active": True, "healthState": "Healthy",
                                  "template": {"containers": [{"name": "taskmanagement-api", "image": A}]}}}
        with patch.object(rollback, "azure", side_effect=[json.dumps(app), json.dumps(revision)]):
            result = rollback.live_state()
        self.assertEqual((result["desired"], result["serving"]), (B, A))
        app["properties"]["configuration"]["activeRevisionsMode"] = "Multiple"
        with patch.object(rollback, "azure", return_value=json.dumps(app)):
            with self.assertRaisesRegex(ValueError, "Single"):
                rollback.live_state()

    def test_multiple_containers_are_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "single"):
            rollback.container_image({"containers": [
                {"name": "taskmanagement-api", "image": A}, {"name": "sidecar", "image": B}]})

    def test_old_ready_revision_cannot_make_health_pass(self):
        old = state(A)
        old.update(serving=B, ready="old")
        self.health_timeout(old, curl_expected=False)

    def test_failed_health_exhausts_two_minute_budget(self):
        self.health_timeout(state(A), curl_expected=True)

    def health_timeout(self, current, curl_expected):
        clock = [0]

        def sleep(seconds):
            clock[0] += seconds

        with patch.object(rollback.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(rollback.time, "sleep", side_effect=sleep), \
                patch.object(rollback, "live_state", return_value=current), \
                patch.object(rollback.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 22, "", "HTTP 503")) as curl:
            with self.assertRaisesRegex(ValueError, "two minutes"):
                rollback.check_health(A, current["fqdn"])
        self.assertEqual(clock[0], 120)
        self.assertEqual(curl.call_count, 12 if curl_expected else 0)

    def test_ready_target_and_health_success(self):
        current = state(A)
        with patch.object(rollback, "live_state", return_value=current), \
                patch.object(rollback.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 0, "", "")) as curl:
            rollback.check_health(A, current["fqdn"])
        self.assertIn("--max-time", curl.call_args.args[0])
        self.assertIn(f"https://{current['fqdn']}/health", curl.call_args.args[0])

    def test_azure_reads_share_health_deadline(self):
        with patch.object(rollback.time, "monotonic", return_value=120), \
                patch.object(rollback, "azure") as azure:
            with self.assertRaisesRegex(ValueError, "deadline expired"):
                rollback.live_state(deadline=120)
            azure.assert_not_called()

    def test_health_drift_fails_before_probe(self):
        with patch.object(rollback, "live_state", return_value=state(C)), \
                patch.object(rollback.subprocess, "run") as curl:
            with self.assertRaisesRegex(ValueError, "changed during verification"):
                rollback.check_health(A, state()["fqdn"])
            curl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
