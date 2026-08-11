from __future__ import annotations

import os
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .security import is_path_redirect


class GovernanceViolation(RuntimeError):
    """Raised when an Aegis-managed mutation targets governing input."""


_GOVERNANCE_CREATION_ROOTS: ContextVar[frozenset[str]] = ContextVar(
    "aegis_governance_creation_roots", default=frozenset()
)
_INSTRUCTION_UPDATE_TARGETS: ContextVar[frozenset[str]] = ContextVar(
    "aegis_instruction_update_targets", default=frozenset()
)


def _path_key(path: Path) -> str:
    candidate = Path(path)
    value = str(candidate.parent.resolve(strict=False) / candidate.name)
    return value.casefold() if os.name == "nt" else value


@contextmanager
def _governance_tree_creation(root: Path):
    """Authorize creation of one absent governance tree for verified deployment.

    The authority is context-local, cannot apply to an existing ``.agents``
    tree, and ends before the extracted artifact is returned to its caller.
    """

    project = Path(root).resolve(strict=True)
    if (project / ".agents").exists():
        raise GovernanceViolation("AEGIS-I001: governance creation requires an absent .agents tree")
    current = _GOVERNANCE_CREATION_ROOTS.get()
    token = _GOVERNANCE_CREATION_ROOTS.set(current | {str(project)})
    try:
        yield
    finally:
        _GOVERNANCE_CREATION_ROOTS.reset(token)


@contextmanager
def _governing_instruction_update(root: Path, target: Path):
    """Authorize only the root bootstrap transaction's exact AGENTS.md target."""

    project = Path(root).resolve(strict=True)
    expected = project / "AGENTS.md"
    candidate = Path(target)
    candidate = candidate if candidate.is_absolute() else project / candidate
    if _path_key(candidate) != _path_key(expected):
        raise GovernanceViolation("AEGIS-I001: bootstrap authority is limited to root AGENTS.md")
    if is_path_redirect(candidate):
        raise GovernanceViolation("AEGIS-I001: redirected governing instruction is forbidden")
    current = _INSTRUCTION_UPDATE_TARGETS.get()
    token = _INSTRUCTION_UPDATE_TARGETS.set(current | {_path_key(expected)})
    try:
        yield
    finally:
        _INSTRUCTION_UPDATE_TARGETS.reset(token)


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        stat_result = path.stat(follow_symlinks=False)
    except OSError:
        return None
    return int(stat_result.st_dev), int(stat_result.st_ino)


def _relative_casefold(path: Path, root: Path) -> tuple[str, ...] | None:
    """Return a confined relative identity with filesystem aliases normalized."""

    try:
        candidate = path.parent.resolve(strict=False) / path.name
        relative = candidate.relative_to(root.resolve(strict=True))
    except ValueError:
        return None
    parts = relative.parts
    if os.name == "nt":
        return tuple(part.casefold() for part in parts)
    return tuple(parts)


def _governing_file_identities(root: Path) -> set[tuple[int, int]]:
    identities: set[tuple[int, int]] = set()
    agents = root / ".agents"
    if agents.is_dir() and not is_path_redirect(agents):
        for path in agents.rglob("*"):
            identity = _identity(path)
            if identity is not None:
                identities.add(identity)
    for path in _instruction_files(root):
        identity = _identity(path)
        if identity is not None:
            identities.add(identity)
    return identities


