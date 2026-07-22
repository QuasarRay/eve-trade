from __future__ import annotations

import unittest
from pathlib import Path

from observability.ci.ci_aggregate import aggregate_exit_code, diagnose_ci_needs, load_needs
from observability.ci.ci_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    canonical_digest,
    sign_evidence,
    workflow_definition_digest,
)


CONTEXT = {
    "repository": "QuasarRay/eve-trade",
    "branch_ref": "refs/heads/experimental",
    "commit_sha": "a" * 40,
    "workflow": "verify",
    "run_id": "123",
    "run_attempt": "2",
    "workflow_definition_digest": workflow_definition_digest(),
}


def evidence(job: str, status: str = "success", started_at: str = "2026-07-10T00:00:00+00:00") -> dict[str, object]:
    command_status = 0 if status == "success" else 1
    commands: list[dict[str, object]] = [
        {
            "step_name": f"Run {job}",
            "exit_code": command_status,
            "stdout_excerpt": "",
            "stderr_excerpt": "",
            "diagnostics": [],
        }
    ]
    if job == "go" and status == "success":
        commands = [
            {"step_name": step, "exit_code": 0, "stdout_excerpt": "", "stderr_excerpt": "", "diagnostics": []}
            for step in ("Run Go tests", "Run Go race detector", "Run Go vulnerability audit")
        ]
    if job == "e2e" and status == "success":
        commands = [
            {
                "step_name": "Run observed integration tests",
                "exit_code": 0,
                "stdout_excerpt": "",
                "stderr_excerpt": "",
                "diagnostics": [
                    {
                        "type": "e2e",
                        "collected_count": 131,
                        "passed_count": 131,
                        "failed_count": 0,
                        "error_count": 0,
                        "skipped_count": 0,
                        "duration_seconds": 95.13,
                    }
                ],
            }
        ]
    bundle: dict[str, object] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        **CONTEXT,
        "job_id": job,
        "job_name": job,
        "step_identity": "ci-evidence/finalize",
        "started_at": started_at,
        "ended_at": "2026-07-10T00:01:00+00:00",
        "command_identity": f"verify/{job}",
        "exit_status": status,
        "normalized_diagnostic": {
            "class": "NONE" if status == "success" else "OBSERVED_FAILURE",
            "summary": f"{job} {status}",
            "caused_by": [],
        },
        "dependencies": [],
        "commands": commands,
        "collector_status": "COMPLETE",
        "provenance": {},
    }
    return sign_evidence(bundle)


