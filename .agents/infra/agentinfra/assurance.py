from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Iterable


EPISTEMIC_STATUSES = {"OBSERVED", "PROVEN", "INFERRED", "ASSUMED", "UNTESTED", "BLOCKED"}
TDD_MODES = {
    "RED_REQUIRED": ("RED", "RED_OBSERVED"),
    "CHARACTERIZATION_REQUIRED": ("CHARACTERIZED", "CHARACTERIZATION_OBSERVED"),
    "NON_BEHAVIORAL_TEST_FIRST": ("TEST_FIRST", "TEST_FIRST_OBSERVED"),
}
TDD_STATUSES = {
    "TEST_DESIGNED",
    "BASELINE_ATTEMPT",
    "BASELINE_EXECUTED",
    "RED_OBSERVED",
    "CHARACTERIZATION_OBSERVED",
    "TEST_FIRST_OBSERVED",
    "TEST_CONTRACT_FROZEN",
    "GREEN_PROVEN",
    "TDD_CYCLE_ABORTED",
    "TDD_CYCLE_COMPLETE",
}
_BASELINE_STATUSES = {value[1] for value in TDD_MODES.values()}
FALSIFICATION_OUTCOMES = {"NO_COUNTEREXAMPLE", "COUNTEREXAMPLE"}
FALSIFICATION_CAPABILITY_STATUSES = {"PROVEN", "UNAVAILABLE", "BLOCKED", "UNTESTED", "NOT_APPLICABLE"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class AssuranceError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: dict, field: str) -> str:
    return hashlib.sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise AssuranceError(f"{label} must be a canonical sha256")
    return value


