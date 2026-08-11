from __future__ import annotations

import codecs
import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from .atomic import atomic_write_bytes
from .governance import _governing_instruction_update
from .locks import FileLock
from .paths import framework_dir, install_state_dir, persistent_dir, runtime_dir
from .security import confined_path
from .transaction import FileTransaction, Mutation, recover_named_transactions


BEGIN = "<!-- AEGIS:BEGIN -->"
END = "<!-- AEGIS:END -->"
_FENCE = re.compile(r"^\s*(```+|~~~+)")


class BootstrapError(RuntimeError):
    pass


def _sha_bytes(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _sha(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))  # compatibility helper used by older callers


def _template_path(root: Path) -> Path:
    return framework_dir(root) / "bootstrap" / "root-AGENTS.block.md"


def _decode(data: bytes) -> tuple[str, bool]:
    bom = data.startswith(codecs.BOM_UTF8)
    payload = data[len(codecs.BOM_UTF8) :] if bom else data
    try:
        return payload.decode("utf-8"), bom
    except UnicodeDecodeError as exc:
        raise BootstrapError("root AGENTS.md is not valid UTF-8") from exc


def _newline(text: str) -> str:
    first_lf = text.find("\n")
    if first_lf >= 1 and text[first_lf - 1] == "\r":
        return "\r\n"
    return "\n"


def managed_block(root: Path, *, newline: str = "\n") -> str:
    body = _template_path(root).read_text(encoding="utf-8").strip()
    if any(line.strip() in {BEGIN, END} for line in body.splitlines()):
        raise BootstrapError("managed bootstrap template must not contain marker lines")
    normalized = newline.join(body.splitlines())
    return f"{BEGIN}{newline}{normalized}{newline}{END}"


