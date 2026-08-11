from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.state_machine import STATES, TransitionError, validate_transition
from agentinfra.state_store import StateStore
from agentinfra.workspace import workspace_fingerprint


TDD_STATES = {
    "DISCOVER", "TEST_DESIGN", "BASELINE", "GREEN", "FALSIFY", "ADVERSARIAL_REVIEW"
}


def _task(mode: str = "RED_REQUIRED") -> dict:
    expected = {
        "RED_REQUIRED": "RED",
        "CHARACTERIZATION_REQUIRED": "CHARACTERIZED",
        "NON_BEHAVIORAL_TEST_FIRST": "TEST_FIRST",
    }[mode]
    return {
        "state": "PLAN",
        "mode": "write",
        "complexity": "M",
        "risk": "medium",
        "change_epoch": 0,
        "transitions": [
            {"from": "CREATED", "to": "DISCOVER"},
            {"from": "DISCOVER", "to": "PRECHECK"},
            {"from": "PRECHECK", "to": "TRIAGE"},
            {"from": "TRIAGE", "to": "PLAN"},
        ],
        "tdd": {
            "mode": mode,
            "test_design_complete": False,
            "baseline_executed": False,
            "baseline_observed_by_framework": False,
            "baseline_outcome": None,
            "required_baseline_outcome": expected,
            "test_contract_digest": "a" * 64,
            "frozen_test_contract_digest": None,
            "oracle_digest": "b" * 64,
            "frozen_oracle_digest": None,
            "baseline_implementation_digest": "c" * 64,
            "observed_implementation_digest": None,
            "harness_valid": False,
            "baseline_intact": False,
            "semantic_reason": "",
        },
    }


def _implementation_expected(task: dict) -> bool:
    tdd = task["tdd"]
    return bool(
        tdd["test_design_complete"]
        and tdd["baseline_executed"]
        and tdd["baseline_observed_by_framework"]
        and tdd["baseline_outcome"] == tdd["required_baseline_outcome"]
        and tdd["test_contract_digest"] == tdd["frozen_test_contract_digest"]
        and tdd["oracle_digest"] == tdd["frozen_oracle_digest"]
        and tdd["baseline_implementation_digest"] == tdd["observed_implementation_digest"]
        and tdd["harness_valid"]
        and tdd["baseline_intact"]
        and bool(tdd["semantic_reason"].strip())
    )


class TDDTransitionStateMachine(RuleBasedStateMachine):
    """Model implementation authority as a conjunction of frozen TDD facts."""

    def __init__(self) -> None:
        super().__init__()
        self.task = _task("RED_REQUIRED")

    @rule()
    def design_test(self) -> None:
        self.task["tdd"]["test_design_complete"] = True

    @rule()
    def execute_legitimate_red(self) -> None:
        tdd = self.task["tdd"]
        tdd.update(
            baseline_executed=True,
            baseline_observed_by_framework=True,
            baseline_outcome="RED",
            frozen_test_contract_digest=tdd["test_contract_digest"],
            frozen_oracle_digest=tdd["oracle_digest"],
            observed_implementation_digest=tdd["baseline_implementation_digest"],
            harness_valid=True,
            baseline_intact=True,
            semantic_reason="required behavior is absent at the production boundary",
        )

    @rule()
    def mutate_test_contract(self) -> None:
        self.task["tdd"]["test_contract_digest"] = "d" * 64

    @rule()
    def mutate_oracle(self) -> None:
        self.task["tdd"]["oracle_digest"] = "e" * 64

    @rule()
    def invalidate_harness(self) -> None:
        self.task["tdd"]["harness_valid"] = False

    @rule()
    def damage_baseline(self) -> None:
        self.task["tdd"]["baseline_intact"] = False

    @invariant()
    def implementation_authority_matches_independent_model(self) -> None:
        try:
            validate_transition(self.task, "IMPLEMENT", reason="model attempt")
            accepted = True
        except TransitionError:
            accepted = False
        assert accepted is _implementation_expected(self.task)


TestTDDTransitionStateMachine = TDDTransitionStateMachine.TestCase