def _instruction_files(root: Path) -> list[Path]:
    found: list[Path] = []
    excluded = {".git", ".aegis", "dist", "build", "vendor", "node_modules", "__pycache__"}
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        names[:] = sorted(name for name in names if name not in excluded)
        base = Path(directory)
        if "AGENTS.md" in files:
            found.append(base / "AGENTS.md")
    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def _entry(path: Path, root: Path) -> dict:
    relative = path.relative_to(root).as_posix()
    if is_path_redirect(path):
        target = os.readlink(path) if path.is_symlink() else "windows-reparse-point"
        return {"path": relative, "kind": "redirect", "target": str(target)}
    if path.is_dir():
        return {"path": relative, "kind": "directory", "size": 0}
    if path.is_file():
        data = path.read_bytes()
        return {
            "path": relative,
            "kind": "file",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return {"path": relative, "kind": "other"}


def capture_governance(root: Path) -> dict:
    """Capture deployed governance and governing-instruction content identities."""

    project = Path(root).resolve(strict=True)
    agents = project / ".agents"
    if not agents.is_dir() or is_path_redirect(agents):
        raise GovernanceViolation("AEGIS-I001: deployed .agents root is missing or redirected")
    paths = [agents, *agents.rglob("*"), *_instruction_files(project)]
    unique = sorted(set(paths), key=lambda item: item.relative_to(project).as_posix())
    entries = [_entry(path, project) for path in unique]
    body = {
        "schema": 1,
        "project_root": str(project),
        "integrity_mode": "INTEGRITY_DETECTION",
        "preventive_scope": "AEGIS_MANAGED_MUTATION_PATHS",
        "entries": entries,
    }
    body["digest"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return body


def verify_governance(root: Path, snapshot: dict) -> dict:
    if not isinstance(snapshot, dict) or snapshot.get("schema") != 1:
        raise GovernanceViolation("AEGIS-I001: governance snapshot schema is invalid")
    project = Path(root).resolve(strict=True)
    if snapshot.get("project_root") != str(project):
        raise GovernanceViolation("AEGIS-I001: governance snapshot belongs to another project root")
    current = capture_governance(project)
    if current.get("digest") != snapshot.get("digest") or current.get("entries") != snapshot.get("entries"):
        raise GovernanceViolation(
            f"AEGIS-I001: governance integrity changed (expected {snapshot.get('digest')}, observed {current.get('digest')})"
        )
    return {"ok": True, "digest": current["digest"], "entry_count": len(current["entries"])}


def assert_mutation_allowed(root: Path, *targets: Path, operation: str = "write") -> None:
    """Deny ordinary Aegis mutations of deployed governance.

    This guard covers Aegis-managed writes, deletes, and rename endpoints.  It
    is deliberately unconditional: there is no flag or environment escape
    hatch.  Out-of-band same-user writes require independent digest detection.
    """

    project = Path(root).resolve(strict=True)
    governance_creation = str(project) in _GOVERNANCE_CREATION_ROOTS.get()
    governing_identities = _governing_file_identities(project)
    for supplied in targets:
        raw = Path(supplied)
        if not raw.is_absolute() and ".." in raw.parts:
            raise GovernanceViolation(f"AEGIS-I001: {operation} path uses parent traversal: {supplied}")
        candidate = raw if raw.is_absolute() else project / raw
        instruction_update = _path_key(candidate) in _INSTRUCTION_UPDATE_TARGETS.get()
        relative_parts = _relative_casefold(candidate, project)
        if relative_parts is None:
            raise GovernanceViolation(f"AEGIS-I010: {operation} target escapes project root: {supplied}")
        if relative_parts and relative_parts[0] == ".agents" and not governance_creation:
            raise GovernanceViolation(f"AEGIS-I001: deployed .agents governance is immutable: {supplied}")
        if (
            relative_parts
            and relative_parts[-1] == ("agents.md" if os.name == "nt" else "AGENTS.md")
            and not instruction_update
        ):
            raise GovernanceViolation(f"AEGIS-I001: governing instruction file is immutable: {supplied}")

        current = candidate.absolute()
        while True:
            if is_path_redirect(current):
                raise GovernanceViolation(f"AEGIS-I001: redirected mutation path is forbidden: {current}")
            try:
                if current.resolve(strict=False) == project:
                    break
            except OSError as exc:
                raise GovernanceViolation(
                    f"AEGIS-I001: cannot resolve mutation ancestry: {current}"
                ) from exc
            parent = current.parent
            if parent == current:
                raise GovernanceViolation(
                    f"AEGIS-I010: {operation} target ancestry does not reach project root: {supplied}"
                )
            current = parent

        resolved = candidate.parent.resolve(strict=False) / candidate.name
        try:
            resolved_relative = resolved.relative_to(project)
        except ValueError as exc:
            raise GovernanceViolation(f"AEGIS-I001: resolved mutation path escapes project root: {supplied}") from exc
        normalized_resolved = tuple(
            part.casefold() if os.name == "nt" else part for part in resolved_relative.parts
        )
        if normalized_resolved and normalized_resolved[0] == ".agents" and not governance_creation:
            raise GovernanceViolation(f"AEGIS-I001: resolved target enters deployed governance: {supplied}")
        identity = _identity(candidate)
        if identity is not None and identity in governing_identities and not instruction_update:
            raise GovernanceViolation(f"AEGIS-I001: hardlinked governing content is immutable: {supplied}")
