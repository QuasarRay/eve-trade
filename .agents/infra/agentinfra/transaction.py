from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Callable, Iterable

from .atomic import atomic_write_bytes, atomic_write_json, durable_unlink
from .locks import FileLock
from .security import confined_path
from .governance import GovernanceViolation, assert_mutation_allowed


class TransactionError(RuntimeError):
    pass


FaultHook = Callable[[str, dict], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


@dataclass(frozen=True)
class Mutation:
    path: Path
    data: bytes | None
    expected_sha256: str | None = None
    expected_exists: bool | None = None
    mode: int | None = None


@dataclass(frozen=True)
class _RecoveryPlan:
    journal_path: Path
    journal: dict
    rollback: bool
    paths: dict[str, Path]
    before: dict[str, bytes | None]
    expected_hashes: dict[str, frozenset[str | None]]


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _fault(hook: FaultHook | None, stage: str, journal: dict) -> None:
    if hook is not None:
        hook(stage, json.loads(json.dumps(journal)))


class FileTransaction:
    """Durable, recoverable multi-file transaction confined to one project root.

    Recovery deterministically restores the pre-transaction state for PREPARED/APPLYING journals and
    verifies the post-state for COMMITTED journals.  It never guesses across destination drift.
    """

    def __init__(
        self,
        root: Path,
        mutations: Iterable[Mutation],
        *,
        state_dir: Path,
        name: str,
        fault: FaultHook | None = None,
    ):
        self.root = root.resolve(strict=True)
        self.mutations = list(mutations)
        try:
            assert_mutation_allowed(self.root, state_dir, operation="transaction journal")
            self.state_dir = confined_path(self.root, state_dir, reject_symlinks=True)
        except GovernanceViolation as exc:
            raise TransactionError(str(exc)) from exc
        self.name = name
        self.fault = fault
        if not self.mutations:
            raise ValueError("transaction requires at least one mutation")

    def _preflight(self) -> tuple[dict, Path]:
        txid = f"{self.name}-{uuid.uuid4().hex}"
        tx_dir = self.state_dir / txid
        records = []
        seen: set[str] = set()
        for index, mutation in enumerate(self.mutations):
            try:
                assert_mutation_allowed(self.root, mutation.path, operation="transaction destination")
                path = confined_path(self.root, mutation.path, reject_symlinks=True)
            except GovernanceViolation as exc:
                raise TransactionError(str(exc)) from exc
            rel = path.relative_to(self.root).as_posix()
            if rel in seen:
                raise TransactionError(f"duplicate transaction destination: {rel}")
            seen.add(rel)
            before = _read(path)
            before_hash = _sha(before)
            if mutation.expected_exists is not None and (before is not None) != mutation.expected_exists:
                raise TransactionError(f"destination existence changed before transaction: {rel}")
            if mutation.expected_sha256 is not None and before_hash != mutation.expected_sha256:
                raise TransactionError(f"destination changed before transaction: {rel}")
            mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode) if path.exists() else mutation.mode
            records.append(
                {
                    "index": index,
                    "path": rel,
                    "before_sha256": before_hash,
                    "after_sha256": _sha(mutation.data),
                    "before_base64": base64.b64encode(before).decode("ascii") if before is not None else None,
                    "mode": mode,
                    "operation": "delete" if mutation.data is None else "replace",
                }
            )
        journal = {
            "schema": 1,
            "id": txid,
            "name": self.name,
            "root": str(self.root),
            "created": _now(),
            "phase": "PREPARED",
            "applied": 0,
            "records": records,
        }
        return journal, tx_dir

    def commit(self, *, retain: bool = True) -> dict:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.state_dir / f".{self.name}.lock", f"transaction:{self.name}")
        lock.acquire()
        journal: dict | None = None
        tx_dir: Path | None = None
        try:
            recover_pending_transactions(self.state_dir, expected_root=self.root, name=self.name, remove_recovered=True)
            journal, tx_dir = self._preflight()
            tx_dir.mkdir(parents=False, exist_ok=False)
            journal_path = tx_dir / "journal.json"
            _fault(self.fault, "before_journal", journal)
            atomic_write_json(journal_path, journal, mode=0o600, root=self.root)
            _fault(self.fault, "after_journal", journal)
            journal["phase"] = "APPLYING"
            atomic_write_json(journal_path, journal, mode=0o600, root=self.root)
            for index, (mutation, record) in enumerate(zip(self.mutations, journal["records"]), 1):
                path = self.root / record["path"]
                if _sha(_read(path)) != record["before_sha256"]:
                    raise TransactionError(f"destination drift before write: {record['path']}")
                _fault(self.fault, "before_destination", journal)
                if mutation.data is None:
                    durable_unlink(path, root=self.root, missing_ok=True)
                else:
                    atomic_write_bytes(path, mutation.data, root=self.root, mode=record["mode"])
                _fault(self.fault, "after_destination", journal)
                if _sha(_read(path)) != record["after_sha256"]:
                    raise TransactionError(f"post-write verification failed: {record['path']}")
                journal["applied"] = index
                atomic_write_json(journal_path, journal, mode=0o600, root=self.root)
            journal["phase"] = "COMMITTED"
            journal["committed"] = _now()
            atomic_write_json(journal_path, journal, mode=0o600, root=self.root)
            _fault(self.fault, "after_commit", journal)
            if not retain:
                (tx_dir / "journal.json").unlink(missing_ok=True)
                tx_dir.rmdir()
            return journal
        except BaseException as original:
            if journal is not None and tx_dir is not None and (tx_dir / "journal.json").exists():
                try:
                    recover_transaction(tx_dir / "journal.json", expected_root=self.root, force_rollback=True)
                except Exception as rollback:
                    raise TransactionError(f"transaction failed ({original}); rollback failed ({rollback})") from original
            elif tx_dir is not None and tx_dir.exists() and not any(tx_dir.iterdir()):
                tx_dir.rmdir()
            raise
        finally:
            lock.release()


