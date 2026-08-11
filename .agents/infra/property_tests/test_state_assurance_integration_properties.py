from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.assurance import (
    AssuranceError,
    build_falsification_receipt,
    new_tdd_cycle,
)
from agentinfra.controls import validate_gate_waiver
from agentinfra.state_machine import TransitionError
from agentinfra.state_store import StateStore
from infra.tests import test_state_machine as state_fixture


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
EMPTY_EVIDENCE = hashlib.sha256(b"").hexdigest()


class StateAssuranceIntegrationProperties(unittest.TestCase):
    def test_state_store_rejects_caller_labelled_required_gate_waiver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".agents").mkdir()
            (root / ".agents" / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
            store = StateStore(root)
            store.create("waiver provenance", mode="write", risk="low")
            store.mutate(
                lambda task: task["gates"].append(
                    {
                        "id": "G1",
                        "description": "required external decision",
                        "severity": "high",
                        "gate_severity": "REQUIRED",
                        "status": "OPEN",
                        "evidence": [],
                        "created_revision": task["revision"] + 1,
                    }
                )
            )

            def forge(task: dict) -> None:
                task["gates"][0].update(
                    status="WAIVED",
                    waiver_reason="caller supplied",
                    waiver_authority="policy:caller",
                )

            with self.assertRaisesRegex(RuntimeError, "external.*waiver|waiver.*external"):
                store.mutate(forge)

    @given(
        severity=st.sampled_from(("HARD", "REQUIRED", "ADVISORY")),
        provenance=st.sampled_from(("manual", "verified-observation", "external-source")),
        epoch=st.integers(min_value=0, max_value=2),
        trusted=st.booleans(),
    )
    def test_gate_waiver_requires_current_external_trusted_provenance(
        self,
        severity: str,
        provenance: str,
        epoch: int,
        trusted: bool,
    ) -> None:
        gate = {
            "id": "G1",
            "description": "constitutional gate",
            "gate_severity": severity,
            "status": "WAIVED",
            "waiver_reason": "explicitly requested exception",
            "waiver_evidence": "E-1",
        }
        evidence = {
            "E-1": {
                "schema": 2,
                "id": "E-1",
                "task_id": "task-1",
                "change_epoch": epoch,
                "provenance": provenance,
                "details": {
                    "gate_ids": ["G1"],
                    "waiver_authority": {
                        "kind": "user",
                        "identity": "external-user",
                        "trusted": trusted,
                    },
                },
            }
        }
        expected = severity == "REQUIRED" and provenance == "external-source" and epoch == 1 and trusted
        self.assertEqual(
            validate_gate_waiver(
                gate,
                evidence,
                task_id="task-1",
                current_epoch=1,
            ),
            expected,
        )

    def test_green_falsification_and_review_are_enforced_as_current_state(self) -> None:
        fixture = state_fixture.TestState(methodName="test_valid_precheck")
        fixture.setUp()
        try:
            fixture.s.create("integrated assurance", mode="write", risk="low")
            fixture._implement()
            with self.assertRaises(TransitionError):
                fixture.s.transition("GREEN", "self-reported green")
            fixture._green()
            with self.assertRaises(TransitionError):
                fixture.s.transition("ADVERSARIAL_REVIEW", "skip falsification")
            fixture._review_current()
            current = fixture.s.load()
            self.assertEqual(current["falsification"]["schema"], 2)
            self.assertEqual(current["review_receipt"]["schema"], 2)
            fixture.s.mutate(lambda task: task.__setitem__("diff_digest", SHA_F))
            self.assertIsNone(fixture.s.load()["review_receipt"])
        finally:
            fixture.tearDown()

    @given(field=st.sampled_from(("test", "oracle")))
    def test_recorded_cycle_identity_cannot_be_replaced_after_baseline(self, field: str) -> None:
        fixture = state_fixture.TestState(methodName="test_valid_precheck")
        fixture.setUp()
        try:
            task = fixture.s.create("cycle identity", mode="write", risk="low")
            fixture._implement()
            current = fixture.s.load()
            cycle = next(
                item for item in current["tdd"]["cycles"]
                if item["cycle_id"] == current["tdd"]["active_cycle_id"]
            )
            replacement = new_tdd_cycle(
                task_id=task["id"],
                cycle_id=cycle["cycle_id"],
                mode="RED_REQUIRED",
                designed_at_revision=cycle["designed_at_revision"],
                test_contract_digest=SHA_F if field == "test" else cycle["test_contract_digest"],
                oracle_digest=SHA_F if field == "oracle" else cycle["oracle_digest"],
            )
            with self.assertRaises((AssuranceError, RuntimeError)):
                fixture.s.record_tdd_cycle(replacement)
        finally:
            fixture.tearDown()

    @given(counterexamples=st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=3))
    def test_falsification_cannot_claim_clean_when_counterexamples_exist(self, counterexamples: list[str]) -> None:
        with self.assertRaises(AssuranceError):
            build_falsification_receipt(
                task_id="task-1",
                tdd_cycle_digest=SHA_A,
                epoch=1,
                diff_digest=SHA_B,
                methods=["property"],
                attempts=["hostile input"],
                boundary_cases=["empty"],
                counterexamples=counterexamples,
                outcome="NO_COUNTEREXAMPLE",
            )


if __name__ == "__main__":
    unittest.main()
