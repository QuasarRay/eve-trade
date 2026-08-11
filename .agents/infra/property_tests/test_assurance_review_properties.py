from __future__ import annotations

import copy
import importlib
import sys
import unittest
from pathlib import Path

from hypothesis import given, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def assurance():
    return importlib.import_module("agentinfra.assurance")


def review():
    return importlib.import_module("agentinfra.review")


def cycle(*, mode: str = "RED_REQUIRED", remediation: bool = False) -> dict:
    return assurance().new_tdd_cycle(
        task_id="task-1",
        cycle_id="TDD-1",
        mode=mode,
        designed_at_revision=7,
        test_contract_digest=SHA_A,
        oracle_digest=SHA_B,
        remediation=remediation,
        discovered_epoch=1 if remediation else None,
    )


def baseline(value: dict, *, outcome: str = "RED", reason: str = "required production behavior is absent") -> dict:
    return assurance().record_baseline(
        value,
        outcome=outcome,
        observed_implementation_digest=SHA_C,
        command=["python", "-m", "unittest", "contract"],
        environment_digest=SHA_D,
        output_digest=SHA_E,
        semantic_reason=reason,
        harness_valid=True,
        baseline_intact=True,
    )


class TDDCycleStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.value = cycle()
        self.current_test = SHA_A
        self.current_oracle = SHA_B
        self.current_implementation = SHA_C

    @rule()
    def establish_red(self) -> None:
        if self.value["status"] == "TEST_DESIGNED":
            self.value = baseline(self.value)

    @rule()
    def mutate_test(self) -> None:
        self.current_test = SHA_F

    @rule()
    def mutate_oracle(self) -> None:
        self.current_oracle = SHA_F

    @rule()
    def mutate_baseline_before_authorization(self) -> None:
        self.current_implementation = SHA_F

    @rule()
    def abort(self) -> None:
        if self.value["status"] not in {"TDD_CYCLE_ABORTED", "TDD_CYCLE_COMPLETE"}:
            self.value = assurance().abort_tdd_cycle(self.value, "contract genuinely incorrect")

    @invariant()
    def authority_is_exactly_the_frozen_conjunction(self) -> None:
        expected = bool(
            self.value["status"] in {"RED_OBSERVED", "CHARACTERIZATION_OBSERVED", "TEST_FIRST_OBSERVED"}
            and self.current_test == SHA_A
            and self.current_oracle == SHA_B
            and self.current_implementation == SHA_C
        )
        actual = assurance().implementation_authorized(
            self.value,
            current_test_contract_digest=self.current_test,
            current_oracle_digest=self.current_oracle,
            current_implementation_digest=self.current_implementation,
        )
        assert actual is expected


TestTDDCycleStateMachine = TDDCycleStateMachine.TestCase


