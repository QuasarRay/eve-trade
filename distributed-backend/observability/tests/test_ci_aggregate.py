from __future__ import annotations

import unittest
from pathlib import Path

from observability.ci.ci_aggregate import aggregate_exit_code, diagnose_ci_needs, load_needs


class CiAggregateTests(unittest.TestCase):
    def test_failed_upstream_job_and_skipped_e2e_are_diagnosed_as_blocked_validation(self) -> None:
        needs = {
            "go": {"result": "failure", "outputs": {}},
            "python": {"result": "success", "outputs": {}},
            "e2e": {"result": "skipped", "outputs": {}},
        }

        diagnosis = diagnose_ci_needs(run_id="ci-run", needs=needs)

        self.assertEqual(aggregate_exit_code(needs), 1)
        self.assertEqual(diagnosis["test_execution"]["E2E_TEST"]["status"], "BLOCKED")
        self.assertEqual(diagnosis["product_status"], "UNRESOLVED")
        self.assertEqual(diagnosis["most_supported_root_cause_event"]["component"], "go")
        self.assertIn("Per-step observed-run artifact", diagnosis["missing_evidence"][0])

    def test_all_successful_jobs_are_authoritative_success(self) -> None:
        needs = {
            "go": {"result": "success", "outputs": {}},
            "python": {"result": "success", "outputs": {}},
            "e2e": {"result": "success", "outputs": {}},
        }

        diagnosis = diagnose_ci_needs(run_id="ci-run", needs=needs)

        self.assertEqual(aggregate_exit_code(needs), 0)
        self.assertEqual(diagnosis["validation_result"], "passed")
        self.assertEqual(diagnosis["test_execution"]["E2E_TEST"]["status"], "PASSED")
        self.assertEqual(diagnosis["product_status"], "PASSED")

    def test_needs_json_accepts_utf8_bom(self) -> None:
        self.assertEqual(load_needs('\ufeff{"go":{"result":"success"}}')["go"]["result"], "success")

    def test_verify_workflow_has_always_run_aggregate_after_e2e(self) -> None:
        workflow = Path(".github/workflows/verify.yaml").read_text(encoding="utf-8")

        self.assertIn("o11y-aggregate:", workflow)
        self.assertIn("if: ${{ always() }}", workflow)
        self.assertIn("needs: [proto, go, rust-trade-settlement, terraform, kubernetes, python, e2e]", workflow)
        self.assertIn("OBS_CI_NEEDS_JSON: ${{ toJson(needs) }}", workflow)
        self.assertIn("actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", workflow)


if __name__ == "__main__":
    unittest.main()
