from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path

from .security import minimal_subprocess_env


GIT_TIMEOUT_SECONDS = 20
DEFAULT_EPHEMERAL = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage",
    "htmlcov",
    "node_modules",
    "dist",
    "build",
    ".aegis",
    ".agents/runtime",
    ".agents/persistent",
}


def _run(root: Path, *args: str):
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
            env=minimal_subprocess_env(),
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired as exc:
        return {"timeout": True, "args": list(args), "error": str(exc)}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_fingerprint(path: Path) -> str:
    if path.is_symlink():
        return "symlink:" + str(path.readlink())
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        mode = path.stat(follow_symlinks=False).st_mode & 0o777
        return f"file:{mode:o}:{digest.hexdigest()}"
    return "other"


def _policy(root: Path) -> dict:
    path = root / ".agents" / "workspace-policy.toml"
    if not path.exists():
        return {"ephemeral": sorted(DEFAULT_EPHEMERAL), "include_ignored": [], "external_symlinks": []}
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except Exception as exc:
        raise RuntimeError(f"invalid workspace fingerprint policy: {exc}") from exc
    block = data.get("fingerprint", {})
    if not isinstance(block, dict):
        raise RuntimeError("workspace fingerprint policy [fingerprint] must be a table")
    out = {}
    for key in ("ephemeral", "include_ignored", "external_symlinks"):
        values = block.get(key, [])
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            raise RuntimeError(f"workspace fingerprint policy {key} must be a string array")
        if any(Path(item).is_absolute() or ".." in Path(item).parts for item in values):
            raise RuntimeError(f"workspace fingerprint policy {key} contains unsafe path")
        out[key] = values
    out["ephemeral"] = sorted(set(DEFAULT_EPHEMERAL) | set(out["ephemeral"]))
    return out


def _is_ephemeral(relative: str, policy: dict) -> bool:
    normalized = relative.replace("\\", "/").strip("/")
    return any(normalized == item.strip("/") or normalized.startswith(item.strip("/") + "/") for item in policy["ephemeral"])


