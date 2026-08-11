from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.final_audit import (
    FinalAuditError,
    REQUIRED_FINAL_AUDIT_CHECKS,
    finalize_audit,
    seal_audit_observation,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def valid_observations() -> list[dict]:
    return [
        seal_audit_observation(
            check_id=check_id,
            status="PROVEN",
            evidence_digest=SHA_A,
            provenance="framework-command",
            detail="executed final-audit observation",
        )
        for check_id in REQUIRED_FINAL_AUDIT_CHECKS
    ]


class FinalAuditProperties(unittest.TestCase):
    def test_complete_exact_contract_produces_sealed_pass_receipt(self) -> None:
        receipt = finalize_audit(
            valid_observations(),
            expected_workspace_digest=SHA_B,
            current_workspace_digest=SHA_B,
        )
        self.assertEqual(receipt["outcome"], "PASS")
        self.assertEqual(receipt["check_count"], len(REQUIRED_FINAL_AUDIT_CHECKS))
        self.assertEqual(receipt["counts"], {"PROVEN": len(REQUIRED_FINAL_AUDIT_CHECKS)})
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    @given(defect=st.sampled_from(("missing", "blocked", "manual", "bad-evidence", "bad-seal", "workspace")))
    def test_missing_unproved_manual_or_stale_observation_fails_closed(self, defect: str) -> None:
        observations = valid_observations()
        expected_workspace = SHA_B
        current_workspace = SHA_B
        if defect == "missing":
            observations.pop()
        elif defect == "workspace":
            current_workspace = SHA_A
        else:
            observations = copy.deepcopy(observations)
            if defect == "blocked":
                observations[0]["status"] = "BLOCKED"
            elif defect == "manual":
                observations[0]["provenance"] = "manual"
            elif defect == "bad-evidence":
                observations[0]["evidence_digest"] = "not-a-digest"
            elif defect == "bad-seal":
                observations[0]["detail"] = "rewritten after sealing"
        with self.assertRaises(FinalAuditError):
            finalize_audit(
                observations,
                expected_workspace_digest=expected_workspace,
                current_workspace_digest=current_workspace,
            )


if __name__ == "__main__":
    unittest.main()
