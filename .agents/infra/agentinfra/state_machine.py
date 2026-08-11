from __future__ import annotations

import hashlib
import json
import re

from .assurance import validate_falsification_receipt
from .review import validate_review_receipt
from .final_audit import non_write_audit_binding_digest, validate_task_audit_receipt


STATES = {
    "CREATED",
    "DISCOVER",
    "PRECHECK",
    "TRIAGE",
    "EXPLORE",
    "RESEARCH",
    "ARCHITECT",
    "PLAN",
    "TEST_DESIGN",
    "BASELINE",
    "IMPLEMENT",
    "DIAGNOSE",
    "GREEN",
    "VERIFY",
    "FALSIFY",
    "REVIEW",  # Compatibility state for pre-constitutional task records.
    "ADVERSARIAL_REVIEW",
    "REMEDIATE",
    "FINAL_AUDIT",
    "FINALIZE",
    "BLOCKED",
    "FAILED",
    "CANCELLED",
    "ABANDONED",
}

TERMINAL_STATES = {"FAILED", "FINALIZE", "CANCELLED", "ABANDONED"}
_ESCAPES = {"BLOCKED", "CANCELLED", "ABANDONED", "FAILED"}

# ``PLAN -> IMPLEMENT`` remains a representable edge because callers may record
# TEST_DESIGN and BASELINE as content-addressed artifacts rather than separate UI
# transitions.  validate_transition applies the same TDD authority predicate to
# both representations.  Mutating tasks can never use TRIAGE -> IMPLEMENT.
ALLOWED = {
    "CREATED": {"DISCOVER"} | _ESCAPES,
    "DISCOVER": {"PRECHECK"} | _ESCAPES,
    "PRECHECK": {"TRIAGE"} | _ESCAPES,
    "TRIAGE": {"EXPLORE", "RESEARCH", "ARCHITECT", "PLAN"} | _ESCAPES,
    "EXPLORE": {"TRIAGE", "RESEARCH", "ARCHITECT", "PLAN"} | _ESCAPES,
    "RESEARCH": {"TRIAGE", "EXPLORE", "ARCHITECT", "PLAN"} | _ESCAPES,
    "ARCHITECT": {"PLAN", "TRIAGE"} | _ESCAPES,
    "PLAN": {"TEST_DESIGN", "IMPLEMENT", "VERIFY"} | _ESCAPES,
    "TEST_DESIGN": {"BASELINE"} | _ESCAPES,
    "BASELINE": {"IMPLEMENT", "REMEDIATE", "TEST_DESIGN"} | _ESCAPES,
    "IMPLEMENT": {"GREEN", "DIAGNOSE", "TEST_DESIGN"} | _ESCAPES,
    "DIAGNOSE": {"TEST_DESIGN", "REVIEW", "ADVERSARIAL_REVIEW"} | _ESCAPES,
    "GREEN": {"VERIFY", "FALSIFY", "ADVERSARIAL_REVIEW", "TEST_DESIGN"} | _ESCAPES,
    "VERIFY": {"FALSIFY", "ADVERSARIAL_REVIEW", "TEST_DESIGN", "FINAL_AUDIT"} | _ESCAPES,
    "FALSIFY": {"VERIFY", "ADVERSARIAL_REVIEW", "TEST_DESIGN"} | _ESCAPES,
    "REVIEW": {"TEST_DESIGN", "REMEDIATE", "VERIFY", "FINAL_AUDIT"} | _ESCAPES,
    "ADVERSARIAL_REVIEW": {"TEST_DESIGN", "VERIFY", "FALSIFY", "FINAL_AUDIT"} | _ESCAPES,
    "REMEDIATE": {"GREEN", "DIAGNOSE", "TEST_DESIGN"} | _ESCAPES,
    "FINAL_AUDIT": {"TEST_DESIGN", "VERIFY", "FINALIZE"} | _ESCAPES,
    "BLOCKED": STATES - {"CREATED", "FINALIZE"},
    "FAILED": set(),
    "FINALIZE": set(),
    "CANCELLED": set(),
    "ABANDONED": set(),
}

