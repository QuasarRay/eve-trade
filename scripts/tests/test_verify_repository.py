from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_repository import CI_JOBS, FULL_GATES, classification, ci_gate_results, local_phases, validate_ci


class VerificationProfileTests(unittest.TestCase):
    def test_full_profile_covers_every_required_gate_once(self) -> None:
        covered = [gate for phase in local_phases("python") for gate in phase.gates]
        self.assertCountEqual(covered, FULL_GATES)
        self.assertEqual(len(covered), len(set(covered)))

    def test_profile_distinguishes_complete_partial_and_failed_results(self) -> None:
        self.assertEqual(classification(set(FULL_GATES), False), ("COMPLETE_VERIFICATION_PASSED", 0))
        self.assertEqual(classification({"go"}, False), ("PARTIAL_VERIFICATION_PASSED", 2))
        self.assertEqual(classification(set(FULL_GATES), True), ("FAILED_VERIFICATION", 1))

    def test_ci_validation_rejects_skipped_or_missing_primary_jobs(self) -> None:
        needs = {job: {"result": "success"} for job in CI_JOBS.values()}
        needs["go"] = {"result": "skipped"}
        results = ci_gate_results(needs, ("o11y",))
        self.assertEqual(results["go"], "skipped")
        self.assertEqual(results["o11y"], "success")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profile.json"
            self.assertEqual(validate_ci(needs, ("o11y",), output), 1)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "FAILED_VERIFICATION")

    def test_ci_validation_accepts_only_a_complete_successful_profile(self) -> None:
        needs = {job: {"result": "success"} for job in CI_JOBS.values()}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "profile.json"
            self.assertEqual(validate_ci(needs, ("o11y",), output), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "COMPLETE_VERIFICATION_PASSED")
        self.assertEqual(payload["missing_gates"], [])


if __name__ == "__main__":
    unittest.main()