def _require_command(command: object) -> list[str]:
    if (
        not isinstance(command, (list, tuple))
        or not command
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise AssuranceError("TDD evidence command must be a non-empty argv array")
    return list(command)


def _require_string_list(value: object, label: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise AssuranceError(f"{label} must be an array of non-empty strings")
    if nonempty and not value:
        raise AssuranceError(f"{label} must not be empty")
    return [item.strip() for item in value]


def can_transition_epistemic(source: str, target: str, *, evidence_ids: Iterable[str] = ()) -> bool:
    """Return whether a claim may be reclassified without inventing evidence.

    Epistemic states are deliberately not treated as a numeric ladder.  A move
    to OBSERVED or PROVEN always needs a framework evidence reference unless it
    is already in that exact state.
    """

    if source not in EPISTEMIC_STATUSES or target not in EPISTEMIC_STATUSES:
        return False
    if source == target:
        return True
    ids = list(evidence_ids)
    valid_evidence = bool(ids) and all(isinstance(item, str) and item.startswith("E-") for item in ids)
    if target in {"OBSERVED", "PROVEN"}:
        return valid_evidence
    # Moving back to an explicitly weaker/uncertain state is honest and does
    # not require proof.  It never creates a success claim.
    return target in {"INFERRED", "ASSUMED", "UNTESTED", "BLOCKED"}


def _event(status: str, **details: object) -> dict:
    if status not in TDD_STATUSES:
        raise AssuranceError(f"unknown TDD evidence status {status!r}")
    return {"status": status, "at": _now(), **details}


def _seal(cycle: dict) -> dict:
    cycle["cycle_sha256"] = _digest(cycle, "cycle_sha256")
    return cycle


def validate_tdd_cycle(cycle: dict) -> None:
    if not isinstance(cycle, dict) or cycle.get("schema") != 1:
        raise AssuranceError("invalid TDD cycle schema")
    for field in ("task_id", "cycle_id"):
        if not isinstance(cycle.get(field), str) or not _ID_RE.fullmatch(cycle[field]):
            raise AssuranceError(f"invalid TDD cycle {field}")
    if cycle.get("mode") not in TDD_MODES:
        raise AssuranceError("invalid TDD cycle mode")
    if cycle.get("status") not in TDD_STATUSES:
        raise AssuranceError("invalid TDD cycle status")
    for field in ("test_contract_digest", "oracle_digest"):
        _require_digest(cycle.get(field), field)
    if not isinstance(cycle.get("events"), list) or not cycle["events"]:
        raise AssuranceError("TDD cycle requires an append-only event ledger")
    if cycle["events"][0].get("status") != "TEST_DESIGNED":
        raise AssuranceError("TDD cycle does not begin with TEST_DESIGNED")
    if any(not isinstance(event, dict) or event.get("status") not in TDD_STATUSES for event in cycle["events"]):
        raise AssuranceError("invalid TDD cycle event")
    if cycle.get("cycle_sha256") != _digest(cycle, "cycle_sha256"):
        raise AssuranceError("TDD cycle integrity digest mismatch")


def new_tdd_cycle(
    *,
    task_id: str,
    cycle_id: str,
    mode: str,
    designed_at_revision: int,
    test_contract_digest: str,
    oracle_digest: str,
    remediation: bool = False,
    discovered_epoch: int | None = None,
) -> dict:
    if not isinstance(task_id, str) or not _ID_RE.fullmatch(task_id):
        raise AssuranceError("invalid task id")
    if not isinstance(cycle_id, str) or not _ID_RE.fullmatch(cycle_id):
        raise AssuranceError("invalid cycle id")
    if mode not in TDD_MODES:
        raise AssuranceError("invalid TDD mode")
    if not isinstance(designed_at_revision, int) or isinstance(designed_at_revision, bool) or designed_at_revision < 0:
        raise AssuranceError("designed_at_revision must be non-negative")
    if remediation and (
        not isinstance(discovered_epoch, int) or isinstance(discovered_epoch, bool) or discovered_epoch < 0
    ):
        raise AssuranceError("remediation cycles require the broken candidate epoch")
    if not remediation and discovered_epoch is not None:
        raise AssuranceError("initial TDD cycle cannot claim a remediation discovery epoch")
    contract = _require_digest(test_contract_digest, "test_contract_digest")
    oracle = _require_digest(oracle_digest, "oracle_digest")
    value = {
        "schema": 1,
        "task_id": task_id,
        "cycle_id": cycle_id,
        "mode": mode,
        "status": "TEST_DESIGNED",
        "remediation": remediation,
        "discovered_epoch": discovered_epoch,
        "designed_at_revision": designed_at_revision,
        "test_contract_digest": contract,
        "oracle_digest": oracle,
        "frozen_test_contract_digest": None,
        "frozen_oracle_digest": None,
        "baseline_implementation_digest": None,
        "baseline_epoch": discovered_epoch if remediation else 0,
        "green_epoch": None,
        "events": [
            _event(
                "TEST_DESIGNED",
                task_revision=designed_at_revision,
                test_contract_digest=contract,
                oracle_digest=oracle,
                remediation=remediation,
            )
        ],
        # Pure constructors are intentionally not an operational trust
        # boundary.  StateStore will never grant implementation authority to
        # a caller-asserted baseline carrying this source marker.
        "authority_source": "CALLER_ASSERTED",
    }
    return _seal(value)


def record_baseline_attempt(
    cycle: dict,
    *,
    classification: str,
    evidence_id: str,
    evidence_digest: str,
    detail: str,
) -> dict:
    """Append a fail-closed observed baseline attempt without granting authority."""

    validate_tdd_cycle(cycle)
    if cycle["status"] != "TEST_DESIGNED":
        raise AssuranceError("baseline attempts require an active TEST_DESIGNED cycle")
    if not isinstance(classification, str) or not classification.strip():
        raise AssuranceError("baseline attempt requires a classification")
    if not isinstance(evidence_id, str) or not evidence_id.startswith("E-"):
        raise AssuranceError("baseline attempt requires framework evidence")
    _require_digest(evidence_digest, "baseline attempt evidence digest")
    if not isinstance(detail, str) or not detail.strip():
        raise AssuranceError("baseline attempt requires detail")
    updated = copy.deepcopy(cycle)
    updated["events"].append(
        _event(
            "BASELINE_ATTEMPT",
            classification=classification.strip(),
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            detail=detail.strip(),
        )
    )
    return _seal(updated)


def record_baseline(
    cycle: dict,
    *,
    outcome: str,
    observed_implementation_digest: str,
    command: Iterable[str],
    environment_digest: str,
    output_digest: str,
    semantic_reason: str,
    harness_valid: bool,
    baseline_intact: bool,
) -> dict:
    validate_tdd_cycle(cycle)
    if cycle["status"] != "TEST_DESIGNED":
        raise AssuranceError("baseline can be recorded exactly once after TEST_DESIGNED")
    expected_outcome, observed_status = TDD_MODES[cycle["mode"]]
    if outcome != expected_outcome:
        raise AssuranceError(f"{cycle['mode']} requires baseline outcome {expected_outcome}")
    if harness_valid is not True:
        raise AssuranceError("invalid or deliberately broken harness cannot establish a baseline")
    if baseline_intact is not True:
        raise AssuranceError("damaged production cannot establish a legitimate baseline")
    if not isinstance(semantic_reason, str) or not semantic_reason.strip():
        raise AssuranceError("baseline requires a semantic explanation")
    implementation = _require_digest(observed_implementation_digest, "baseline implementation digest")
    environment = _require_digest(environment_digest, "environment digest")
    output = _require_digest(output_digest, "output digest")
    argv = _require_command(command)
    updated = copy.deepcopy(cycle)
    updated["status"] = observed_status
    updated["baseline_implementation_digest"] = implementation
    updated["frozen_test_contract_digest"] = updated["test_contract_digest"]
    updated["frozen_oracle_digest"] = updated["oracle_digest"]
    common = {
        "baseline_implementation_digest": implementation,
        "test_contract_digest": updated["test_contract_digest"],
        "oracle_digest": updated["oracle_digest"],
        "command": argv,
        "environment_digest": environment,
        "output_digest": output,
        "semantic_reason": semantic_reason.strip(),
        "harness_valid": True,
        "baseline_intact": True,
    }
    updated["events"].append(_event("BASELINE_EXECUTED", outcome=outcome, **common))
    updated["events"].append(_event(observed_status, **common))
    updated["events"].append(
        _event(
            "TEST_CONTRACT_FROZEN",
            test_contract_digest=updated["frozen_test_contract_digest"],
            oracle_digest=updated["frozen_oracle_digest"],
        )
    )
    return _seal(updated)


def implementation_authorized(
    cycle: dict,
    *,
    current_test_contract_digest: str,
    current_oracle_digest: str,
    current_implementation_digest: str,
) -> bool:
    try:
        validate_tdd_cycle(cycle)
    except AssuranceError:
        return False
    return bool(
        cycle.get("status") in _BASELINE_STATUSES
        and cycle.get("test_contract_digest") == cycle.get("frozen_test_contract_digest") == current_test_contract_digest
        and cycle.get("oracle_digest") == cycle.get("frozen_oracle_digest") == current_oracle_digest
        and cycle.get("baseline_implementation_digest") == current_implementation_digest
    )


def record_green(
    cycle: dict,
    *,
    current_epoch: int,
    current_test_contract_digest: str,
    current_oracle_digest: str,
    current_implementation_digest: str,
    diff_digest: str,
    command: Iterable[str],
    environment_digest: str,
    output_digest: str,
    passed: bool,
) -> dict:
    validate_tdd_cycle(cycle)
    if cycle.get("status") not in _BASELINE_STATUSES:
        raise AssuranceError("GREEN requires a current RED/characterization/test-first baseline")
    if current_test_contract_digest != cycle.get("frozen_test_contract_digest"):
        raise AssuranceError("GREEN test contract differs from the frozen baseline contract")
    if current_oracle_digest != cycle.get("frozen_oracle_digest"):
        raise AssuranceError("GREEN oracle differs from the frozen baseline oracle")
    if not isinstance(current_epoch, int) or isinstance(current_epoch, bool) or current_epoch <= int(cycle["baseline_epoch"]):
        raise AssuranceError("GREEN is not bound to a post-baseline implementation epoch")
    if passed is not True:
        raise AssuranceError("a failing or unavailable command cannot establish GREEN")
    implementation = _require_digest(current_implementation_digest, "current implementation digest")
    diff = _require_digest(diff_digest, "diff digest")
    environment = _require_digest(environment_digest, "environment digest")
    output = _require_digest(output_digest, "output digest")
    argv = _require_command(command)
    updated = copy.deepcopy(cycle)
    updated["status"] = "GREEN_PROVEN"
    updated["green_epoch"] = current_epoch
    updated["green_implementation_digest"] = implementation
    updated["green_diff_digest"] = diff
    updated["events"].append(
        _event(
            "GREEN_PROVEN",
            epoch=current_epoch,
            implementation_digest=implementation,
            diff_digest=diff,
            test_contract_digest=current_test_contract_digest,
            oracle_digest=current_oracle_digest,
            command=argv,
            environment_digest=environment,
            output_digest=output,
        )
    )
    return _seal(updated)


def abort_tdd_cycle(cycle: dict, reason: str) -> dict:
    validate_tdd_cycle(cycle)
    if cycle["status"] in {"TDD_CYCLE_ABORTED", "TDD_CYCLE_COMPLETE"}:
        raise AssuranceError("terminal TDD cycle cannot be aborted again")
    if not isinstance(reason, str) or not reason.strip():
        raise AssuranceError("TDD cycle abort requires a reason")
    updated = copy.deepcopy(cycle)
    updated["status"] = "TDD_CYCLE_ABORTED"
    updated["events"].append(_event("TDD_CYCLE_ABORTED", reason=reason.strip()))
    return _seal(updated)


def complete_tdd_cycle(cycle: dict) -> dict:
    validate_tdd_cycle(cycle)
    if cycle["status"] != "GREEN_PROVEN":
        raise AssuranceError("TDD cycle can complete only after GREEN_PROVEN")
    updated = copy.deepcopy(cycle)
    updated["status"] = "TDD_CYCLE_COMPLETE"
    updated["events"].append(_event("TDD_CYCLE_COMPLETE", epoch=updated["green_epoch"]))
    return _seal(updated)


def tdd_cycle_digest(cycle: dict) -> str:
    validate_tdd_cycle(cycle)
    return cycle["cycle_sha256"]


def build_falsification_receipt(
    *,
    task_id: str,
    tdd_cycle_digest: str,
    epoch: int,
    diff_digest: str,
    methods: list[str],
    attempts: list[str],
    boundary_cases: list[str],
    counterexamples: list[str],
    outcome: str,
) -> dict:
    """Build tamper-evident negative evidence bound to one GREEN candidate."""

    if not isinstance(task_id, str) or not _ID_RE.fullmatch(task_id):
        raise AssuranceError("invalid falsification task id")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch <= 0:
        raise AssuranceError("falsification epoch must be a positive integer")
    if outcome not in FALSIFICATION_OUTCOMES:
        raise AssuranceError("invalid falsification outcome")
    found = _require_string_list(counterexamples, "counterexamples", nonempty=False)
    if outcome == "NO_COUNTEREXAMPLE" and found:
        raise AssuranceError("falsification cannot claim NO_COUNTEREXAMPLE when counterexamples exist")
    if outcome == "COUNTEREXAMPLE" and not found:
        raise AssuranceError("COUNTEREXAMPLE outcome requires a recorded counterexample")
    receipt = {
        "schema": 1,
        "task_id": task_id,
        "recorded_at": _now(),
        "tdd_cycle_digest": _require_digest(tdd_cycle_digest, "TDD-cycle digest"),
        "epoch": epoch,
        "diff_digest": _require_digest(diff_digest, "diff digest"),
        "methods": _require_string_list(methods, "falsification methods", nonempty=True),
        "attempts": _require_string_list(attempts, "falsification attempts", nonempty=True),
        "boundary_cases": _require_string_list(boundary_cases, "falsification boundary cases", nonempty=True),
        "counterexamples": found,
        "outcome": outcome,
    }
    receipt["receipt_sha256"] = _digest(receipt, "receipt_sha256")
    return receipt


def validate_falsification_receipt(
    receipt: dict,
    *,
    task_id: str | None = None,
    current_tdd_cycle_digest: str | None = None,
    current_epoch: int | None = None,
    current_diff_digest: str | None = None,
    require_clean: bool = True,
) -> bool:
    """Return whether a falsification receipt is intact and current."""

    if not isinstance(receipt, dict) or receipt.get("schema") not in {1, 2}:
        return False
    if receipt.get("receipt_sha256") != _digest(receipt, "receipt_sha256"):
        return False
    if not isinstance(receipt.get("task_id"), str) or not _ID_RE.fullmatch(receipt["task_id"]):
        return False
    if not isinstance(receipt.get("epoch"), int) or isinstance(receipt.get("epoch"), bool) or receipt["epoch"] <= 0:
        return False
    if any(not isinstance(receipt.get(field), str) or not _SHA256_RE.fullmatch(receipt[field]) for field in ("tdd_cycle_digest", "diff_digest")):
        return False
    if receipt.get("schema") == 1:
        if any(
            not isinstance(receipt.get(field), list)
            or not receipt[field]
            or any(not isinstance(item, str) or not item.strip() for item in receipt[field])
            for field in ("methods", "attempts", "boundary_cases")
        ):
            return False
        counterexamples = receipt.get("counterexamples")
        if not isinstance(counterexamples, list) or any(not isinstance(item, str) or not item.strip() for item in counterexamples):
            return False
    else:
        required = receipt.get("required_families")
        attempts = receipt.get("attempts")
        if (
            not isinstance(required, list)
            or not required
            or required != sorted(set(required))
            or not isinstance(attempts, list)
            or len(attempts) != len(required)
            or [item.get("family") for item in attempts if isinstance(item, dict)] != required
        ):
            return False
        for attempt in attempts:
            if (
                not isinstance(attempt, dict)
                or not isinstance(attempt.get("attempt_id"), str)
                or not isinstance(attempt.get("evidence_id"), str)
                or not attempt["evidence_id"].startswith("E-")
                or not isinstance(attempt.get("evidence_digest"), str)
                or not _SHA256_RE.fullmatch(attempt["evidence_digest"])
                or attempt.get("capability_status") not in FALSIFICATION_CAPABILITY_STATUSES
                or attempt.get("outcome") not in FALSIFICATION_OUTCOMES
                or attempt.get("task_id") != receipt.get("task_id")
                or attempt.get("tdd_cycle_digest") != receipt.get("tdd_cycle_digest")
                or attempt.get("epoch") != receipt.get("epoch")
                or attempt.get("diff_digest") != receipt.get("diff_digest")
            ):
                return False
        if any(item["capability_status"] != "PROVEN" for item in attempts):
            return False
        counterexamples = [item["attempt_id"] for item in attempts if item["outcome"] == "COUNTEREXAMPLE"]
    outcome = receipt.get("outcome")
    if outcome not in FALSIFICATION_OUTCOMES:
        return False
    if (outcome == "NO_COUNTEREXAMPLE") != (not counterexamples):
        return False
    if require_clean and outcome != "NO_COUNTEREXAMPLE":
        return False
    expected = {
        "task_id": task_id,
        "tdd_cycle_digest": current_tdd_cycle_digest,
        "epoch": current_epoch,
        "diff_digest": current_diff_digest,
    }
    return all(value is None or receipt.get(field) == value for field, value in expected.items())


def build_execution_falsification_receipt(
    *,
    task_id: str,
    tdd_cycle_digest: str,
    epoch: int,
    diff_digest: str,
    required_families: list[str],
    attempts: list[dict],
) -> dict:
    """Build the trusted schema only from executed, evidence-bound attempts."""

    required = sorted(set(_require_string_list(required_families, "required falsification families", nonempty=True)))
    selected = sorted((copy.deepcopy(item) for item in attempts), key=lambda item: item.get("family", ""))
    receipt = {
        "schema": 2,
        "task_id": task_id,
        "recorded_at": _now(),
        "tdd_cycle_digest": _require_digest(tdd_cycle_digest, "TDD-cycle digest"),
        "epoch": epoch,
        "diff_digest": _require_digest(diff_digest, "diff digest"),
        "required_families": required,
        "attempts": selected,
        "outcome": "COUNTEREXAMPLE" if any(item.get("outcome") == "COUNTEREXAMPLE" for item in selected) else "NO_COUNTEREXAMPLE",
    }
    receipt["receipt_sha256"] = _digest(receipt, "receipt_sha256")
    if not validate_falsification_receipt(
        receipt,
        task_id=task_id,
        current_tdd_cycle_digest=tdd_cycle_digest,
        current_epoch=epoch,
        current_diff_digest=diff_digest,
        require_clean=False,
    ):
        raise AssuranceError("executed falsification attempts do not exactly cover the required families")
    return receipt
