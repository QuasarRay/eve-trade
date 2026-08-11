from __future__ import annotations

"""Framework-controlled operational TDD observation boundary."""

import fnmatch
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import sys
import uuid

from .assurance import (
    TDD_MODES,
    _seal,
    abort_tdd_cycle,
    new_tdd_cycle,
    record_baseline,
    record_baseline_attempt,
    record_green,
    validate_tdd_cycle,
)
from .governance import capture_governance, verify_governance
from .process import ProcessResult, run_process
from .security import is_path_redirect
from .state_store import StateStore
from .workspace import workspace_fingerprint


BASELINE_CLASSIFICATIONS = {
    "EXPECTED_BEHAVIORAL_RED",
    "CHARACTERIZATION_PASS",
    "TEST_FIRST_OBSERVED",
    "HARNESS_FAILURE",
    "NO_RELEVANT_TEST_EXECUTED",
    "CAPABILITY_UNAVAILABLE",
    "TIMEOUT",
    "UNRELATED_FAILURE",
    "TDD_CHRONOLOGY_VIOLATION",
    "FROZEN_CONTRACT_VIOLATION",
}
_EXPECTED_CLASSIFICATION = {
    "RED_REQUIRED": "EXPECTED_BEHAVIORAL_RED",
    "CHARACTERIZATION_REQUIRED": "CHARACTERIZATION_PASS",
    "NON_BEHAVIORAL_TEST_FIRST": "TEST_FIRST_OBSERVED",
}