def _read_recovery_journal(journal_path: Path) -> dict:
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TransactionError(f"invalid pending transaction journal {journal_path}: {exc}") from exc
    if not isinstance(journal, dict):
        raise TransactionError("invalid transaction journal schema")
    return journal


def _valid_sha256(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _prepare_recovery(
    journal_path: Path,
    *,
    expected_root: Path,
    force_rollback: bool = False,
    journal: dict | None = None,
) -> _RecoveryPlan:
    journal = _read_recovery_journal(journal_path) if journal is None else journal
    if journal.get("schema") != 1 or not isinstance(journal.get("records"), list):
        raise TransactionError("invalid transaction journal schema")
    root = expected_root.resolve(strict=True)
    journal_root = journal.get("root")
    if not isinstance(journal_root, str) or not journal_root or Path(journal_root).resolve() != root:
        raise TransactionError("transaction journal root mismatch")
    phase = journal.get("phase")
    if phase not in {"PREPARED", "APPLYING", "COMMITTED", "ROLLED_BACK"}:
        raise TransactionError(f"unsupported transaction recovery phase: {phase!r}")
    decoded_before: dict[str, bytes | None] = {}
    validated_paths: dict[str, Path] = {}
    expected_hashes: dict[str, frozenset[str | None]] = {}
    seen: set[str] = set()
    for record in journal["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise TransactionError("invalid transaction record schema")
        relative = record["path"]
        if relative in seen:
            raise TransactionError(f"duplicate transaction recovery destination: {relative}")
        seen.add(relative)
        path = confined_path(root, relative, reject_symlinks=True)
        encoded = record.get("before_base64")
        try:
            before = base64.b64decode(encoded, validate=True) if encoded is not None else None
        except (ValueError, TypeError) as exc:
            raise TransactionError(f"invalid rollback bytes for {record.get('path')}") from exc
        if _sha(before) != record.get("before_sha256"):
            raise TransactionError(f"rollback bytes do not match before hash: {record['path']}")
        after_hash = record.get("after_sha256")
        if not _valid_sha256(after_hash):
            raise TransactionError(f"invalid after hash: {record['path']}")
        decoded_before[relative] = before
        validated_paths[relative] = path
        if phase == "ROLLED_BACK":
            expected_hashes[relative] = frozenset({record["before_sha256"]})
        elif phase == "COMMITTED" and not force_rollback:
            expected_hashes[relative] = frozenset({after_hash})
        else:
            expected_hashes[relative] = frozenset({record["before_sha256"], after_hash})
    return _RecoveryPlan(
        journal_path=journal_path,
        journal=journal,
        rollback=phase in {"PREPARED", "APPLYING"} or (phase == "COMMITTED" and force_rollback),
        paths=validated_paths,
        before=decoded_before,
        expected_hashes=expected_hashes,
    )


def _preflight_recovery_batch(plans: list[_RecoveryPlan]) -> None:
    destinations: dict[Path, tuple[Path, bool]] = {}
    for plan in plans:
        for relative, path in plan.paths.items():
            prior = destinations.get(path)
            if prior is not None and (prior[1] or plan.rollback):
                raise TransactionError(
                    "ambiguous destination appears in multiple pending transactions: "
                    f"{relative} ({prior[0]} and {plan.journal_path})"
                )
            destinations[path] = (plan.journal_path, plan.rollback)
    for plan in plans:
        for relative, path in plan.paths.items():
            if _sha(_read(path)) not in plan.expected_hashes[relative]:
                raise TransactionError(f"ambiguous destination drift during recovery: {relative}")


def _apply_recovery(plan: _RecoveryPlan, *, root: Path) -> dict:
    journal = plan.journal
    if not plan.rollback:
        return journal
    for record in reversed(journal["records"]):
        path = plan.paths[record["path"]]
        before = plan.before[record["path"]]
        if before is None:
            durable_unlink(path, root=root, missing_ok=True)
        else:
            atomic_write_bytes(
                path,
                before,
                root=root,
                mode=record.get("mode"),
            )
    journal["phase"] = "ROLLED_BACK"
    journal["rolled_back"] = _now()
    journal["applied"] = 0
    atomic_write_json(plan.journal_path, journal, mode=0o600, root=root)
    return journal


def recover_transaction(journal_path: Path, *, expected_root: Path, force_rollback: bool = False) -> dict:
    root = expected_root.resolve(strict=True)
    plan = _prepare_recovery(journal_path, expected_root=root, force_rollback=force_rollback)
    _preflight_recovery_batch([plan])
    return _apply_recovery(plan, root=root)


def _recover_candidates(
    state_dir: Path,
    *,
    root: Path,
    names: set[str] | None,
    remove_recovered: bool,
) -> list[dict]:
    candidates: list[tuple[Path, Path, dict]] = []
    for directory in sorted(state_dir.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or directory.is_symlink():
            continue
        journal_path = directory / "journal.json"
        if not journal_path.is_file() or journal_path.is_symlink():
            raise TransactionError(f"transaction directory has no real journal: {directory}")
        preview = _read_recovery_journal(journal_path)
        if names is not None and preview.get("name") not in names:
            continue
        if remove_recovered and any(path != journal_path for path in directory.iterdir()):
            raise TransactionError(f"recovered transaction directory is not empty: {directory}")
        candidates.append((directory, journal_path, preview))

    plans = [
        _prepare_recovery(
            journal_path,
            expected_root=root,
            force_rollback=preview.get("phase") not in {"COMMITTED", "ROLLED_BACK"},
            journal=preview,
        )
        for _, journal_path, preview in candidates
    ]
    # Recovery of a selected journal set is one preflight decision.  No journal
    # may mutate a destination until every candidate and destination is known to
    # be recoverable, including cross-journal destination ambiguity.
    _preflight_recovery_batch(plans)
    recovered = [_apply_recovery(plan, root=root) for plan in plans]
    if remove_recovered:
        for directory, journal_path, _ in candidates:
            durable_unlink(journal_path, root=root)
            try:
                directory.rmdir()
            except OSError as exc:
                raise TransactionError(f"recovered transaction directory is not empty: {directory}") from exc
    return recovered


def recover_pending_transactions(
    state_dir: Path,
    *,
    expected_root: Path,
    name: str | None = None,
    remove_recovered: bool = False,
) -> list[dict]:
    """Recover durable journals left by a terminated process.

    Callers must hold the corresponding transaction-name lock when filtering by
    name.  Every candidate is schema/root checked before any destination change.
    """
    root = expected_root.resolve(strict=True)
    state_dir = confined_path(root, state_dir, reject_symlinks=True)
    if not state_dir.exists():
        return []
    return _recover_candidates(
        state_dir,
        root=root,
        names={name} if name is not None else None,
        remove_recovered=remove_recovered,
    )


def recover_named_transactions(state_dir: Path, *, expected_root: Path, names: Iterable[str]) -> list[dict]:
    """Recover selected operation journals before a caller plans mutations."""
    root = expected_root.resolve(strict=True)
    state_dir = confined_path(root, state_dir, reject_symlinks=True)
    if not state_dir.exists():
        return []
    selected = list(dict.fromkeys(names))
    for name in selected:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("transaction recovery name must be non-empty")
    locks = [FileLock(state_dir / f".{name}.lock", f"transaction:{name}") for name in sorted(selected)]
    acquired: list[FileLock] = []
    try:
        for lock in locks:
            lock.acquire()
            acquired.append(lock)
        return _recover_candidates(
            state_dir,
            root=root,
            names=set(selected),
            remove_recovered=True,
        )
    finally:
        for lock in reversed(acquired):
            lock.release()
