from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra"
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

try:
    from infra.tests import test_public_falsification_workflow as public_fixture
except ModuleNotFoundError:  # deployed unittest discovery exposes the tests directory directly
    import test_public_falsification_workflow as public_fixture


class PublicReviewAuditWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = public_fixture.PublicFalsificationWorkflowTests(
            methodName="test_required_family_uses_executed_current_evidence"
        )
        self.fixture.setUp()
        self.root = self.fixture.root
        self.store = self.fixture.store
        task = self.store.load()
        compiled = dict(task["precheck"]["compiled_policy"])
        compiled.pop("digest", None)
        compiled["gates"] = [
            {
                "id": "G1",
                "description": "disposable acceptance contract",
                "severity": "HARD",
                "family": "fixture",
                "waivable": False,
            }
        ]
        compiled["commands"] = {"test": [["python", "-B", "-m", "unittest"]]}
        compiled["review"] = {
            "independent_required": True,
            "tdd_provenance_audit": True,
            "specialist_security": False,
        }
        sealed = public_fixture.seal(compiled)
        self.store.mutate(lambda state: state["precheck"].__setitem__("compiled_policy", sealed))

    def tearDown(self) -> None:
        self.fixture.tearDown()

    def cli(self, *args: str, expected: int = 0) -> dict:
        return self.fixture.cli(*args, expected=expected)

    def test_closed_handoff_drives_current_review_and_exact_task_audit(self) -> None:
        self.fixture.reach_falsify()
        self.cli(
            "task", "gate-add", "disposable acceptance contract",
            "--id", "G1", "--severity", "HARD",
        )
        attempt = self.cli(
            "falsify", "run",
            "--family", "boundary",
            "--adapter", "unittest",
            "--test-id", "tests.test_normalize.NormalizeContract.test_boundary",
            "--interpretation", "empty input remains stable",
        )
        self.cli("falsify", "complete")
        self.cli("task", "transition", "ADVERSARIAL_REVIEW", "--reason", "current falsification complete")
        opened = self.cli(
            "subagent", "open",
            "--role", "adversarial-reviewer",
            "--context-evidence", attempt["evidence_id"],
        )
        lease = opened["lease"]["lease_id"]
        review_path = self.root / ".aegis" / "review-handoff.json"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "assumptions_tested": [
                        {
                            "claim": "normalization is total over empty input",
                            "observation": "the executed boundary contract passed",
                            "evidence_ids": [attempt["evidence_id"]],
                        }
                    ],
                    "counterexamples_attempted": [
                        {
                            "claim": "empty input may be corrupted",
                            "observation": "no corruption was observed",
                            "evidence_ids": [attempt["evidence_id"]],
                        }
                    ],
                    "boundary_cases": [
                        {
                            "claim": "empty string boundary",
                            "observation": "preserved",
                            "evidence_ids": [attempt["evidence_id"]],
                        }
                    ],
                    "potential_failures": [
                        {
                            "claim": "post-review source mutation",
                            "observation": "must invalidate this receipt",
                            "evidence_ids": [attempt["evidence_id"]],
                        }
                    ],
                    "unexpected_scope": [],
                    "findings": [],
                    "outcome": "ACCEPTED",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.cli(
            "subagent", "close",
            "--lease-id", lease,
            "--outcome", "accepted",
            "--summary", "structured adversarial fixture review complete",
            "--evidence", attempt["evidence_id"],
            "--review-file", str(review_path),
        )
        review = self.cli("review", "complete", "--lease-id", lease)
        self.assertEqual(review["receipt"]["schema"], 2)
        self.assertEqual(review["receipt"]["workflow_independence"], "WORKFLOW_INDEPENDENT")
        self.assertEqual(review["receipt"]["identity_attestation"], "UNATTESTED_IDENTITY")
        self.assertEqual(review["receipt"]["reviewer_lease"], lease)

        self.cli("task", "transition", "VERIFY", "--reason", "review accepted")
        verification = self.cli(
            "evidence", "add",
            "--kind", "test",
            "--summary", "current exact contract verification",
            "--verification",
            "--gate-id", "G1",
            "--argv", sys.executable, "-B", "-m", "unittest",
            "tests.test_normalize.NormalizeContract.test_trim",
        )
        self.cli("task", "gate-prove", "G1", "--evidence", verification["id"])
        self.cli("task", "transition", "FINAL_AUDIT", "--reason", "all current obligations ready")
        audited = self.cli("task", "audit-complete")
        receipt = audited["final_audit_receipt"]
        self.assertEqual(receipt["check_count"], 40)
        self.assertEqual(len(receipt["observations"]), 40)
        self.assertEqual(
            [item["check_id"] for item in receipt["observations"]],
            list(receipt["authoritative_checks"]),
        )
        self.assertEqual(len({item["producer_id"] for item in receipt["observations"]}), 40)
        self.assertEqual(len({item["proof_digest"] for item in receipt["observations"]}), 40)
        self.assertTrue(all(item["evidence_record_ids"] for item in receipt["observations"]))
        finalized = self.cli("task", "transition", "FINALIZE", "--reason", "exact receipt current")
        self.assertEqual(finalized["state"], "FINALIZE")


if __name__ == "__main__":
    unittest.main()
