"""Atomic run index and portable latest-pointer helpers."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INDEX_SCHEMA_VERSION = "o11y.run-index.v1"
TERMINAL_COMPLETE_STATUSES = {"COMPLETE", "SOURCE_CHANGED_DURING_RUN"}


def index_path(root: Path) -> Path:
    return root.resolve() / ".o11y" / "index.json"


def latest_pointer_path(root: Path) -> Path:
    return root.resolve() / ".o11y" / "runs" / "latest-local.txt"


def default_index() -> dict[str, Any]:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "updated_at": "",
        "latest_started_run_id": "",
        "latest_completed_run_id": "",
        "runs": [],
    }


def load_index(root: Path) -> tuple[dict[str, Any], list[str]]:
    path = index_path(root)
    if not path.exists():
        return default_index(), []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return default_index(), [f"malformed index ignored: {type(exc).__name__}: {exc}"]
    if not isinstance(value, dict) or not isinstance(value.get("runs"), list):
        return default_index(), ["malformed index ignored: missing runs list"]
    value.setdefault("schema_version", INDEX_SCHEMA_VERSION)
    value.setdefault("updated_at", "")
    value.setdefault("latest_started_run_id", "")
    value.setdefault("latest_completed_run_id", "")
    return value, []


def update_run_index(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    root = root.resolve()
    idx, errors = load_index(root)
    record = normalize_record(root, record)
    records = [item for item in idx.get("runs", []) if item.get("run_id") != record["run_id"]]
    records.append(record)
    records.sort(key=lambda item: (str(item.get("run_started_at", "")), str(item.get("run_id", ""))))
    idx = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "latest_started_run_id": records[-1]["run_id"] if records else "",
        "latest_completed_run_id": _latest_completed(records),
        "runs": records,
    }
    if errors:
        idx["index_warnings"] = errors
    _atomic_write_json(index_path(root), idx)
    if record.get("run_status") in TERMINAL_COMPLETE_STATUSES and idx["latest_completed_run_id"] == record["run_id"]:
        write_latest_pointer(root, record["run_id"])
    return idx


def normalize_record(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    run_id = str(record["run_id"])
    run_path = str(record.get("run_path") or f".o11y/runs/{run_id}").replace("\\", "/")
    if Path(run_path).is_absolute():
        try:
            run_path = Path(run_path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            run_path = f".o11y/runs/{run_id}"
    value = {
        "run_id": run_id,
        "run_path": run_path,
        "run_status": str(record.get("run_status", "UNKNOWN")),
        "run_started_at": str(record.get("run_started_at", "")),
        "run_finished_at": str(record.get("run_finished_at", "")),
        "full_head_sha": str(record.get("full_head_sha", "")),
        "short_head_sha": str(record.get("short_head_sha", "")),
        "branch": str(record.get("branch", "")),
        "worktree_dirty": bool(record.get("worktree_dirty", False)),
        "ci_or_local": str(record.get("ci_or_local", "")),
        "command": str(record.get("command", "")),
        "exit_code": int(record.get("exit_code", 0) or 0),
        "diagnosis_path": str(record.get("diagnosis_path", "")),
        "report_path": str(record.get("report_path", "")),
    }
    return value


def record_from_provenance(
    root: Path,
    provenance: dict[str, Any],
    *,
    command: str = "",
    exit_code: int = 0,
    diagnosis_path: str = "",
    report_path: str = "",
) -> dict[str, Any]:
    run_id = str(provenance.get("run_id", ""))
    return normalize_record(
        root,
        {
            "run_id": run_id,
            "run_path": f".o11y/runs/{run_id}",
            "run_status": provenance.get("run_status", "UNKNOWN"),
            "run_started_at": provenance.get("run_started_at", ""),
            "run_finished_at": provenance.get("run_finished_at", ""),
            "full_head_sha": provenance.get("full_head_sha", ""),
            "short_head_sha": provenance.get("short_head_sha", ""),
            "branch": provenance.get("branch", ""),
            "worktree_dirty": provenance.get("worktree_dirty", False),
            "ci_or_local": provenance.get("ci_or_local", ""),
            "command": command,
            "exit_code": exit_code,
            "diagnosis_path": diagnosis_path,
            "report_path": report_path,
        },
    )


def write_latest_pointer(root: Path, run_id: str) -> None:
    pointer = latest_pointer_path(root)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(pointer, run_id.strip() + "\n")


def read_latest_pointer(root: Path) -> tuple[str, list[str]]:
    pointer = latest_pointer_path(root)
    if not pointer.exists():
        return "", ["latest pointer is missing"]
    try:
        value = pointer.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return "", [f"latest pointer unreadable: {exc}"]
    if not value:
        return "", ["latest pointer is empty"]
    if Path(value).is_absolute() or ":" in value.replace("\\", "/").split("/", 1)[0]:
        return "", [f"latest pointer is machine-specific and ignored: {value}"]
    run_id = Path(value.replace("\\", "/")).name
    run_dir = root.resolve() / ".o11y" / "runs" / run_id
    if not run_dir.exists():
        return run_id, [f"latest pointer target does not exist: {run_id}"]
    return run_id, []


def latest_completed_record(root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    idx, errors = load_index(root)
    run_id = str(idx.get("latest_completed_run_id") or "")
    if not run_id:
        pointer_id, pointer_errors = read_latest_pointer(root)
        errors.extend(pointer_errors)
        run_id = pointer_id
    if not run_id:
        return None, errors
    for record in idx.get("runs", []):
        if record.get("run_id") == run_id:
            return record, errors
    run_dir = root.resolve() / ".o11y" / "runs" / run_id
    if run_dir.exists():
        return {"run_id": run_id, "run_path": f".o11y/runs/{run_id}", "run_status": "UNKNOWN"}, errors
    errors.append(f"latest completed run not found: {run_id}")
    return None, errors


def find_current_exact_record(root: Path, current_state: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    idx, errors = load_index(root)
    sha = str(current_state.get("full_head_sha", ""))
    dirty = bool(current_state.get("worktree_dirty", False))
    candidates = [
        record
        for record in idx.get("runs", [])
        if record.get("run_status") in TERMINAL_COMPLETE_STATUSES
        and record.get("full_head_sha") == sha
        and bool(record.get("worktree_dirty", False)) == dirty
    ]
    candidates.sort(key=lambda item: (str(item.get("run_started_at", "")), str(item.get("run_id", ""))))
    return (candidates[-1] if candidates else None), errors


def _latest_completed(records: list[dict[str, Any]]) -> str:
    completed = [item for item in records if item.get("run_status") in TERMINAL_COMPLETE_STATUSES]
    return completed[-1]["run_id"] if completed else ""


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)