def _marker_offsets(text: str) -> tuple[list[int], list[int]]:
    begins: list[int] = []
    ends: list[int] = []
    offset = 0
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        logical = line.rstrip("\r\n")
        stripped = logical.lstrip()
        quoted = stripped.startswith(">")
        fence_match = _FENCE.match(logical) if not quoted else None
        if fence_match:
            token = fence_match.group(1)[0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
        elif fence is None and not quoted:
            marker = logical.strip()
            if marker == BEGIN:
                begins.append(offset + logical.index(BEGIN))
            elif marker == END:
                ends.append(offset + logical.index(END))
        offset += len(line)
    return begins, ends


def _split_existing(text: str) -> tuple[str, str | None, str]:
    begins, ends = _marker_offsets(text)
    if not begins and not ends:
        return text, None, ""
    if len(begins) != 1 or len(ends) != 1 or ends[0] <= begins[0]:
        raise BootstrapError("malformed or duplicate Aegis markers in root AGENTS.md")
    start = begins[0]
    finish = ends[0] + len(END)
    return text[:start], text[start:finish], text[finish:]


def _state_root(root: Path) -> Path:
    return install_state_dir(root) / "bootstrap"


def _journal_path(root: Path) -> Path:
    return _state_root(root) / "install.json"


def _legacy_journal_path(root: Path) -> Path:
    return runtime_dir(root) / "bootstrap-install.json"


def _load_journal(root: Path) -> dict | None:
    path = _journal_path(root)
    legacy = False
    if not path.exists() and _legacy_journal_path(root).exists():
        path = _legacy_journal_path(root)
        legacy = True
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BootstrapError(f"invalid bootstrap journal: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") not in {1, 2}:
        raise BootstrapError("unsupported bootstrap journal schema")
    value["_legacy_runtime"] = legacy
    return value


def _journal_bytes(value: dict) -> bytes:
    public = {key: item for key, item in value.items() if not key.startswith("_")}
    return (json.dumps(public, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _framework_version(root: Path) -> str:
    return (framework_dir(root) / "VERSION").read_text(encoding="utf-8").strip()


def plan_install(root: Path, *, replace_managed_block: bool = False) -> dict:
    root = root.resolve(strict=True)
    target = confined_path(root, "AGENTS.md", reject_symlinks=True)
    target_existed = target.exists()
    old_bytes = target.read_bytes() if target_existed else b""
    old, bom = _decode(old_bytes)
    newline = _newline(old)
    prefix, current, suffix = _split_existing(old)
    desired = managed_block(root, newline=newline)
    journal = _load_journal(root)

    if current is not None and current != desired:
        previous_hash = journal.get("installed_block_sha256") if journal else None
        if previous_hash != _sha_bytes(current.encode("utf-8")) and not replace_managed_block:
            raise BootstrapError(
                "existing Aegis block differs from both this framework and the recorded install; "
                "refusing to overwrite possible user edits (use --replace-managed-block only after review)"
            )

    if current is None:
        separator = "" if not old else ("" if old.endswith(newline * 2) else newline if old.endswith(newline) else newline * 2)
        new = old + separator + desired + newline
        action = "create" if not target_existed else "append"
    else:
        new = prefix + desired + suffix
        action = "noop" if new == old else "update"
    new_bytes = (codecs.BOM_UTF8 if bom else b"") + new.encode("utf-8")
    return {
        "target": str(target),
        "destination": "AGENTS.md",
        "target_existed": target_existed,
        "action": action,
        "changed": new_bytes != old_bytes,
        "before_sha256": _sha_bytes(old_bytes),
        "after_sha256": _sha_bytes(new_bytes),
        "installed_block_sha256": _sha_bytes(desired.encode("utf-8")),
        "newline": "CRLF" if newline == "\r\n" else "LF",
        "utf8_bom": bom,
        "old": old,
        "new": new,
        "_old_bytes": old_bytes,
        "_new_bytes": new_bytes,
        "_journal": journal,
    }


def _public(plan: dict) -> dict:
    return {key: value for key, value in plan.items() if key not in {"old", "new"} and not key.startswith("_")}


def install(root: Path, *, apply: bool = False, replace_managed_block: bool = False) -> dict:
    root = root.resolve(strict=True)
    with _governing_instruction_update(root, root / "AGENTS.md"):
        recover_named_transactions(
            persistent_dir(root) / "transactions",
            expected_root=root,
            names=("bootstrap-install", "bootstrap-uninstall"),
        )
    initial = plan_install(root, replace_managed_block=replace_managed_block)
    if not apply or not initial["changed"]:
        return {"applied": False, **_public(initial)}
    lock_path = persistent_dir(root) / "locks" / "bootstrap.lock"
    lock = FileLock(lock_path, "aegis-bootstrap-install")
    lock.acquire()
    created_backup: Path | None = None
    try:
        plan = plan_install(root, replace_managed_block=replace_managed_block)
        if plan["before_sha256"] != initial["before_sha256"]:
            raise BootstrapError("root AGENTS.md changed while bootstrap install was being planned")
        target = Path(plan["target"])
        prior = plan["_journal"]
        state_root = _state_root(root)
        backup_rel = prior.get("backup") if prior else None
        original_sha = prior.get("original_sha256") if prior else plan["before_sha256"]
        created_target = prior.get("created_target") if prior else not plan["target_existed"]
        original_mode = prior.get("original_mode") if prior else (
            stat.S_IMODE(target.stat(follow_symlinks=False).st_mode) if target.exists() else None
        )
        if prior and prior.get("schema") == 1:
            # A v1 journal can be used only when its backup is still present.  Never invent recovery data.
            legacy_backup = prior.get("backup")
            if legacy_backup and Path(legacy_backup).is_file():
                backup_rel = str(Path(legacy_backup).resolve().relative_to(root).as_posix())
            elif not prior.get("created_target"):
                raise BootstrapError("legacy bootstrap journal lacks durable backup; refusing unsafe upgrade")
        if prior is None and target.exists():
            backup = state_root / "backups" / f"original-{uuid.uuid4().hex}.bin"
            backup.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(backup, plan["_old_bytes"], root=root, mode=0o600)
            created_backup = backup
            backup_rel = backup.relative_to(root).as_posix()
        journal = {
            "schema": 2,
            "framework_version": _framework_version(root),
            "destination": "AGENTS.md",
            "created_target": bool(created_target),
            "original_sha256": original_sha,
            "original_mode": original_mode,
            "installed_sha256": plan["after_sha256"],
            "installed_block_sha256": plan["installed_block_sha256"],
            "backup": backup_rel,
            "upgrade_count": int(prior.get("upgrade_count", 0) if prior else 0) + (1 if prior else 0),
        }
        mutations = [
            Mutation(target, plan["_new_bytes"], expected_sha256=plan["before_sha256"] if plan["target_existed"] else None, expected_exists=plan["target_existed"], mode=original_mode),
            Mutation(_journal_path(root), _journal_bytes(journal), expected_sha256=_sha_bytes(_journal_path(root).read_bytes()) if _journal_path(root).exists() else None, expected_exists=_journal_path(root).exists(), mode=0o600),
        ]
        with _governing_instruction_update(root, target):
            FileTransaction(
                root,
                mutations,
                state_dir=persistent_dir(root) / "transactions",
                name="bootstrap-install",
            ).commit(retain=False)
        _legacy_journal_path(root).unlink(missing_ok=True)
        return {"applied": True, **_public(plan), "backup": backup_rel, "journal": str(_journal_path(root))}
    except BaseException:
        if created_backup is not None and created_backup.exists() and not _journal_path(root).exists():
            created_backup.unlink(missing_ok=True)
        raise
    finally:
        lock.release()


def plan_uninstall(root: Path, *, force_managed_block: bool = False) -> dict:
    root = root.resolve(strict=True)
    target = confined_path(root, "AGENTS.md", reject_symlinks=True)
    journal = _load_journal(root)
    if journal is None:
        if not target.exists():
            return {"target": str(target), "action": "noop", "changed": False, "old": "", "new": ""}
        old_bytes = target.read_bytes()
        old, _ = _decode(old_bytes)
        _, current, _ = _split_existing(old)
        if current is None:
            return {"target": str(target), "action": "noop", "changed": False, "old": old, "new": old}
        raise BootstrapError("managed block exists without persistent install metadata; refusing destructive guesswork")
    if journal.get("_legacy_runtime"):
        raise BootstrapError("bootstrap recovery metadata exists only under disposable runtime; migrate before uninstall")
    if journal.get("schema") != 2 or journal.get("destination") != "AGENTS.md":
        raise BootstrapError("invalid persistent bootstrap journal")
    if not target.exists():
        raise BootstrapError("installed AGENTS.md is missing; recovery requires explicit inspection")
    old_bytes = target.read_bytes()
    old, _ = _decode(old_bytes)
    _, current, _ = _split_existing(old)
    if current is None:
        raise BootstrapError("managed Aegis block is missing from installed AGENTS.md")
    if _sha_bytes(current.encode("utf-8")) != journal.get("installed_block_sha256") and not force_managed_block:
        raise BootstrapError("managed Aegis block changed after installation; refusing removal")
    if _sha_bytes(old_bytes) != journal.get("installed_sha256") and not force_managed_block:
        raise BootstrapError("root AGENTS.md changed after installation; refusing exact rollback over user edits")
    backup_rel = journal.get("backup")
    if journal.get("created_target"):
        new_bytes = None
    else:
        if not isinstance(backup_rel, str):
            raise BootstrapError("persistent bootstrap journal has no original backup")
        backup = confined_path(root, backup_rel, must_exist=True, reject_symlinks=True)
        new_bytes = backup.read_bytes()
        if _sha_bytes(new_bytes) != journal.get("original_sha256"):
            raise BootstrapError("bootstrap backup integrity check failed")
    new_text = _decode(new_bytes or b"")[0]
    return {
        "target": str(target),
        "action": "remove",
        "changed": True,
        "before_sha256": _sha_bytes(old_bytes),
        "after_sha256": _sha_bytes(new_bytes),
        "old": old,
        "new": new_text,
        "_old_bytes": old_bytes,
        "_new_bytes": new_bytes,
        "_journal": journal,
    }


def uninstall(root: Path, *, apply: bool = False, force_managed_block: bool = False) -> dict:
    root = root.resolve(strict=True)
    with _governing_instruction_update(root, root / "AGENTS.md"):
        recover_named_transactions(
            persistent_dir(root) / "transactions",
            expected_root=root,
            names=("bootstrap-install", "bootstrap-uninstall"),
        )
    initial = plan_uninstall(root, force_managed_block=force_managed_block)
    if not apply or not initial.get("changed"):
        return {"applied": False, **_public(initial)}
    lock = FileLock(persistent_dir(root) / "locks" / "bootstrap.lock", "aegis-bootstrap-uninstall")
    lock.acquire()
    try:
        plan = plan_uninstall(root, force_managed_block=force_managed_block)
        if plan["before_sha256"] != initial["before_sha256"]:
            raise BootstrapError("root AGENTS.md changed before uninstall commit")
        target = Path(plan["target"])
        mutations = [
            Mutation(target, plan["_new_bytes"], expected_sha256=plan["before_sha256"], expected_exists=True, mode=plan["_journal"].get("original_mode")),
            Mutation(_journal_path(root), None, expected_sha256=_sha_bytes(_journal_path(root).read_bytes()), expected_exists=True),
        ]
        backup_rel = plan["_journal"].get("backup")
        if backup_rel:
            backup = confined_path(root, backup_rel, must_exist=True, reject_symlinks=True)
            mutations.append(Mutation(backup, None, expected_sha256=_sha_bytes(backup.read_bytes()), expected_exists=True))
        with _governing_instruction_update(root, target):
            FileTransaction(
                root,
                mutations,
                state_dir=persistent_dir(root) / "transactions",
                name="bootstrap-uninstall",
            ).commit(retain=False)
        return {"applied": True, **_public(plan)}
    finally:
        lock.release()


def verify_installed(root: Path) -> tuple[bool, str]:
    target = root / "AGENTS.md"
    if not target.is_file() or target.is_symlink():
        return False, "root AGENTS.md missing or symlinked"
    try:
        text, _ = _decode(target.read_bytes())
        _, current, _ = _split_existing(text)
    except Exception as exc:
        return False, str(exc)
    if current is None:
        return False, "managed Aegis bootstrap block missing"
    desired = managed_block(root, newline=_newline(text))
    if current != desired:
        return False, "managed Aegis bootstrap block drifted"
    return True, "managed Aegis bootstrap block matches framework"
