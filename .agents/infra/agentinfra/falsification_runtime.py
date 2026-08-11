from __future__ import annotations

"""Execution-backed falsification operations for one current GREEN candidate."""

import json
from pathlib import Path
import uuid

from .assurance import build_execution_falsification_receipt
from .state_store import StateStore
from .tdd_runtime import (
    _command_details,
    _objective_observation,
    _observer_command,
    _observer_payload,
    _task_cycle,
    _verify_sealed,
    green_current,
)
from .process import run_process
from .workspace import workspace_fingerprint


class FalsificationRuntimeError(RuntimeError):
    pass


def _required(task: dict) -> list[str]:
    compiled = _verify_sealed(task.get("precheck", {}).get("compiled_policy"), "compiled policy")
    values = compiled.get("falsification", {}).get("required_families")
    if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
        raise FalsificationRuntimeError("compiled policy has no valid required falsification families")
    if values != sorted(set(values)):
        raise FalsificationRuntimeError("compiled falsification family inventory is not canonical")
    return values


def run_attempt(
    root: Path,
    *,
    family: str,
    adapter_kind: str,
    test_id: str,
    interpretation: str,
    timeout: float,
    task_id: str | None = None,
) -> dict:
    project = Path(root).resolve(strict=True)
    store = StateStore(project)
    task = store.load(task_id)
    if task.get("state") != "FALSIFY":
        raise FalsificationRuntimeError("falsification attempts require task state FALSIFY")
    if not green_current(project, task):
        raise FalsificationRuntimeError("falsification requires current framework-observed GREEN")
    required = _required(task)
    if family not in required:
        raise FalsificationRuntimeError("falsification family is not required/applicable to this compiled task")
    if adapter_kind != "unittest" or not isinstance(test_id, str) or not test_id.strip():
        raise FalsificationRuntimeError("falsification requires a non-empty trusted unittest selector")
    if not isinstance(interpretation, str) or not interpretation.strip():
        raise FalsificationRuntimeError("falsification interpretation must be non-empty")
    cycle = _task_cycle(task)
    adapter = {"kind": adapter_kind, "test_id": test_id.strip()}
    command = _observer_command(project, adapter)
    try:
        result = run_process(command, cwd=project, timeout=timeout, capture_limit=256_000)
    except (FileNotFoundError, OSError):
        result = None
        payload = None
        capability_status = "UNAVAILABLE"
        outcome = "NO_COUNTEREXAMPLE"
    else:
        payload = _observer_payload(result)
        if result.timed_out:
            capability_status = "BLOCKED"
            outcome = "NO_COUNTEREXAMPLE"
        elif payload is None or payload.get("observer_failure") or any(
            kind != "SELECTOR_NOT_FOUND" for kind in payload.get("error_kinds", [])
        ):
            capability_status = "BLOCKED"
            outcome = "NO_COUNTEREXAMPLE"
        elif payload.get("relevant_collected") is not True or payload.get("relevant_started") is not True:
            capability_status = "UNTESTED"
            outcome = "NO_COUNTEREXAMPLE"
        elif result.returncode == 0 and payload.get("relevant_completed") is True:
            capability_status = "PROVEN"
            outcome = "NO_COUNTEREXAMPLE"
        elif result.returncode == 1 and payload.get("relevant_failed") is True and payload.get("relevant_completed") is True:
            capability_status = "PROVEN"
            outcome = "COUNTEREXAMPLE"
        else:
            capability_status = "BLOCKED"
            outcome = "NO_COUNTEREXAMPLE"
    attempt_id = "FA-" + uuid.uuid4().hex
    objective = _objective_observation(result, payload)
    details = {
        "operation": "falsification-attempt",
        "attempt_id": attempt_id,
        "family": family,
        "capability_status": capability_status,
        "outcome": outcome,
        "tdd_cycle_digest": cycle["cycle_sha256"],
        "diff_digest": task["diff_digest"],
        "interpretation": interpretation.strip(),
        "command": _command_details(result, success=capability_status == "PROVEN"),
        "observation": objective,
        "workspace": workspace_fingerprint(project),
    }
    holder: dict[str, dict] = {}

    def bind(state: dict, record: dict) -> None:
        attempt = {
            "schema": 1,
            "attempt_id": attempt_id,
            "family": family,
            "task_id": state["id"],
            "tdd_cycle_digest": cycle["cycle_sha256"],
            "epoch": state["change_epoch"],
            "diff_digest": state["diff_digest"],
            "capability_status": capability_status,
            "outcome": outcome,
            "evidence_id": record["id"],
            "evidence_digest": record["record_sha256"],
            "interpretation": interpretation.strip(),
        }
        holder["attempt"] = attempt
        state.setdefault("falsification_attempts", []).append(attempt)
        state["falsification"] = None
        state["review_receipt"] = None
        if outcome == "COUNTEREXAMPLE":
            state["verification_evidence"] = []
            state["verification_epoch"] = None

    _, evidence = store._record_framework_observation(
        bind,
        task_id=task["id"],
        kind="mutation" if outcome == "COUNTEREXAMPLE" else "test",
        summary=f"Falsification {family}: {outcome}",
        provenance="framework-command",
        evidence_details=details,
    )
    return {
        **holder["attempt"],
        "evidence_id": evidence["id"],
        "observation": objective,
    }


def complete(root: Path, *, task_id: str | None = None) -> dict:
    project = Path(root).resolve(strict=True)
    store = StateStore(project)
    task = store.load(task_id)
    if task.get("state") != "FALSIFY":
        raise FalsificationRuntimeError("falsification completion requires task state FALSIFY")
    if not green_current(project, task):
        raise FalsificationRuntimeError("falsification completion requires current GREEN")
    cycle = _task_cycle(task)
    required = _required(task)
    selected: list[dict] = []
    for family in required:
        matches = [
            item for item in task.get("falsification_attempts", [])
            if item.get("family") == family
            and item.get("task_id") == task["id"]
            and item.get("tdd_cycle_digest") == cycle["cycle_sha256"]
            and item.get("epoch") == task["change_epoch"]
            and item.get("diff_digest") == task["diff_digest"]
        ]
        if not matches:
            raise FalsificationRuntimeError(f"required falsification family has no executed attempt: {family}")
        latest = matches[-1]
        if latest.get("capability_status") != "PROVEN":
            raise FalsificationRuntimeError(
                f"required falsification family is not proven ({latest.get('capability_status')}): {family}"
            )
        selected.append(latest)
    receipt = build_execution_falsification_receipt(
        task_id=task["id"],
        tdd_cycle_digest=cycle["cycle_sha256"],
        epoch=task["change_epoch"],
        diff_digest=task["diff_digest"],
        required_families=required,
        attempts=selected,
    )
    updated = store.record_falsification(receipt, task["id"])
    return {
        "task_id": task["id"],
        "receipt": receipt,
        "task_revision": updated["revision"],
        "request": {},
    }