PRECHECK_KEYS = {
    "governance_snapshot",
    "constitution",
    "instruction_provenance",
    "repository_discovery",
    "workspace_snapshot",
    "test_law_baseline",
    "tdd_plan",
    "compiled_policy",
    "mandatory_gates",
    "write_scope",
    "budgets",
    "command_matrix",
    "review_requirements",
}

TDD_BASELINE_OUTCOMES = {
    "RED_REQUIRED": "RED",
    "CHARACTERIZATION_REQUIRED": "CHARACTERIZED",
    "NON_BEHAVIORAL_TEST_FIRST": "TEST_FIRST",
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TransitionError(RuntimeError):
    pass


def _transition_targets(task: dict) -> list[str]:
    return [item.get("to") for item in task.get("transitions", [])]


def _artifact_digest(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    digest = value.get("digest", value.get("sha256"))
    return digest if isinstance(digest, str) and _SHA256_RE.fullmatch(digest) else None


def _value_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _current_tdd_cycle(task: dict) -> dict | None:
    tdd = task.get("tdd")
    if not isinstance(tdd, dict):
        return None
    active = tdd.get("active_cycle_id")
    cycles = tdd.get("cycles")
    if not isinstance(active, str) or not isinstance(cycles, list):
        return None
    return next((item for item in cycles if isinstance(item, dict) and item.get("cycle_id") == active), None)


def _validate_current_green(task: dict) -> dict:
    cycle = _current_tdd_cycle(task)
    if cycle is None or cycle.get("status") not in {"GREEN_PROVEN", "TDD_CYCLE_COMPLETE"}:
        raise TransitionError("current TDD cycle has not established GREEN")
    if task.get("tdd", {}).get("green_observed_by_framework") is not True:
        raise TransitionError("current GREEN lacks trusted framework execution evidence")
    epoch = int(task.get("change_epoch", 0))
    if cycle.get("green_epoch") != epoch or task.get("tdd", {}).get("green_epoch") != epoch:
        raise TransitionError("GREEN evidence is stale for the current implementation epoch")
    if cycle.get("test_contract_digest") != cycle.get("frozen_test_contract_digest"):
        raise TransitionError("GREEN test contract no longer matches its frozen RED contract")
    if cycle.get("oracle_digest") != cycle.get("frozen_oracle_digest"):
        raise TransitionError("GREEN oracle no longer matches its frozen RED oracle")
    if (
        cycle.get("green_implementation_digest") != task.get("implementation_digest")
        or cycle.get("green_diff_digest") != task.get("diff_digest")
    ):
        raise TransitionError("GREEN evidence is not bound to the current implementation and diff")
    return cycle


def _validate_current_falsification(task: dict, cycle: dict) -> None:
    if not isinstance(task.get("falsification"), dict) or task["falsification"].get("schema") != 2 or not validate_falsification_receipt(
        task.get("falsification"),
        task_id=task.get("id"),
        current_tdd_cycle_digest=cycle.get("cycle_sha256"),
        current_epoch=task.get("change_epoch"),
        current_diff_digest=task.get("diff_digest"),
        require_clean=True,
    ):
        raise TransitionError("current candidate lacks clean, current falsification evidence")


def _validate_current_review(task: dict, cycle: dict) -> None:
    receipt = task.get("review_receipt")
    if not isinstance(receipt, dict) or receipt.get("schema") != 2 or receipt.get("task_id") != task.get("id"):
        raise TransitionError("current candidate lacks an independent review receipt")
    matching_handoff = any(
        item.get("lease_id", item.get("handoff_id")) == receipt.get("reviewer_lease")
        and item.get("role") == receipt.get("reviewer_role")
        and item.get("outcome") in {"accepted", "partial"}
        for item in task.get("child_history", [])
    )
    if not matching_handoff or not validate_review_receipt(
        receipt,
        current_epoch=task.get("change_epoch"),
        current_diff_digest=task.get("diff_digest"),
        current_requirements_digest=_artifact_digest(task.get("precheck", {}).get("compiled_policy")),
        current_evidence_set_digest=task.get("evidence_set_digest"),
        current_tdd_cycle_digest=cycle.get("cycle_sha256"),
        current_test_law_baseline_digest=_artifact_digest(task.get("precheck", {}).get("test_law_baseline")),
        current_falsification_receipt_digest=(
            task.get("falsification", {}).get("receipt_sha256")
            if isinstance(task.get("falsification"), dict)
            else None
        ),
    ):
        raise TransitionError("independent review is missing, rejected, or stale")


def _validate_current_final_audit(task: dict) -> None:
    receipt = task.get("final_audit_receipt")
    cycle = _current_tdd_cycle(task)
    workspace = task.get("final_audit_workspace")
    from .final_audit_runtime import PRODUCER_REGISTRY_DIGEST

    non_write = task.get("mode") == "read"
    workspace_sha256 = workspace.get("sha256") if isinstance(workspace, dict) else None
    tdd_digest = (
        non_write_audit_binding_digest(binding="tdd-cycle", task_id=task["id"], epoch=task["change_epoch"], workspace_sha256=workspace_sha256)
        if non_write else (cycle.get("cycle_sha256") if cycle else None)
    )
    review_digest = (
        non_write_audit_binding_digest(binding="review-receipt", task_id=task["id"], epoch=task["change_epoch"], workspace_sha256=workspace_sha256)
        if non_write else (
            task.get("review_receipt", {}).get("receipt_sha256")
            if isinstance(task.get("review_receipt"), dict) else None
        )
    )
    falsification_digest = (
        non_write_audit_binding_digest(binding="falsification-receipt", task_id=task["id"], epoch=task["change_epoch"], workspace_sha256=workspace_sha256)
        if non_write else (
            task.get("falsification", {}).get("receipt_sha256")
            if isinstance(task.get("falsification"), dict) else None
        )
    )

    if (cycle is None and not non_write) or not isinstance(workspace, dict) or workspace.get("available") is not True or not validate_task_audit_receipt(
        receipt,
        task_id=task.get("id"),
        epoch=task.get("change_epoch"),
        implementation_digest=task.get("implementation_digest"),
        diff_digest=task.get("diff_digest"),
        workspace_sha256=workspace.get("sha256"),
        compiled_contract_digest=_artifact_digest(task.get("precheck", {}).get("compiled_policy")),
        write_scope_digest=_artifact_digest(task.get("precheck", {}).get("write_scope")),
        governance_digest=_artifact_digest(task.get("precheck", {}).get("governance_snapshot")),
        evidence_set_digest=task.get("evidence_set_digest"),
        evidence_head=task.get("evidence_head"),
        tdd_cycle_digest=tdd_digest,
        review_receipt_digest=review_digest,
        falsification_receipt_digest=falsification_digest,
        test_law_baseline_digest=_artifact_digest(task.get("precheck", {}).get("test_law_baseline")),
        gates_digest=_value_digest(task.get("gates", [])),
        risks_digest=_value_digest(task.get("risks", [])),
        decisions_digest=_value_digest(task.get("decisions", [])),
        child_history_digest=_value_digest(task.get("child_history", [])),
        verification_digest=_value_digest(task.get("verification_evidence", [])),
        producer_registry_digest=PRODUCER_REGISTRY_DIGEST,
    ):
        raise TransitionError("final audit lacks a current exact framework-produced receipt")


def _validate_precheck(task: dict) -> None:
    artifacts = task.get("precheck")
    if not isinstance(artifacts, dict):
        raise TransitionError("precheck must contain content-addressed artifacts")
    missing = sorted(key for key in PRECHECK_KEYS if _artifact_digest(artifacts.get(key)) is None)
    if missing:
        raise TransitionError("precheck artifacts missing or unbound: " + ", ".join(missing))


def _validate_tdd_authority(task: dict) -> None:
    tdd = task.get("tdd")
    if not isinstance(tdd, dict):
        raise TransitionError("implementation requires a compiled TDD contract")
    mode = tdd.get("mode")
    expected = TDD_BASELINE_OUTCOMES.get(mode)
    if expected is None:
        raise TransitionError("implementation requires a constitutional TDD mode")
    if tdd.get("required_baseline_outcome") != expected:
        raise TransitionError("compiled TDD baseline outcome conflicts with its mode")
    if tdd.get("test_design_complete") is not True:
        raise TransitionError("implementation requires completed TEST_DESIGN")
    if tdd.get("baseline_executed") is not True:
        raise TransitionError("implementation requires an executed pre-implementation baseline")
    if tdd.get("baseline_observed_by_framework") is not True:
        raise TransitionError("implementation requires a trusted framework-observed baseline")
    if tdd.get("baseline_outcome") != expected:
        raise TransitionError(f"{mode} requires baseline outcome {expected}")
    for current, frozen, label in (
        ("test_contract_digest", "frozen_test_contract_digest", "test contract"),
        ("oracle_digest", "frozen_oracle_digest", "oracle"),
    ):
        current_digest = tdd.get(current)
        frozen_digest = tdd.get(frozen)
        if not (
            isinstance(current_digest, str)
            and _SHA256_RE.fullmatch(current_digest)
            and current_digest == frozen_digest
        ):
            raise TransitionError(f"{label} is not frozen at its current digest")
    baseline = tdd.get("baseline_implementation_digest")
    observed = tdd.get("observed_implementation_digest")
    if not (
        isinstance(baseline, str)
        and _SHA256_RE.fullmatch(baseline)
        and baseline == observed
    ):
        raise TransitionError("baseline evidence is not bound to the observed pre-implementation source")
    if tdd.get("harness_valid") is not True:
        raise TransitionError("invalid or deliberately damaged test harness cannot establish baseline authority")
    if tdd.get("baseline_intact") is not True:
        raise TransitionError("damaged or reconstructed production baseline cannot authorize implementation")
    semantic_reason = tdd.get("semantic_reason")
    if not isinstance(semantic_reason, str) or not semantic_reason.strip():
        raise TransitionError("baseline lacks a semantic explanation of the observed behavior")


def validate_transition(task: dict, target: str, *, reason: str | None = None) -> None:
    target = target.upper()
    current = task["state"]
    if reason is not None and not reason.strip():
        raise TransitionError("transition reason must not be empty")
    if target not in STATES:
        raise TransitionError(f"unknown state: {target}")
    if current == target == "FINALIZE":
        return
    if target not in ALLOWED[current]:
        raise TransitionError(f"invalid transition {current} -> {target}")
    if current == "BLOCKED" and target != "FAILED":
        previous = task.get("previous_state")
        if not previous or target != previous:
            raise TransitionError(f"BLOCKED task may resume only to previous state {previous!r}, not {target}")
    if current == "PRECHECK" and target == "TRIAGE":
        _validate_precheck(task)
    if target in {"IMPLEMENT", "REMEDIATE"}:
        history = _transition_targets(task)
        if task.get("mode") == "write":
            if current == "TRIAGE":
                raise TransitionError("mutating task cannot jump directly from TRIAGE to implementation")
            if "PLAN" not in history and current not in {"PLAN", "BASELINE"}:
                raise TransitionError("mutating task requires PLAN before implementation")
            _validate_tdd_authority(task)
        elif task.get("risk") in {"high", "critical"} and "PLAN" not in history and current != "PLAN":
            raise TransitionError(f"{task.get('risk')} risk task requires PLAN before implementation")
    if target in {"GREEN", "FALSIFY"} and task.get("mode") == "write":
        _validate_current_green(task)
    if target == "ADVERSARIAL_REVIEW" and task.get("mode") == "write":
        cycle = _validate_current_green(task)
        _validate_current_falsification(task, cycle)
    if target == "FINAL_AUDIT":
        if task.get("active_child"):
            raise TransitionError("cannot enter FINAL_AUDIT with an active child")
        if not task.get("verification_evidence"):
            raise TransitionError("task requires direct verification evidence before FINAL_AUDIT")
        if task.get("verification_epoch") != int(task.get("change_epoch", 0)):
            raise TransitionError("verification evidence is stale for the current implementation epoch")
        if task.get("mode") == "write":
            if not task.get("gates"):
                raise TransitionError("mutating task requires at least one acceptance gate before FINAL_AUDIT")
            if not any(gate.get("status") == "PROVEN" for gate in task.get("gates", [])):
                raise TransitionError("mutating task requires at least one proven, non-waived acceptance gate")
            cycle = _validate_current_green(task)
            _validate_current_falsification(task, cycle)
            _validate_current_review(task, cycle)
        bad = [gate["id"] for gate in task.get("gates", []) if gate.get("status") not in {"PROVEN", "WAIVED"}]
        if bad:
            raise TransitionError("unresolved acceptance gates before FINAL_AUDIT: " + ", ".join(bad))
        blocking = [
            risk["id"]
            for risk in task.get("risks", [])
            if risk.get("severity") in {"high", "critical"} and risk.get("status") != "resolved"
        ]
        if blocking:
            raise TransitionError("unresolved blocking risks before FINAL_AUDIT: " + ", ".join(blocking))
        current_epoch = int(task.get("change_epoch", 0))
        current_review = any(
            item.get("to") in {"REVIEW", "ADVERSARIAL_REVIEW"} and item.get("epoch") == current_epoch
            for item in task.get("transitions", [])
        )
        if task.get("mode") == "write" and not current_review:
            raise TransitionError("mutating task requires current independent ADVERSARIAL_REVIEW before FINAL_AUDIT")
        if task.get("risk") == "critical":
            specialist_review = any(
                item.get("outcome") in {"accepted", "partial"}
                and any(marker in str(item.get("role", "")).casefold() for marker in ("adversarial", "security"))
                for item in task.get("child_history", [])
            )
            if not specialist_review:
                raise TransitionError("critical risk task requires an accepted adversarial or security child review")
    if target == "FINALIZE":
        if task.get("active_child"):
            raise TransitionError("cannot finalize with active child lease")
        if task.get("mode") == "write" and not task.get("gates"):
            raise TransitionError("mutating task requires at least one acceptance gate")
        if task.get("mode") == "write" and not any(gate.get("status") == "PROVEN" for gate in task.get("gates", [])):
            raise TransitionError("mutating task requires at least one proven, non-waived acceptance gate")
        if task.get("mode") == "write":
            cycle = _validate_current_green(task)
            _validate_current_falsification(task, cycle)
            _validate_current_review(task, cycle)
        bad = [gate["id"] for gate in task.get("gates", []) if gate.get("status") not in {"PROVEN", "WAIVED"}]
        if bad:
            raise TransitionError("unresolved acceptance gates: " + ", ".join(bad))
        blocking = [
            risk["id"]
            for risk in task.get("risks", [])
            if risk.get("severity") in {"high", "critical"} and risk.get("status") != "resolved"
        ]
        if blocking:
            raise TransitionError("unresolved blocking risks: " + ", ".join(blocking))
        if not task.get("verification_evidence"):
            raise TransitionError("task requires direct verification evidence")
        if task.get("verification_epoch") != int(task.get("change_epoch", 0)):
            raise TransitionError("verification evidence is stale for the current implementation epoch")
        if not task.get("final_audit_complete"):
            raise TransitionError("final audit not recorded")
        _validate_current_final_audit(task)