def _parse_status(raw: bytes, policy: dict) -> list[tuple[str, str, str | None]]:
    chunks = raw.split(b"\0")
    records: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(chunks):
        record = chunks[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise RuntimeError("malformed git porcelain status record")
        status = record[:2].decode("ascii", "strict")
        relative = record[3:].decode("utf-8", "surrogateescape")
        previous = None
        if "R" in status or "C" in status:
            if index >= len(chunks) or not chunks[index]:
                raise RuntimeError("truncated git rename/copy status record")
            previous = chunks[index].decode("utf-8", "surrogateescape")
            index += 1
        if _is_ephemeral(relative, policy) and (previous is None or _is_ephemeral(previous, policy)):
            continue
        records.append((status, relative, previous))
    return records


def _failed(result, label: str) -> dict | None:
    if result is None:
        return {"schema": 2, "available": False, "kind": "git", "fallback_allowed": True, "reason": "git executable missing"}
    if isinstance(result, dict):
        return {"schema": 2, "available": False, "kind": "git", "fallback_allowed": False, "reason": f"git {label} timed out"}
    if result.returncode != 0:
        return {
            "schema": 2,
            "available": False,
            "kind": "git",
            "fallback_allowed": False,
            "reason": f"git {label} failed",
            "exit": result.returncode,
            "stderr_sha256": _sha(result.stderr),
        }
    return None


def git_workspace_fingerprint(root: Path) -> dict:
    root = root.resolve()
    probe = _run(root, "rev-parse", "--show-toplevel")
    if probe is None:
        return {"schema": 2, "available": False, "kind": "git", "fallback_allowed": True, "reason": "git executable missing"}
    if isinstance(probe, dict):
        return {"schema": 2, "available": False, "kind": "git", "fallback_allowed": False, "reason": "git root probe timed out"}
    if probe.returncode != 0:
        return {"schema": 2, "available": False, "kind": "git", "fallback_allowed": True, "reason": "not a git worktree"}
    top = Path(probe.stdout.decode("utf-8", "replace").strip()).resolve()
    try:
        root.relative_to(top)
    except ValueError:
        return {"schema": 2, "available": False, "kind": "git", "fallback_allowed": False, "reason": "framework root outside git top-level"}
    prefix_result = _run(root, "rev-parse", "--show-prefix")
    failure = _failed(prefix_result, "prefix probe")
    if failure:
        return failure
    prefix = prefix_result.stdout.decode("utf-8", "replace").strip()
    status = _run(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".")
    unstaged = _run(root, "diff", "--binary", "--no-ext-diff", "--", ".")
    staged = _run(root, "diff", "--cached", "--binary", "--no-ext-diff", "--", ".")
    for result, label in ((status, "status"), (unstaged, "diff"), (staged, "cached diff")):
        failure = _failed(result, label)
        if failure:
            return failure
    treeish = f"HEAD:{prefix.rstrip('/')}" if prefix else "HEAD^{tree}"
    head = _run(root, "rev-parse", "--verify", treeish)
    unborn = False
    if head is None or isinstance(head, dict):
        return _failed(head, "HEAD probe")
    if head.returncode != 0:
        symbolic = _run(root, "symbolic-ref", "--quiet", "HEAD")
        if symbolic is not None and not isinstance(symbolic, dict) and symbolic.returncode == 0:
            unborn = True
        else:
            return _failed(head, "HEAD probe")
    policy = _policy(root)
    status_records = _parse_status(status.stdout, policy)
    untracked = []
    for marker, relative, _ in status_records:
        if marker == "??":
            untracked.append((relative, _file_fingerprint(root / relative)))
    ignored = []
    for relative in policy["include_ignored"]:
        path = root / relative
        ignored.append((relative, _file_fingerprint(path) if path.exists() or path.is_symlink() else "missing"))
    external = []
    for relative in policy["external_symlinks"]:
        link = root / relative
        if not link.is_symlink():
            external.append((relative, "not-symlink"))
            continue
        target = link.resolve(strict=True)
        external.append((relative, str(link.readlink()), _file_fingerprint(target)))
    payload = {
        "schema": 2,
        "git_top": str(top),
        "scope": str(root),
        "prefix": prefix,
        "baseline_tree": None if unborn else head.stdout.decode("utf-8", "replace").strip(),
        "unborn": unborn,
        "status_sha256": _sha(json.dumps(status_records, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")),
        "unstaged_diff_sha256": _sha(unstaged.stdout),
        "staged_diff_sha256": _sha(staged.stdout),
        "untracked": sorted(untracked),
        "declared_ignored": sorted(ignored),
        "external_dependencies": sorted(external),
        "policy_sha256": _sha(_canonical_policy(policy)),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"schema": 2, "available": True, "kind": "git", "sha256": _sha(canonical), "details": payload}


def _canonical_policy(policy: dict) -> bytes:
    return json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")


def tree_workspace_fingerprint(root: Path) -> dict:
    root = root.resolve()
    policy = _policy(root)
    declared_external = set(policy["external_symlinks"])
    items = []
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    except OSError as exc:
        return {"schema": 2, "available": False, "kind": "tree", "fallback_allowed": False, "reason": str(exc)}
    for path in paths:
        try:
            relative = path.relative_to(root).as_posix()
            if _is_ephemeral(relative, policy):
                continue
            if path.is_symlink():
                resolved = path.resolve(strict=True)
                try:
                    resolved.relative_to(root)
                    items.append((relative, "symlink:" + str(path.readlink())))
                except ValueError:
                    if relative not in declared_external:
                        return {"schema": 2, "available": False, "kind": "tree", "fallback_allowed": False, "reason": f"undeclared external symlink: {relative}"}
                    items.append((relative, "external:" + str(path.readlink()), _file_fingerprint(resolved)))
            elif path.is_file():
                items.append((relative, _file_fingerprint(path)))
        except OSError as exc:
            return {"schema": 2, "available": False, "kind": "tree", "fallback_allowed": False, "reason": f"cannot fingerprint {path}: {exc}"}
    payload = {"schema": 2, "scope": str(root), "items": items, "policy_sha256": _sha(_canonical_policy(policy))}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"schema": 2, "available": True, "kind": "tree", "sha256": _sha(canonical), "file_count": len(items), "details": payload}


def workspace_fingerprint(root: Path) -> dict:
    git = git_workspace_fingerprint(root)
    if git.get("available") or not git.get("fallback_allowed"):
        return git
    return tree_workspace_fingerprint(root)
