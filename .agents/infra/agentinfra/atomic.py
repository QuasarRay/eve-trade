from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable

from .security import is_path_redirect
from .governance import GovernanceViolation, assert_mutation_allowed


class AtomicWriteError(OSError):
    pass


FaultHook = Callable[[str, Path], None]


def _call_fault(fault: FaultHook | None, stage: str, path: Path) -> None:
    if fault is not None:
        fault(stage, path)


def _assert_confined(path: Path, root: Path | None, *, reject_symlinks: bool) -> None:
    if ".." in path.parts:
        raise AtomicWriteError(f"parent traversal is not allowed: {path}")
    absolute = path.parent.resolve(strict=False) / path.name
    resolved_root = None
    if root is not None:
        resolved_root = root.resolve(strict=True)
        try:
            assert_mutation_allowed(resolved_root, path, operation="filesystem mutation")
        except GovernanceViolation as exc:
            raise AtomicWriteError(str(exc)) from exc
        try:
            absolute.relative_to(resolved_root)
        except ValueError as exc:
            raise AtomicWriteError(f"target escapes required root {resolved_root}: {path}") from exc
    if reject_symlinks:
        if is_path_redirect(path):
            raise AtomicWriteError(f"refusing redirected target: {path}")
        current = path.parent.absolute()
        while True:
            if is_path_redirect(current):
                raise AtomicWriteError(f"refusing redirected parent: {current}")
            # Drive substitutions, junction aliases, and equivalent spellings
            # can give the lexical target and canonical root different prefixes.
            # Compare filesystem identity after resolution, but inspect the
            # lexical ancestor first so an in-root symlink is still rejected.
            if resolved_root is not None and current.resolve(strict=False) == resolved_root:
                break
            if resolved_root is None and current.parent == current:
                break
            if current.parent == current:
                raise AtomicWriteError(f"target ancestry does not reach required root {resolved_root}: {path}")
            current = current.parent


def _directory_fsync(directory: Path, *, required: bool) -> None:
    if os.name == "nt":
        # Python does not expose a portable Windows directory handle suitable for FlushFileBuffers.
        # ReplaceFile/MoveFileEx durability remains a host capability, not a fabricated fsync claim.
        if required and not hasattr(os, "O_DIRECTORY"):
            return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, flags)
    except OSError:
        if required and os.name != "nt":
            raise
        return
    try:
        os.fsync(fd)
    except OSError:
        if required:
            raise
    finally:
        os.close(fd)


def _replace_with_bounded_retry(source: Path, destination: Path) -> None:
    """Handle transient Windows sharing violations without hiding persistent contention."""
    delays = (0.0, 0.005, 0.015, 0.04, 0.1) if os.name == "nt" else (0.0,)
    last: OSError | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            last = exc
    assert last is not None
    raise last


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    root: Path | None = None,
    mode: int | None = None,
    preserve_mode: bool = True,
    reject_symlinks: bool = True,
    durability: str = "required",
    fault: FaultHook | None = None,
) -> None:
    """Atomically replace *path* without exposing partial target contents.

    The temporary file is always created in the destination directory.  Existing permission bits are
    preserved by default; new public control files use 0644 unless the caller explicitly requests a more
    restrictive mode.  File data is fsynced before replacement and the parent directory is fsynced where
    the host exposes that operation.
    """

    path = Path(path)
    if durability not in {"required", "best-effort"}:
        raise ValueError("durability must be 'required' or 'best-effort'")
    _assert_confined(path, root, reject_symlinks=reject_symlinks)
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_confined(path, root, reject_symlinks=reject_symlinks)
    original_mode = None
    if path.exists() and preserve_mode:
        original_mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    desired_mode = mode if mode is not None else (original_mode if original_mode is not None else 0o644)
    _call_fault(fault, "before_temp_create", path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    replaced = False
    try:
        os.chmod(tmp, desired_mode)
        _call_fault(fault, "before_temp_write", path)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(data)
            _call_fault(fault, "during_temp_write", path)
            stream.flush()
            os.fsync(stream.fileno())
        _call_fault(fault, "after_temp_fsync", path)
        _call_fault(fault, "before_replace", path)
        _replace_with_bounded_retry(tmp, path)
        replaced = True
        _call_fault(fault, "after_replace", path)
        _directory_fsync(path.parent, required=durability == "required")
        _call_fault(fault, "after_directory_fsync", path)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if not replaced:
            tmp.unlink(missing_ok=True)
        raise
    finally:
        if not replaced:
            tmp.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, **kwargs) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), **kwargs)


def atomic_write_json(path: Path, obj, **kwargs) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True) + "\n", **kwargs)


def durable_unlink(
    path: Path,
    *,
    root: Path | None = None,
    missing_ok: bool = False,
    reject_symlinks: bool = True,
    durability: str = "required",
) -> None:
    if durability not in {"required", "best-effort"}:
        raise ValueError("durability must be 'required' or 'best-effort'")
    path = Path(path)
    _assert_confined(path, root, reject_symlinks=reject_symlinks)
    try:
        path.unlink()
    except FileNotFoundError:
        if not missing_ok:
            raise
        return
    _directory_fsync(path.parent, required=durability == "required")