class CiAggregateTests(unittest.TestCase):
    def test_failed_jobs_are_observations_not_root_causes(self) -> None:
        needs = {
            "go": {"result": "failure", "outputs": {}},
            "python": {"result": "failure", "outputs": {}},
            "e2e": {"result": "skipped", "outputs": {}},
        }
        bundles = [evidence("go", "failure"), evidence("python", "failure")]

        diagnosis = diagnose_ci_needs(run_id="ci-run", needs=needs, evidence=bundles)

        self.assertEqual(aggregate_exit_code(needs, bundles), 1)
        self.assertEqual(diagnosis["test_execution"]["E2E_TEST"]["status"], "BLOCKED")
        self.assertEqual(diagnosis["product_status"], "UNRESOLVED")
        self.assertIsNone(diagnosis["most_supported_root_cause_event"])
        self.assertTrue(diagnosis["abstained"])
        failures = [event for event in diagnosis["events"] if event["classification"] == "OBSERVED_FAILURE"]
        self.assertEqual({event["component"] for event in failures}, {"go", "python"})
        self.assertTrue(all(event["confidence"] < 1.0 for event in failures))

    def test_parallel_failures_and_identical_timestamps_remain_independent(self) -> None:
        needs = {"go": {"result": "failure"}, "python": {"result": "failure"}}
        diagnosis = diagnose_ci_needs(
            run_id="ci-run",
            needs=needs,
            evidence=[evidence("go", "failure"), evidence("python", "failure")],
        )

        self.assertEqual(diagnosis["causal_chain"], [])
        self.assertTrue(all(not event["caused_by"] for event in diagnosis["events"]))
        self.assertIn("no validated evidence", diagnosis["primary_diagnosis"]["summary"])

    def test_skipped_job_with_failed_dependency_is_classified_explicitly(self) -> None:
        needs = {"go": {"result": "failure"}, "e2e": {"result": "skipped"}}
        diagnosis = diagnose_ci_needs(run_id="ci-run", needs=needs, evidence=[evidence("go", "failure")])

        e2e = next(event for event in diagnosis["events"] if event["component"] == "e2e")
        self.assertEqual(e2e["classification"], "SKIPPED_DUE_TO_DEPENDENCY")
        self.assertEqual(e2e["caused_by"], ["JOB:go"])

    def test_missing_artifact_is_insufficient_evidence_and_fails_gate(self) -> None:
        needs = {"go": {"result": "success"}, "e2e": {"result": "success"}}
        bundles = [evidence("go")]

        diagnosis = diagnose_ci_needs(run_id="ci-run", needs=needs, evidence=bundles)

        self.assertEqual(aggregate_exit_code(needs, bundles), 1)
        self.assertEqual(diagnosis["analysis_status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("mandatory producer evidence is missing for job e2e", diagnosis["missing_evidence"])

    def test_all_successful_jobs_require_authoritative_evidence(self) -> None:
        needs = {"go": {"result": "success"}, "e2e": {"result": "success"}}
        bundles = [evidence("go"), evidence("e2e")]

        diagnosis = diagnose_ci_needs(run_id="ci-run", needs=needs, evidence=bundles)

        self.assertEqual(aggregate_exit_code(needs, bundles), 0)
        self.assertEqual(diagnosis["validation_result"], "passed")
        self.assertEqual(diagnosis["test_execution"]["E2E_TEST"]["status"], "PASSED")
        self.assertEqual(diagnosis["product_status"], "PASSED")
        self.assertEqual(diagnosis["test_execution"]["E2E_TEST"]["tests_passed"], 131)
        self.assertGreater(diagnosis["test_execution"]["E2E_TEST"]["duration_seconds"], 0)

    def test_e2e_success_without_executed_tests_cannot_pass_aggregate(self) -> None:
        bundle = evidence("e2e")
        bundle["commands"] = [
            {"step_name": "Run observed integration tests", "exit_code": 0, "diagnostics": []}
        ]
        bundle["artifact_digest"] = canonical_digest(bundle)

        diagnosis = diagnose_ci_needs(run_id="ci-run", needs={"e2e": {"result": "success"}}, evidence=[bundle])

        self.assertEqual(aggregate_exit_code({"e2e": {"result": "success"}}, [bundle]), 1)
        self.assertEqual(diagnosis["test_execution"]["E2E_TEST"]["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("successful e2e job lacks nonzero-duration passing test evidence", diagnosis["missing_evidence"])

    def test_corrupt_or_context_mismatched_artifacts_fail_gate(self) -> None:
        needs = {"go": {"result": "success"}}
        for message in (
            "corrupted artifact",
            "commit_sha mismatch",
            "repository mismatch",
            "run_id mismatch",
            "run_attempt mismatch",
            "artifact digest mismatch",
        ):
            with self.subTest(message=message):
                self.assertEqual(aggregate_exit_code(needs, [evidence("go")], [message]), 1)

    def test_needs_json_accepts_utf8_bom(self) -> None:
        self.assertEqual(load_needs('\ufeff{"go":{"result":"success"}}')["go"]["result"], "success")

    def test_empty_needs_cannot_false_green(self) -> None:
        self.assertEqual(aggregate_exit_code({}, []), 1)

    def test_workflow_downloads_producer_artifacts_and_fails_on_missing_output(self) -> None:
        workflow = Path(".github/workflows/verify.yaml").read_text(encoding="utf-8")

        self.assertIn("./.github/actions/ci-evidence", workflow)
        self.assertIn("actions/download-artifact@", workflow)
        self.assertIn("pattern: o11y-producer-*", workflow)
        self.assertIn("if-no-files-found: error", workflow)
        self.assertNotIn("continue-on-error: true", workflow)


if __name__ == "__main__":
    unittest.main()
