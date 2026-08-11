from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Iterable

from .atomic import atomic_write_bytes, atomic_write_json
from .locks import FileLock
from .process import ProcessResult, run_process
from .security import SECRET_NAME, is_path_redirect, redact_mapping, redact_text
from .workspace import workspace_fingerprint


EVIDENCE_KINDS = {
    "baseline-test",
    "command",
    "verification-command",
    "observation",
    "external-source",
    "decision",
    "recovery",
    "mutation",
    "audit",
    "test",
}
PROVENANCE_TYPES = {"manual", "framework-command", "verified-observation", "external-source"}
_FRAMEWORK_PROVENANCE_TOKEN = object()
_TERMINAL_TASK_STATES = {"FAILED", "FINALIZE", "CANCELLED", "ABANDONED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _redact(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in redact_mapping(value).items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _validate_record(record: dict, *, task_id: str, line_no: int, previous: str | None, sequence: int) -> None:
    if not isinstance(record, dict):
        raise RuntimeError(f"evidence schema failure at line {line_no}: record is not an object")
    claimed = record.get("record_sha256")
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    if claimed != hashlib.sha256(_canonical(body)).hexdigest():
        raise RuntimeError(f"evidence integrity failure at line {line_no}")
    if body.get("previous_sha256") != previous:
        raise RuntimeError(f"evidence chain failure at line {line_no}")
    schema = body.get("schema", 1)
    required = {"id", "at", "kind", "summary", "details", "previous_sha256"}
    missing = sorted(required - set(body))
    if missing:
        raise RuntimeError(f"evidence schema failure at line {line_no}: missing {', '.join(missing)}")
    if not isinstance(body["id"], str) or not body["id"].startswith("E-"):
        raise RuntimeError(f"evidence schema failure at line {line_no}: invalid id")
    if not isinstance(body["summary"], str) or not body["summary"].strip():
        raise RuntimeError(f"evidence schema failure at line {line_no}: empty summary")
    if not isinstance(body["details"], dict):
        raise RuntimeError(f"evidence schema failure at line {line_no}: details is not an object")
    if schema == 1:
        return
    if schema != 2:
        raise RuntimeError(f"evidence schema failure at line {line_no}: unsupported schema {schema!r}")
    if body.get("task_id") != task_id:
        raise RuntimeError(f"evidence task mismatch at line {line_no}")
    if body.get("sequence") != sequence:
        raise RuntimeError(f"evidence sequence failure at line {line_no}")
    if body.get("kind") not in EVIDENCE_KINDS:
        raise RuntimeError(f"evidence schema failure at line {line_no}: invalid kind")
    if body.get("provenance") not in PROVENANCE_TYPES:
        raise RuntimeError(f"evidence schema failure at line {line_no}: invalid provenance")
    epoch = body.get("change_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise RuntimeError(f"evidence schema failure at line {line_no}: invalid epoch")


def _anchor_digest(anchor: dict) -> str:
    return _sha({key: value for key, value in anchor.items() if key != "anchor_sha256"})


def _load_anchor(task_dir: Path) -> dict | None:
    path = task_dir / "evidence-head.json"
    if not path.exists():
        return None
    try:
        anchor = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid evidence anchor: {exc}") from exc
    if (
        not isinstance(anchor, dict)
        or anchor.get("schema") != 1
        or anchor.get("task_id") != task_dir.name
        or anchor.get("anchor_sha256") != _anchor_digest(anchor)
    ):
        raise RuntimeError("evidence anchor integrity failure")
    return anchor


def evidence_lock(task_dir: Path) -> FileLock:
    """Return the single lock governing the ledger and its durable head anchor."""
    return FileLock(task_dir / ".evidence.lock", "evidence-ledger")


def load_evidence(task_dir: Path, *, verify: bool = True) -> list[dict]:
    path = task_dir / "evidence.jsonl"
    if not path.exists():
        anchor = _load_anchor(task_dir)
        if anchor and anchor.get("count"):
            raise RuntimeError("evidence anchor references missing ledger")
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise RuntimeError("evidence ledger has a truncated tail")
    out: list[dict] = []
    previous = None
    ids: set[str] = set()
    for line_no, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise RuntimeError(f"evidence schema failure at line {line_no}: blank records are forbidden")
        try:
            record = json.loads(line)
        except Exception as exc:
            raise RuntimeError(f"evidence JSON failure at line {line_no}: {exc}") from exc
        if verify:
            _validate_record(record, task_id=task_dir.name, line_no=line_no, previous=previous, sequence=line_no)
        if record.get("id") in ids:
            raise RuntimeError(f"duplicate evidence id at line {line_no}: {record.get('id')}")
        ids.add(record.get("id"))
        previous = record.get("record_sha256")
        out.append(record)
    if verify:
        anchor = _load_anchor(task_dir)
        if anchor is not None and (anchor.get("count") != len(out) or anchor.get("head_sha256") != previous):
            raise RuntimeError("evidence ledger does not match task evidence anchor")
    return out


def _tail_record(path: Path) -> dict | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        if size == 0:
            return None
        stream.seek(-1, os.SEEK_END)
        if stream.read(1) != b"\n":
            raise RuntimeError("evidence ledger has a truncated tail")
        # Scan backwards in bounded blocks.  The previous byte-at-a-time loop
        # made append latency proportional to the final record size in filesystem
        # operations; captured-output evidence can legitimately be tens of KiB.
        end = size - 1  # Exclude the required final newline.
        pieces: list[bytes] = []
        while end > 0:
            start = max(0, end - 64 * 1024)
            stream.seek(start)
            block = stream.read(end - start)
            newline = block.rfind(b"\n")
            if newline >= 0:
                pieces.append(block[newline + 1 :])
                break
            pieces.append(block)
            end = start
        line = b"".join(reversed(pieces))
    if not line.strip():
        raise RuntimeError("evidence ledger has a blank tail record")
    try:
        return json.loads(line)
    except Exception as exc:
        raise RuntimeError(f"evidence JSON failure at tail: {exc}") from exc


def _recover_stale_anchor(task_dir: Path, anchor: dict, path: Path) -> dict:
    """Advance an anchor only across valid append-only records left by a crash."""
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise RuntimeError("evidence ledger has a truncated tail")
    previous = None
    records = []
    for line_no, line in enumerate(raw.splitlines(), 1):
        record = json.loads(line)
        _validate_record(record, task_id=task_dir.name, line_no=line_no, previous=previous, sequence=line_no)
        previous = record["record_sha256"]
        records.append(record)
    count = int(anchor.get("count", 0))
    if count > len(records):
        raise RuntimeError("evidence ledger was truncated behind its anchor")
    anchored_head = records[count - 1]["record_sha256"] if count else None
    if anchored_head != anchor.get("head_sha256"):
        raise RuntimeError("evidence history before the durable anchor was rewritten")
    if count == len(records):
        return anchor
    repaired = {
        "schema": 1,
        "task_id": task_dir.name,
        "count": len(records),
        "head_sha256": records[-1]["record_sha256"],
        "task_revision": records[-1].get("details", {}).get("task_revision"),
        "recovered_append_count": len(records) - count,
    }
    repaired["anchor_sha256"] = _anchor_digest(repaired)
    atomic_write_json(task_dir / "evidence-head.json", repaired, mode=0o600)
    return repaired


def _redact_argv(argv: Iterable[str]) -> list[str]:
    values = list(argv)
    out: list[str] = []
    redact_next = False
    for value in values:
        if redact_next:
            out.append("[REDACTED]")
            redact_next = False
            continue
        if "=" in value:
            key, supplied = value.split("=", 1)
            if SECRET_NAME.search(key.lstrip("-")):
                out.append(key + "=[REDACTED]")
                continue
        out.append(redact_text(value))
        if value.startswith("-") and SECRET_NAME.search(value.lstrip("-")):
            redact_next = True
    return out


def append_evidence(
    task_dir: Path,
    kind: str,
    summary: str,
    *,
    provenance: str = "manual",
    evidence_id: str | None = None,
    task_id: str | None = None,
    lock_held: bool = False,
    _provenance_token: object | None = None,
    **details,
) -> dict:
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind: {kind!r}")
    if provenance not in PROVENANCE_TYPES:
        raise ValueError(f"unknown evidence provenance: {provenance!r}")
    if provenance != "manual" and _provenance_token is not _FRAMEWORK_PROVENANCE_TOKEN:
        raise ValueError("non-manual evidence provenance can be minted only by a framework observation boundary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("evidence summary must not be empty")
    task_id = task_id or task_dir.name
    if task_id != task_dir.name:
        raise ValueError("evidence task id does not match owning task directory")
    epoch = details.get("change_epoch", 0)
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise ValueError("evidence change_epoch must be a non-negative integer")
    task_dir.mkdir(parents=True, exist_ok=True)
    lock = None if lock_held else evidence_lock(task_dir)
    if lock is not None:
        lock.acquire()
    try:
        state_path = task_dir / "state.json"
        if is_path_redirect(state_path):
            raise RuntimeError("evidence task state path is redirected")
        if state_path.exists():
            try:
                task_state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"cannot validate task state before evidence append: {exc}") from exc
            if not isinstance(task_state, dict) or not isinstance(task_state.get("state"), str):
                raise RuntimeError("cannot validate task state before evidence append")
            if task_state["state"] in _TERMINAL_TASK_STATES:
                raise RuntimeError(f"terminal task state {task_state['state']} rejects evidence append")
        path = task_dir / "evidence.jsonl"
        anchor = _load_anchor(task_dir)
        if anchor is None:
            existing = load_evidence(task_dir)
            previous = existing[-1]["record_sha256"] if existing else None
            count = len(existing)
        else:
            tail = _tail_record(path)
            if anchor.get("count") == 0:
                if tail is not None:
                    raise RuntimeError("evidence anchor/ledger count mismatch")
            elif tail is None:
                raise RuntimeError("evidence ledger tail does not match anchor")
            elif tail.get("record_sha256") != anchor.get("head_sha256"):
                anchor = _recover_stale_anchor(task_dir, anchor, path)
            previous = anchor.get("head_sha256")
            count = int(anchor.get("count", 0))
        record = {
            "schema": 2,
            "id": evidence_id or "E-" + uuid.uuid4().hex,
            "sequence": count + 1,
            "task_id": task_id,
            "at": now(),
            "kind": kind,
            "summary": redact_text(summary.strip()),
            "provenance": provenance,
            "change_epoch": epoch,
            "details": _redact(details),
            "previous_sha256": previous,
        }
        body = dict(record)
        record["record_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            written = 0
            while written < len(encoded):
                written += os.write(fd, encoded[written:])
            os.fsync(fd)
        finally:
            os.close(fd)
        new_anchor = {
            "schema": 1,
            "task_id": task_id,
            "count": count + 1,
            "head_sha256": record["record_sha256"],
            "task_revision": details.get("task_revision"),
        }
        new_anchor["anchor_sha256"] = _anchor_digest(new_anchor)
        atomic_write_json(task_dir / "evidence-head.json", new_anchor, mode=0o600)
        return record
    finally:
        if lock is not None:
            lock.release()


def _append_framework_evidence(
    task_dir: Path,
    kind: str,
    summary: str,
    *,
    provenance: str,
    **details,
) -> dict:
    """Private mint for framework-controlled provenance.

    This intentionally remains a private boundary: callers using the general
    append API cannot turn an arbitrary label into verification-grade evidence.
    """

    if provenance not in {"framework-command", "verified-observation", "external-source"}:
        raise ValueError("framework evidence mint requires non-manual provenance")

    return append_evidence(
        task_dir,
        kind,
        summary,
        provenance=provenance,
        _provenance_token=_FRAMEWORK_PROVENANCE_TOKEN,
        **details,
    )


def _append_verified_observation(task_dir: Path, kind: str, summary: str, **details) -> dict:
    return _append_framework_evidence(
        task_dir,
        kind,
        summary,
        provenance="verified-observation",
        **details,
    )


def rollback_last_evidence(task_dir: Path, record_sha256: str, *, lock_held: bool = False) -> None:
    """Remove exactly one just-appended tail record, never arbitrary history.

    The prior anchor is installed before the ledger is shortened.  A crash in
    that narrow interval therefore leaves an extra, recoverable append instead
    of silently accepting a truncated anchored history.
    """
    if not isinstance(record_sha256, str) or len(record_sha256) != 64:
        raise ValueError("rollback requires a canonical evidence record digest")
    task_dir = Path(task_dir)
    lock = None if lock_held else evidence_lock(task_dir)
    if lock is not None:
        lock.acquire()
    try:
        records = load_evidence(task_dir, verify=True)
        if not records or records[-1].get("record_sha256") != record_sha256:
            raise RuntimeError("evidence rollback target is not the verified ledger tail")
        prior = records[:-1]
        prior_head = prior[-1]["record_sha256"] if prior else None
        prior_anchor = {
            "schema": 1,
            "task_id": task_dir.name,
            "count": len(prior),
            "head_sha256": prior_head,
            "task_revision": prior[-1].get("details", {}).get("task_revision") if prior else None,
        }
        prior_anchor["anchor_sha256"] = _anchor_digest(prior_anchor)
        current_anchor = _load_anchor(task_dir)
        if current_anchor is None:
            raise RuntimeError("evidence rollback requires a durable current anchor")
        encoded = b"".join(
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            for record in prior
        )
        atomic_write_json(task_dir / "evidence-head.json", prior_anchor, mode=0o600)
        try:
            atomic_write_bytes(task_dir / "evidence.jsonl", encoded, mode=0o600)
        except BaseException as exc:
            try:
                atomic_write_json(task_dir / "evidence-head.json", current_anchor, mode=0o600)
            except BaseException as restore_exc:
                raise RuntimeError(
                    f"evidence rollback failed and its anchor could not be restored: {restore_exc}"
                ) from exc
            raise
        # Prove that the resulting pair is internally consistent before return.
        verified = load_evidence(task_dir, verify=True)
        if len(verified) != len(prior) or (verified[-1]["record_sha256"] if verified else None) != prior_head:
            raise RuntimeError("evidence rollback postcondition failed")
    finally:
        if lock is not None:
            lock.release()


def execute_command_evidence(
    task_dir: Path,
    *,
    root: Path,
    argv: Iterable[str],
    summary: str,
    change_epoch: int,
    task_revision: int,
    gate_ids: Iterable[str] = (),
    timeout: float = 60.0,
    expected_exit: int = 0,
    lock_held: bool = False,
) -> tuple[dict, ProcessResult]:
    before = workspace_fingerprint(root)
    result = run_process(argv, cwd=root, timeout=timeout, capture_limit=64_000)
    after = workspace_fingerprint(root)
    stable = (
        before.get("available") is True
        and after.get("available") is True
        and before.get("sha256") == after.get("sha256")
    )
    success = not result.timed_out and result.returncode == expected_exit and stable
    record = append_evidence(
        task_dir,
        "verification-command",
        summary,
        provenance="framework-command",
        _provenance_token=_FRAMEWORK_PROVENANCE_TOKEN,
        change_epoch=change_epoch,
        task_revision=task_revision,
        command={
            "argv": _redact_argv(result.argv),
            "cwd": result.cwd,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "duration_seconds": result.duration_seconds,
            "exit_code": result.returncode,
            "expected_exit": expected_exit,
            "timed_out": result.timed_out,
            "success": success,
            "stdout_sha256": result.stdout_sha256,
            "stderr_sha256": result.stderr_sha256,
            "stdout_bytes": result.stdout_bytes,
            "stderr_bytes": result.stderr_bytes,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "stdout_preview": result.stdout,
            "stderr_preview": result.stderr,
        },
        workspace=after,
        workspace_before_sha256=before.get("sha256"),
        workspace_stable=stable,
        gate_ids=sorted(set(gate_ids)),
        lock_held=lock_held,
    )
    return record, result


def verify_evidence(task_dir: Path) -> tuple[bool, str]:
    try:
        records = load_evidence(task_dir, verify=True)
        return True, f"{len(records)} evidence record(s) verified"
    except Exception as exc:
        return False, str(exc)