class TDDRuntimeError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _verify_sealed(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise TDDRuntimeError(f"{label} is missing")
    body = {key: item for key, item in value.items() if key != "digest"}
    if value.get("digest") != _digest(body):
        raise TDDRuntimeError(f"{label} digest is invalid")
    return value


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TDDRuntimeError("TDD contract path must be non-empty")
    text = value.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or ":" in path.parts[0] or ".." in path.parts:
        raise TDDRuntimeError(f"TDD contract path escapes the project: {value!r}")
    if any(marker in text for marker in ("*", "?", "[")):
        raise TDDRuntimeError("explicit TDD contract paths cannot contain globs")
    return path.as_posix()


def _covered(path: str, pattern: str) -> bool:
    candidate = path.replace("\\", "/").casefold().strip("/")
    normalized = pattern.replace("\\", "/").casefold().strip("/")
    if normalized.endswith("/**"):
        prefix = normalized[:-3].rstrip("/")
        return candidate == prefix or candidate.startswith(prefix + "/")
    return fnmatch.fnmatchcase(candidate, normalized)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(_covered(path, pattern) for pattern in patterns)


def _assert_real_path(root: Path, relative: str) -> Path:
    candidate = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.exists() and is_path_redirect(current):
            raise TDDRuntimeError(f"TDD surface contains a redirected path: {relative}")
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise TDDRuntimeError(f"TDD surface escapes the project: {relative}") from exc
    return candidate


def _file_entry(root: Path, relative: str) -> dict:
    path = _assert_real_path(root, relative)
    if not path.is_file() or is_path_redirect(path):
        raise TDDRuntimeError(f"TDD contract file is missing or redirected: {relative}")
    data = path.read_bytes()
    return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def snapshot_explicit(root: Path, paths: list[str], *, label: str) -> dict:
    normalized = sorted({_relative(path) for path in paths})
    entries = [_file_entry(root, relative) for relative in normalized]
    body = {"schema": 1, "label": label, "paths": normalized, "entries": entries}
    body["digest"] = _digest(body)
    return body


def snapshot_patterns(root: Path, patterns: list[str], *, label: str, require_nonempty: bool) -> dict:
    normalized = sorted({str(pattern).replace("\\", "/").casefold().strip("/") for pattern in patterns})
    if not normalized:
        if require_nonempty:
            raise TDDRuntimeError(f"{label} has no compiled paths")
        body = {"schema": 1, "label": label, "patterns": [], "entries": []}
        body["digest"] = _digest(body)
        return body
    entries: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        if relative == ".aegis" or relative.startswith(".aegis/"):
            continue
        if not _matches_any(relative, normalized):
            continue
        if is_path_redirect(path):
            raise TDDRuntimeError(f"{label} contains a redirected path: {relative}")
        if path.is_file():
            entries.append(_file_entry(root, relative))
    if require_nonempty and not entries:
        raise TDDRuntimeError(f"{label} resolved to no files")
    body = {"schema": 1, "label": label, "patterns": normalized, "entries": entries}
    body["digest"] = _digest(body)
    return body


def _task_cycle(task: dict) -> dict:
    tdd = task.get("tdd", {})
    active = tdd.get("active_cycle_id")
    cycle = next((item for item in tdd.get("cycles", []) if item.get("cycle_id") == active), None)
    if not isinstance(cycle, dict):
        raise TDDRuntimeError("task has no active TDD cycle")
    validate_tdd_cycle(cycle)
    return cycle


def _contract(task: dict) -> tuple[dict, dict, str]:
    precheck = task.get("precheck", {})
    compiled = _verify_sealed(precheck.get("compiled_policy"), "compiled policy")
    scope = _verify_sealed(precheck.get("write_scope"), "compiled write scope")
    mode = compiled.get("task", {}).get("tdd_mode")
    if mode not in TDD_MODES:
        raise TDDRuntimeError("compiled policy has no supported TDD mode")
    if scope.get("governance_digest") != precheck.get("governance_snapshot", {}).get("digest"):
        raise TDDRuntimeError("write scope and governance snapshot are not bound")
    return compiled, scope, mode


def _current_snapshots(root: Path, cycle: dict) -> dict:
    surfaces = cycle.get("surface_contract")
    if not isinstance(surfaces, dict):
        raise TDDRuntimeError("TDD cycle has no compiled surface contract")
    return {
        "production": snapshot_patterns(
            root,
            list(surfaces.get("production_patterns", [])),
            label="production",
            require_nonempty=True,
        ),
        "tests": snapshot_explicit(root, list(surfaces.get("test_paths", [])), label="test-contract"),
        "oracles": snapshot_explicit(root, list(surfaces.get("oracle_paths", [])), label="oracle"),
        "user_dirty": snapshot_patterns(
            root,
            list(surfaces.get("user_dirty_patterns", [])),
            label="user-dirty",
            require_nonempty=False,
        ),
    }


def _environment_identity(adapter: dict) -> dict:
    observer = Path(__file__).with_name("unittest_observer.py")
    body = {
        "schema": 1,
        "adapter": adapter,
        "observer_sha256": hashlib.sha256(observer.read_bytes()).hexdigest(),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "os_name": os.name,
    }
    body["digest"] = _digest(body)
    return body


def _observer_command(root: Path, adapter: dict) -> list[str]:
    if adapter.get("kind") != "unittest":
        raise TDDRuntimeError("no trusted observer is installed for the declared adapter")
    return [
        str(Path(sys.executable).resolve()),
        "-B",
        str(Path(__file__).with_name("unittest_observer.py").resolve()),
        "--root",
        str(root),
        "--test-id",
        adapter["test_id"],
    ]


def _observer_payload(result: ProcessResult) -> dict | None:
    try:
        value = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("adapter") != "unittest":
        return None
    return value


def _baseline_classification(mode: str, result: ProcessResult, payload: dict | None) -> str:
    if result.timed_out:
        return "TIMEOUT"
    if payload is None or payload.get("observer_failure") or any(
        kind != "SELECTOR_NOT_FOUND" for kind in payload.get("error_kinds", [])
    ):
        return "HARNESS_FAILURE"
    if payload.get("relevant_collected") is not True or payload.get("relevant_started") is not True:
        return "NO_RELEVANT_TEST_EXECUTED"
    if payload.get("relevant_errored") is True or payload.get("relevant_skipped") is True:
        return "HARNESS_FAILURE"
    if mode == "RED_REQUIRED" and (
        result.returncode == 1
        and payload.get("relevant_failed") is True
        and payload.get("relevant_completed") is True
    ):
        return "EXPECTED_BEHAVIORAL_RED"
    if mode == "CHARACTERIZATION_REQUIRED" and result.returncode == 0 and payload.get("relevant_completed") is True:
        return "CHARACTERIZATION_PASS"
    if mode == "NON_BEHAVIORAL_TEST_FIRST" and result.returncode == 0 and payload.get("relevant_completed") is True:
        return "TEST_FIRST_OBSERVED"
    return "UNRELATED_FAILURE"


def _green_classification(result: ProcessResult, payload: dict | None) -> str:
    if result.timed_out:
        return "TIMEOUT"
    if payload is None or payload.get("observer_failure") or any(
        kind != "SELECTOR_NOT_FOUND" for kind in payload.get("error_kinds", [])
    ):
        return "HARNESS_FAILURE"
    if payload.get("relevant_collected") is not True or payload.get("relevant_started") is not True:
        return "NO_RELEVANT_TEST_EXECUTED"
    if (
        result.returncode == 0
        and payload.get("relevant_completed") is True
        and payload.get("relevant_failed") is not True
        and payload.get("relevant_errored") is not True
        and payload.get("relevant_skipped") is not True
    ):
        return "GREEN"
    return "UNRELATED_FAILURE"


def _objective_observation(result: ProcessResult | None, payload: dict | None) -> dict:
    if result is None:
        return {
            "returncode": None,
            "timed_out": False,
            "relevant_test_id": None,
            "relevant_test_executed": False,
            "started": False,
            "assertion_boundary_reached": False,
        }
    return {
        "argv": list(result.argv),
        "cwd": result.cwd,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.duration_seconds,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "stdout_bytes": result.stdout_bytes,
        "stderr_bytes": result.stderr_bytes,
        "relevant_test_id": payload.get("requested_test_id") if payload else None,
        "relevant_test_executed": bool(payload and payload.get("relevant_started") and payload.get("relevant_completed")),
        "started": bool(payload and payload.get("relevant_started")),
        "assertion_boundary_reached": bool(payload and payload.get("relevant_failed")),
        "observer": payload,
    }


def _command_details(result: ProcessResult | None, *, success: bool) -> dict | None:
    if result is None:
        return None
    return {
        "argv": list(result.argv),
        "cwd": result.cwd,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.duration_seconds,
        "exit_code": result.returncode,
        "timed_out": result.timed_out,
        "success": success,
        "stdout_sha256": result.stdout_sha256,
        "stderr_sha256": result.stderr_sha256,
        "stdout_bytes": result.stdout_bytes,
        "stderr_bytes": result.stderr_bytes,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
    }


def design(
    root: Path,
    *,
    adapter_kind: str,
    test_id: str,
    test_paths: list[str],
    oracle_paths: list[str],
    task_id: str | None = None,
) -> dict:
    project = Path(root).resolve(strict=True)
    store = StateStore(project)
    task = store.load(task_id)
    if task.get("state") != "TEST_DESIGN":
        raise TDDRuntimeError("tdd design requires task state TEST_DESIGN")
    compiled, scope, mode = _contract(task)
    if adapter_kind != "unittest" or not isinstance(test_id, str) or not test_id.strip():
        raise TDDRuntimeError("trusted TDD currently requires a non-empty unittest test id")
    normalized_tests = sorted({_relative(path) for path in test_paths})
    normalized_oracles = sorted({_relative(path) for path in oracle_paths})
    if not normalized_tests:
        raise TDDRuntimeError("TDD design requires at least one test-contract path")
    for relative in normalized_tests:
        if not _matches_any(relative, list(scope.get("test_paths", []))):
            raise TDDRuntimeError(f"test contract is outside compiled test scope: {relative}")
        if _matches_any(relative, list(scope.get("production_paths", []))):
            raise TDDRuntimeError(f"test contract overlaps compiled production scope: {relative}")
    for relative in normalized_oracles:
        if not (
            _matches_any(relative, list(scope.get("test_paths", [])))
            or _matches_any(relative, list(scope.get("reference_paths", [])))
        ):
            raise TDDRuntimeError(f"oracle is outside compiled test/reference scope: {relative}")
    governance = task.get("precheck", {}).get("governance_snapshot")
    verify_governance(project, governance)
    adapter = {"kind": adapter_kind, "test_id": test_id.strip()}
    production = snapshot_patterns(
        project,
        list(scope.get("production_paths", [])),
        label="production",
        require_nonempty=True,
    )
    tests = snapshot_explicit(project, normalized_tests, label="test-contract")
    oracles = snapshot_explicit(project, normalized_oracles, label="oracle")
    user_dirty = snapshot_patterns(
        project,
        list(scope.get("user_dirty", [])),
        label="user-dirty",
        require_nonempty=False,
    )
    environment = _environment_identity(adapter)
    cycle = new_tdd_cycle(
        task_id=task["id"],
        cycle_id=f"tdd-{task['change_epoch']}-{uuid.uuid4().hex[:12]}",
        mode=mode,
        designed_at_revision=task["revision"],
        test_contract_digest=_digest({"adapter": adapter, "snapshot": tests}),
        oracle_digest=oracles["digest"],
        remediation=task["change_epoch"] > 0,
        discovered_epoch=task["change_epoch"] if task["change_epoch"] > 0 else None,
    )
    cycle.update(
        authority_source="FRAMEWORK_OBSERVED",
        adapter=adapter,
        adapter_digest=_digest({"adapter": adapter, "environment": environment}),
        environment_identity=environment,
        compiled_contract_digest=compiled["digest"],
        write_scope_digest=scope["digest"],
        governance_digest=governance["digest"],
        design_workspace_digest=workspace_fingerprint(project).get("sha256"),
        surface_contract={
            "production_patterns": list(scope.get("production_paths", [])),
            "test_paths": normalized_tests,
            "oracle_paths": normalized_oracles,
            "user_dirty_patterns": list(scope.get("user_dirty", [])),
            "generated_patterns": list(scope.get("generated_paths", [])),
        },
        design_snapshots={
            "production": production,
            "tests": tests,
            "oracles": oracles,
            "user_dirty": user_dirty,
        },
        baseline_evidence_id=None,
        green_evidence_id=None,
    )
    cycle = _seal(cycle)
    updated = store.record_tdd_cycle(cycle, task["id"])
    return {
        "task_id": task["id"],
        "cycle_id": cycle["cycle_id"],
        "status": cycle["status"],
        "mode": mode,
        "test_contract_digest": cycle["test_contract_digest"],
        "oracle_digest": cycle["oracle_digest"],
        "production_snapshot_digest": production["digest"],
        "adapter_digest": cycle["adapter_digest"],
        "task_revision": updated["revision"],
    }


def _commit_attempt(
    store: StateStore,
    task: dict,
    cycle: dict,
    *,
    classification: str,
    detail: str,
    result: ProcessResult | None,
    payload: dict | None,
    snapshots: dict,
) -> tuple[dict, dict, dict]:
    objective = _objective_observation(result, payload)
    evidence_details = {
        "task_id": task["id"],
        "task_revision": task["revision"],
        "change_epoch": task["change_epoch"],
        "tdd_cycle_id": cycle["cycle_id"],
        "operation": "baseline",
        "classification": classification,
        "command": _command_details(result, success=False),
        "observation": objective,
        "snapshots": snapshots,
        "workspace": workspace_fingerprint(store.root),
    }

    def build(record: dict) -> dict:
        attempted = record_baseline_attempt(
            cycle,
            classification=classification,
            evidence_id=record["id"],
            evidence_digest=record["record_sha256"],
            detail=detail,
        )
        attempted["authority_source"] = "FRAMEWORK_OBSERVED"
        return _seal(attempted)

    return store._record_framework_tdd_observation(
        build,
        task_id=task["id"],
        kind="baseline-test",
        summary=f"TDD baseline rejected: {classification}",
        provenance="framework-command" if result is not None else "verified-observation",
        evidence_details=evidence_details,
    )


def baseline(
    root: Path,
    *,
    semantic_reason: str,
    timeout: float,
    task_id: str | None = None,
) -> tuple[dict, bool]:
    project = Path(root).resolve(strict=True)
    store = StateStore(project)
    task = store.load(task_id)
    if task.get("state") != "TEST_DESIGN":
        raise TDDRuntimeError("tdd baseline requires task state TEST_DESIGN")
    if not isinstance(semantic_reason, str) or not semantic_reason.strip():
        raise TDDRuntimeError("baseline requires a non-empty semantic interpretation")
    cycle = _task_cycle(task)
    if cycle.get("authority_source") != "FRAMEWORK_OBSERVED" or cycle.get("status") != "TEST_DESIGNED":
        raise TDDRuntimeError("baseline requires a current framework-designed TDD cycle")
    verify_governance(project, task.get("precheck", {}).get("governance_snapshot"))
    before = _current_snapshots(project, cycle)
    designed = cycle["design_snapshots"]
    if before["production"]["digest"] != designed["production"]["digest"]:
        classification = "TDD_CHRONOLOGY_VIOLATION"
        updated, evidence, _ = _commit_attempt(
            store,
            task,
            cycle,
            classification=classification,
            detail="compiled production changed after TEST_DESIGN and before baseline execution",
            result=None,
            payload=None,
            snapshots={"design": designed, "before": before},
        )
        return {
            "task_id": task["id"],
            "classification": classification,
            "implementation_authorized": False,
            "evidence_id": evidence["id"],
            "observation": _objective_observation(None, None),
        }, False
    if (
        before["tests"]["digest"] != designed["tests"]["digest"]
        or before["oracles"]["digest"] != designed["oracles"]["digest"]
    ):
        classification = "FROZEN_CONTRACT_VIOLATION"
        _, evidence, _ = _commit_attempt(
            store,
            task,
            cycle,
            classification=classification,
            detail="test/oracle contract changed after TEST_DESIGN; a new cycle is required",
            result=None,
            payload=None,
            snapshots={"design": designed, "before": before},
        )
        return {
            "task_id": task["id"],
            "classification": classification,
            "implementation_authorized": False,
            "evidence_id": evidence["id"],
            "observation": _objective_observation(None, None),
        }, False
    command = _observer_command(project, cycle["adapter"])
    try:
        result = run_process(command, cwd=project, timeout=timeout, capture_limit=256_000)
    except (FileNotFoundError, OSError):
        result = None
        payload = None
        classification = "CAPABILITY_UNAVAILABLE"
    else:
        payload = _observer_payload(result)
        classification = _baseline_classification(cycle["mode"], result, payload)
    after = _current_snapshots(project, cycle)
    if after["production"]["digest"] != designed["production"]["digest"]:
        classification = "TDD_CHRONOLOGY_VIOLATION"
    elif (
        after["tests"]["digest"] != designed["tests"]["digest"]
        or after["oracles"]["digest"] != designed["oracles"]["digest"]
    ):
        classification = "FROZEN_CONTRACT_VIOLATION"
    expected = _EXPECTED_CLASSIFICATION[cycle["mode"]]
    objective = _objective_observation(result, payload)
    evidence_details = {
        "task_id": task["id"],
        "task_revision": task["revision"],
        "change_epoch": task["change_epoch"],
        "tdd_cycle_id": cycle["cycle_id"],
        "designed_cycle_digest": cycle["cycle_sha256"],
        "operation": "baseline",
        "classification": classification,
        "command": _command_details(result, success=classification == expected),
        "observation": objective,
        "snapshots": {"design": designed, "before": before, "after": after},
        "workspace": workspace_fingerprint(project),
        "environment_identity": cycle["environment_identity"],
    }
    if classification != expected:
        _, evidence, _ = _commit_attempt(
            store,
            task,
            cycle,
            classification=classification,
            detail="baseline execution did not establish the mode-required observation",
            result=result,
            payload=payload,
            snapshots={"design": designed, "before": before, "after": after},
        )
        return {
            "task_id": task["id"],
            "classification": classification,
            "implementation_authorized": False,
            "evidence_id": evidence["id"],
            "observation": objective,
        }, False

    def build(record: dict) -> dict:
        observed = record_baseline(
            cycle,
            outcome=TDD_MODES[cycle["mode"]][0],
            observed_implementation_digest=before["production"]["digest"],
            command=result.argv,
            environment_digest=cycle["environment_identity"]["digest"],
            output_digest=_digest(
                {
                    "stdout": result.stdout_sha256,
                    "stderr": result.stderr_sha256,
                    "observer": payload,
                }
            ),
            semantic_reason=semantic_reason,
            harness_valid=True,
            baseline_intact=True,
        )
        observed.update(
            authority_source="FRAMEWORK_OBSERVED",
            baseline_evidence_id=record["id"],
            baseline_evidence_digest=record["record_sha256"],
            baseline_classification=classification,
            frozen_adapter_digest=cycle["adapter_digest"],
            baseline_snapshots={"before": before, "after": after},
        )
        for event in observed["events"]:
            if event.get("status") == "BASELINE_EXECUTED":
                event.update(
                    classification=classification,
                    evidence_id=record["id"],
                    evidence_digest=record["record_sha256"],
                    task_revision=task["revision"],
                    change_epoch=task["change_epoch"],
                )
        return _seal(observed)

    updated, evidence, stored_cycle = store._record_framework_tdd_observation(
        build,
        task_id=task["id"],
        kind="baseline-test",
        summary=f"Trusted TDD baseline: {classification}",
        provenance="framework-command",
        evidence_details=evidence_details,
    )
    return {
        "task_id": task["id"],
        "classification": classification,
        "implementation_authorized": bool(updated["tdd"].get("baseline_observed_by_framework")),
        "evidence_id": evidence["id"],
        "cycle_digest": stored_cycle["cycle_sha256"],
        "observation": objective,
    }, True


def green(root: Path, *, timeout: float, task_id: str | None = None) -> tuple[dict, bool]:
    project = Path(root).resolve(strict=True)
    store = StateStore(project)
    task = store.load(task_id)
    if task.get("state") not in {"IMPLEMENT", "REMEDIATE"}:
        raise TDDRuntimeError("tdd green requires task state IMPLEMENT or REMEDIATE")
    cycle = _task_cycle(task)
    if not task.get("tdd", {}).get("baseline_observed_by_framework"):
        raise TDDRuntimeError("GREEN requires a framework-observed baseline")
    verify_governance(project, task.get("precheck", {}).get("governance_snapshot"))
    before = _current_snapshots(project, cycle)
    designed = cycle["design_snapshots"]
    if (
        before["tests"]["digest"] != designed["tests"]["digest"]
        or before["oracles"]["digest"] != designed["oracles"]["digest"]
        or cycle.get("adapter_digest") != cycle.get("frozen_adapter_digest")
    ):
        return {
            "task_id": task["id"],
            "classification": "FROZEN_CONTRACT_VIOLATION",
            "passed": False,
            "request": {"adapter": cycle["adapter"]},
            "observation": _objective_observation(None, None),
        }, False
    command = _observer_command(project, cycle["adapter"])
    try:
        result = run_process(command, cwd=project, timeout=timeout, capture_limit=256_000)
    except (FileNotFoundError, OSError):
        return {
            "task_id": task["id"],
            "classification": "CAPABILITY_UNAVAILABLE",
            "passed": False,
            "request": {"adapter": cycle["adapter"]},
            "observation": _objective_observation(None, None),
        }, False
    payload = _observer_payload(result)
    classification = _green_classification(result, payload)
    after = _current_snapshots(project, cycle)
    if (
        after["tests"]["digest"] != designed["tests"]["digest"]
        or after["oracles"]["digest"] != designed["oracles"]["digest"]
    ):
        classification = "FROZEN_CONTRACT_VIOLATION"
    objective = _objective_observation(result, payload)
    if classification != "GREEN":
        return {
            "task_id": task["id"],
            "classification": classification,
            "passed": False,
            "request": {"adapter": cycle["adapter"]},
            "observation": objective,
        }, False
    if after["production"]["digest"] == cycle["baseline_implementation_digest"]:
        return {
            "task_id": task["id"],
            "classification": "NO_IMPLEMENTATION_CHANGE",
            "passed": False,
            "request": {"adapter": cycle["adapter"]},
            "observation": objective,
        }, False
    workspace = workspace_fingerprint(project)
    if workspace.get("available") is not True:
        raise TDDRuntimeError("GREEN cannot bind an unavailable workspace fingerprint")
    evidence_details = {
        "task_id": task["id"],
        "task_revision": task["revision"],
        "change_epoch": task["change_epoch"],
        "tdd_cycle_id": cycle["cycle_id"],
        "designed_cycle_digest": cycle["cycle_sha256"],
        "operation": "green",
        "classification": classification,
        "command": _command_details(result, success=True),
        "observation": objective,
        "snapshots": {"before": before, "after": after},
        "workspace": workspace,
        "environment_identity": cycle["environment_identity"],
    }

    def build(record: dict) -> dict:
        observed = record_green(
            cycle,
            current_epoch=task["change_epoch"],
            current_test_contract_digest=cycle["frozen_test_contract_digest"],
            current_oracle_digest=cycle["frozen_oracle_digest"],
            current_implementation_digest=after["production"]["digest"],
            diff_digest=workspace["sha256"],
            command=result.argv,
            environment_digest=cycle["environment_identity"]["digest"],
            output_digest=_digest(
                {
                    "stdout": result.stdout_sha256,
                    "stderr": result.stderr_sha256,
                    "observer": payload,
                }
            ),
            passed=True,
        )
        observed.update(
            authority_source="FRAMEWORK_OBSERVED",
            green_evidence_id=record["id"],
            green_evidence_digest=record["record_sha256"],
            green_classification="GREEN",
            green_snapshots={"before": before, "after": after},
            green_workspace_digest=workspace["sha256"],
        )
        for event in observed["events"]:
            if event.get("status") == "GREEN_PROVEN":
                event.update(
                    classification="GREEN",
                    evidence_id=record["id"],
                    evidence_digest=record["record_sha256"],
                    task_revision=task["revision"],
                    change_epoch=task["change_epoch"],
                )
        return _seal(observed)

    _, evidence, stored_cycle = store._record_framework_tdd_observation(
        build,
        task_id=task["id"],
        kind="test",
        summary="Trusted TDD GREEN",
        provenance="framework-command",
        evidence_details=evidence_details,
    )
    return {
        "task_id": task["id"],
        "classification": "GREEN",
        "passed": True,
        "evidence_id": evidence["id"],
        "cycle_digest": stored_cycle["cycle_sha256"],
        "request": {"adapter": cycle["adapter"]},
        "observation": objective,
    }, True


def abort(root: Path, *, reason: str, task_id: str | None = None) -> dict:
    project = Path(root).resolve(strict=True)
    store = StateStore(project)
    task = store.load(task_id)
    cycle = abort_tdd_cycle(_task_cycle(task), reason)
    cycle["authority_source"] = "FRAMEWORK_OBSERVED"
    cycle = _seal(cycle)
    updated = store.record_tdd_cycle(cycle, task["id"])
    return {"task_id": task["id"], "cycle_id": cycle["cycle_id"], "status": cycle["status"], "task_revision": updated["revision"]}


def implementation_authority_current(root: Path, task: dict) -> bool:
    try:
        cycle = _task_cycle(task)
        if cycle.get("authority_source") != "FRAMEWORK_OBSERVED":
            return False
        if task.get("tdd", {}).get("baseline_observed_by_framework") is not True:
            return False
        current = _current_snapshots(Path(root).resolve(strict=True), cycle)
        designed = cycle["design_snapshots"]
        return bool(
            current["production"]["digest"] == cycle.get("baseline_implementation_digest") == designed["production"]["digest"]
            and current["tests"]["digest"] == designed["tests"]["digest"]
            and current["oracles"]["digest"] == designed["oracles"]["digest"]
            and cycle.get("adapter_digest") == cycle.get("frozen_adapter_digest")
        )
    except (RuntimeError, OSError, KeyError, TypeError):
        return False


def green_current(root: Path, task: dict) -> bool:
    """Recompute whether GREEN still describes the current candidate."""

    try:
        cycle = _task_cycle(task)
        if (
            cycle.get("authority_source") != "FRAMEWORK_OBSERVED"
            or cycle.get("status") not in {"GREEN_PROVEN", "TDD_CYCLE_COMPLETE"}
            or task.get("tdd", {}).get("green_observed_by_framework") is not True
            or cycle.get("green_epoch") != task.get("change_epoch")
        ):
            return False
        current = _current_snapshots(Path(root).resolve(strict=True), cycle)
        designed = cycle["design_snapshots"]
        workspace = workspace_fingerprint(Path(root).resolve(strict=True))
        return bool(
            workspace.get("available") is True
            and current["production"]["digest"] == cycle.get("green_implementation_digest") == task.get("implementation_digest")
            and current["tests"]["digest"] == designed["tests"]["digest"]
            and current["oracles"]["digest"] == designed["oracles"]["digest"]
            and workspace.get("sha256") == cycle.get("green_diff_digest") == task.get("diff_digest")
        )
    except (RuntimeError, OSError, KeyError, TypeError):
        return False


def status(root: Path, *, task_id: str | None = None) -> dict:
    project = Path(root).resolve(strict=True)
    task = StateStore(project).load(task_id)
    cycle = _task_cycle(task)
    return {
        "task_id": task["id"],
        "task_state": task["state"],
        "task_revision": task["revision"],
        "change_epoch": task["change_epoch"],
        "implementation_authorized": implementation_authority_current(project, task),
        "cycle": cycle,
    }