class AssuranceAndReviewProperties(unittest.TestCase):
    @given(
        source=st.sampled_from(("UNTESTED", "BLOCKED", "INFERRED", "ASSUMED")),
        target=st.sampled_from(("OBSERVED", "PROVEN")),
    )
    def test_epistemic_shortcuts_require_framework_evidence(self, source: str, target: str) -> None:
        self.assertFalse(assurance().can_transition_epistemic(source, target, evidence_ids=[]))
        self.assertTrue(assurance().can_transition_epistemic(source, target, evidence_ids=["E-1"]))

    @given(mode=st.sampled_from(("RED_REQUIRED", "CHARACTERIZATION_REQUIRED", "NON_BEHAVIORAL_TEST_FIRST")))
    def test_baseline_mode_requires_exact_observation(self, mode: str) -> None:
        expected = {
            "RED_REQUIRED": "RED",
            "CHARACTERIZATION_REQUIRED": "CHARACTERIZED",
            "NON_BEHAVIORAL_TEST_FIRST": "TEST_FIRST",
        }[mode]
        value = cycle(mode=mode)
        wrong = ({"RED", "CHARACTERIZED", "TEST_FIRST"} - {expected}).pop()
        with self.assertRaises(assurance().AssuranceError):
            baseline(value, outcome=wrong)
        observed = baseline(value, outcome=expected)
        self.assertEqual(observed["frozen_test_contract_digest"], SHA_A)
        self.assertEqual(observed["frozen_oracle_digest"], SHA_B)

    @given(harness=st.booleans(), intact=st.booleans(), semantic=st.sampled_from(("", "real semantic failure")))
    def test_fake_red_is_rejected(self, harness: bool, intact: bool, semantic: str) -> None:
        value = cycle()
        kwargs = dict(
            outcome="RED",
            observed_implementation_digest=SHA_C,
            command=["python", "contract.py"],
            environment_digest=SHA_D,
            output_digest=SHA_E,
            semantic_reason=semantic,
            harness_valid=harness,
            baseline_intact=intact,
        )
        legitimate = harness and intact and bool(semantic)
        if legitimate:
            assurance().record_baseline(value, **kwargs)
        else:
            with self.assertRaises(assurance().AssuranceError):
                assurance().record_baseline(value, **kwargs)

    def test_cycle_operations_are_append_only_and_do_not_rewrite_input(self) -> None:
        value = cycle()
        original = copy.deepcopy(value)
        observed = baseline(value)
        self.assertEqual(value, original)
        self.assertEqual(
            [event["status"] for event in observed["events"]],
            ["TEST_DESIGNED", "BASELINE_EXECUTED", "RED_OBSERVED", "TEST_CONTRACT_FROZEN"],
        )

    @given(mutated=st.sampled_from(("test", "oracle", "epoch", "failed")))
    def test_green_binds_same_contract_and_current_epoch(self, mutated: str) -> None:
        value = baseline(cycle())
        kwargs = dict(
            current_epoch=1,
            current_test_contract_digest=SHA_A,
            current_oracle_digest=SHA_B,
            current_implementation_digest=SHA_D,
            diff_digest=SHA_E,
            command=["python", "-m", "unittest", "contract"],
            environment_digest=SHA_F,
            output_digest=SHA_C,
            passed=True,
        )
        if mutated == "test":
            kwargs["current_test_contract_digest"] = SHA_F
        elif mutated == "oracle":
            kwargs["current_oracle_digest"] = SHA_F
        elif mutated == "epoch":
            kwargs["current_epoch"] = 0
        elif mutated == "failed":
            kwargs["passed"] = False
        with self.assertRaises(assurance().AssuranceError):
            assurance().record_green(value, **kwargs)
        kwargs.update(current_epoch=1, current_test_contract_digest=SHA_A, current_oracle_digest=SHA_B, passed=True)
        green = assurance().record_green(value, **kwargs)
        complete = assurance().complete_tdd_cycle(green)
        self.assertEqual(complete["status"], "TDD_CYCLE_COMPLETE")

    def test_remediation_cycle_cannot_authorize_fix_before_regression_red(self) -> None:
        value = cycle(remediation=True)
        self.assertFalse(
            assurance().implementation_authorized(
                value,
                current_test_contract_digest=SHA_A,
                current_oracle_digest=SHA_B,
                current_implementation_digest=SHA_C,
            )
        )
        self.assertTrue(
            assurance().implementation_authorized(
                baseline(value, reason="review counterexample reproduced by regression"),
                current_test_contract_digest=SHA_A,
                current_oracle_digest=SHA_B,
                current_implementation_digest=SHA_C,
            )
        )

    def _receipt(self, **overrides) -> dict:
        values = dict(
            task_id="task-1",
            reviewer_lease="lease-1",
            reviewer_role="adversarial-reviewer",
            reviewer_identity="reviewer",
            implementer_identity="implementer",
            epoch=2,
            diff_digest=SHA_A,
            requirements_digest=SHA_B,
            evidence_set_digest=SHA_C,
            tdd_cycle_digest=SHA_D,
            test_law_baseline_digest=SHA_E,
            assumptions_tested=["workspace boundary remains closed"],
            counterexamples_attempted=["stale evidence epoch"],
            boundary_cases=["empty ledger"],
            potential_failures=["review becomes stale after mutation"],
            unexpected_scope=[],
            findings=[],
            outcome="ACCEPTED",
        )
        values.update(overrides)
        return review().build_review_receipt(**values)

    @given(field=st.sampled_from(("epoch", "diff", "requirements", "evidence", "tdd", "laws")))
    def test_review_receipt_is_stale_after_any_bound_surface_changes(self, field: str) -> None:
        receipt = self._receipt()
        current = dict(
            current_epoch=2,
            current_diff_digest=SHA_A,
            current_requirements_digest=SHA_B,
            current_evidence_set_digest=SHA_C,
            current_tdd_cycle_digest=SHA_D,
            current_test_law_baseline_digest=SHA_E,
        )
        key = {
            "epoch": "current_epoch",
            "diff": "current_diff_digest",
            "requirements": "current_requirements_digest",
            "evidence": "current_evidence_set_digest",
            "tdd": "current_tdd_cycle_digest",
            "laws": "current_test_law_baseline_digest",
        }[field]
        current[key] = 3 if field == "epoch" else SHA_F
        self.assertFalse(review().validate_review_receipt(receipt, **current))

    @given(empty_field=st.sampled_from(("assumptions_tested", "counterexamples_attempted", "boundary_cases", "potential_failures")))
    def test_rubber_stamp_and_self_review_are_rejected(self, empty_field: str) -> None:
        with self.assertRaises(review().ReviewError):
            self._receipt(**{empty_field: []})
        with self.assertRaises(review().ReviewError):
            self._receipt(reviewer_identity="implementer")


if __name__ == "__main__":
    unittest.main()