class TDDLifecycleProperties(unittest.TestCase):
    def _prechecked_store(self, root: Path) -> StateStore:
        (root / ".agents").mkdir(exist_ok=True)
        (root / ".agents" / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
        store = StateStore(root)
        store.create("tdd lifecycle", mode="write", complexity="M", risk="low")
        store.transition("DISCOVER", "discover repository")
        store.transition("PRECHECK", "compile artifacts")
        store.mutate(
            lambda task: task["precheck"].update(
                governance_snapshot={"digest": "a" * 64},
                constitution={"digest": "b" * 64},
                instruction_provenance={"digest": "c" * 64},
                repository_discovery={"digest": "d" * 64},
                workspace_snapshot=workspace_fingerprint(root),
                test_law_baseline={"digest": "e" * 64},
                tdd_plan={"digest": "f" * 64},
                compiled_policy={"digest": "1" * 64},
                mandatory_gates={"digest": "2" * 64},
                write_scope={"digest": "3" * 64},
                budgets={"digest": "4" * 64},
                command_matrix={"digest": "5" * 64},
                review_requirements={"digest": "6" * 64},
            )
        )
        store.transition("TRIAGE", "artifacts verified")
        return store

    def test_constitutional_tdd_states_are_first_class(self) -> None:
        self.assertGreaterEqual(STATES, TDD_STATES)

    def test_mutating_task_cannot_jump_triage_to_implement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._prechecked_store(Path(directory))
            with self.assertRaises(TransitionError):
                store.transition("IMPLEMENT", "skip plan and tests")

    def test_plan_cannot_enter_implement_before_test_design_and_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._prechecked_store(Path(directory))
            store.transition("PLAN", "bounded implementation plan")
            with self.assertRaises(TransitionError):
                store.transition("IMPLEMENT", "no test-first contract")

    @given(mode=st.sampled_from(("RED_REQUIRED", "CHARACTERIZATION_REQUIRED", "NON_BEHAVIORAL_TEST_FIRST")))
    def test_every_tdd_mode_requires_its_exact_baseline_outcome(self, mode: str) -> None:
        task = _task(mode)
        tdd = task["tdd"]
        tdd.update(
            test_design_complete=True,
            baseline_executed=True,
            baseline_observed_by_framework=True,
            frozen_test_contract_digest=tdd["test_contract_digest"],
            frozen_oracle_digest=tdd["oracle_digest"],
            observed_implementation_digest=tdd["baseline_implementation_digest"],
            harness_valid=True,
            baseline_intact=True,
            semantic_reason="executable baseline observation",
        )
        wrong = ({"RED", "CHARACTERIZED", "TEST_FIRST"} - {tdd["required_baseline_outcome"]}).pop()
        tdd["baseline_outcome"] = wrong
        with self.assertRaises(TransitionError):
            validate_transition(task, "IMPLEMENT", reason="wrong baseline")
        tdd["baseline_outcome"] = tdd["required_baseline_outcome"]
        validate_transition(task, "IMPLEMENT", reason="correct baseline")

    @given(field=st.sampled_from(("test_contract_digest", "oracle_digest", "observed_implementation_digest")))
    def test_frozen_contract_or_baseline_mutation_revokes_implementation_authority(self, field: str) -> None:
        task = _task("RED_REQUIRED")
        tdd = task["tdd"]
        tdd.update(
            test_design_complete=True,
            baseline_executed=True,
            baseline_observed_by_framework=True,
            baseline_outcome="RED",
            frozen_test_contract_digest=tdd["test_contract_digest"],
            frozen_oracle_digest=tdd["oracle_digest"],
            observed_implementation_digest=tdd["baseline_implementation_digest"],
            harness_valid=True,
            baseline_intact=True,
            semantic_reason="real missing behavior",
        )
        validate_transition(task, "IMPLEMENT", reason="frozen contract")
        tdd[field] = "9" * 64
        with self.assertRaises(TransitionError):
            validate_transition(task, "IMPLEMENT", reason="mutated contract")

    @given(
        framework_observed=st.booleans(),
        harness_valid=st.booleans(),
        baseline_intact=st.booleans(),
        reason=st.sampled_from(("", "irrelevant", "semantic defect")),
    )
    def test_fake_or_irrelevant_red_cannot_authorize_implementation(
        self,
        framework_observed: bool,
        harness_valid: bool,
        baseline_intact: bool,
        reason: str,
    ) -> None:
        task = _task("RED_REQUIRED")
        tdd = task["tdd"]
        tdd.update(
            test_design_complete=True,
            baseline_executed=True,
            baseline_observed_by_framework=framework_observed,
            baseline_outcome="RED",
            frozen_test_contract_digest=tdd["test_contract_digest"],
            frozen_oracle_digest=tdd["oracle_digest"],
            observed_implementation_digest=tdd["baseline_implementation_digest"],
            harness_valid=harness_valid,
            baseline_intact=baseline_intact,
            semantic_reason=reason if reason == "semantic defect" else "",
        )
        should_accept = framework_observed and harness_valid and baseline_intact and reason == "semantic defect"
        if should_accept:
            validate_transition(task, "IMPLEMENT", reason="legitimate red")
        else:
            with self.assertRaises(TransitionError):
                validate_transition(task, "IMPLEMENT", reason="fake red")

    def test_boolean_self_reports_do_not_complete_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".agents").mkdir()
            (root / ".agents" / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
            store = StateStore(root)
            store.create("weak precheck", mode="write", risk="low")
            store.transition("DISCOVER", "start")
            store.transition("PRECHECK", "start")
            store.mutate(lambda task: task["precheck"].update({"instructions_discovered": True, "project_overlay_checked": True, "acceptance_defined": True, "workspace_inspected": True, "workspace_snapshot": workspace_fingerprint(root)}))
            with self.assertRaises(TransitionError):
                store.transition("TRIAGE", "booleans are not artifacts")


if __name__ == "__main__":
    unittest.main()
