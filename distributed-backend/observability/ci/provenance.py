"""Repository and runner provenance for observed validation runs."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROVENANCE_SCHEMA_VERSION = "o11y.run-provenance.v1"
PROVENANCE_ENVELOPE_SCHEMA_VERSION = "o11y.run-provenance-envelope.v1"
RUNNER_VERSION = "o11y-trust-model-2026-07-09"
SOURCE_PATHSPEC = ("--", ".", ":(exclude).o11y/runs/**", ":(exclude).o11y/index.json")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_run_provenance(
    root: Path,
    *,
    run_id: str,
    run_started_at: str,
    run_finished_at: str = "",
    status: str = "IN_PROGRESS",
    commands_executed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    git = collect_git_state(root)
    versions = collect_tool_versions(root)
    ci_or_local = "ci" if os.getenv("GITHUB_ACTIONS") else "local"
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "run_id": run_id,
        "run_started_at": run_started_at,
        "run_finished_at": run_finished_at,
        "run_status": status,
        "repository_root": str(root),
        "branch": git["branch"],
        "full_head_sha": git["full_head_sha"],
        "short_head_sha": git["short_head_sha"],
        "commit_subject": git["commit_subject"],
        "worktree_dirty": git["worktree_dirty"],
        "worktree_diff_fingerprint_if_dirty": git["worktree_diff_fingerprint_if_dirty"],
        "runner_version": RUNNER_VERSION,
        "o11y_code_version_or_sha": hash_observability_code(root),
        "operating_system": platform.platform(),
        "architecture": platform.machine(),
        "execution_environment": os.getenv("OBSERVABILITY_ENV") or ("github-actions" if ci_or_local == "ci" else "local"),
        "ci_or_local": ci_or_local,
        "workflow_run_id_if_available": os.getenv("GITHUB_RUN_ID", ""),
        "workflow_job_id_if_available": os.getenv("GITHUB_JOB", ""),
        "attempt_number_if_available": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "github_sha_if_available": os.getenv("GITHUB_SHA", ""),
        "github_ref_if_available": os.getenv("GITHUB_REF", ""),
        "commands_executed": commands_executed or [],
        "tool_versions": versions,
    }


def repository_identity(provenance: dict[str, Any] | None) -> dict[str, Any]:
    """Return the source identity fields that decide whether evidence is exact."""
    provenance = provenance or {}
    return {
        "full_head_sha": str(provenance.get("full_head_sha") or provenance.get("github_sha") or ""),
        "worktree_dirty": _optional_bool(provenance.get("worktree_dirty")),
        "worktree_diff_fingerprint_if_dirty": str(provenance.get("worktree_diff_fingerprint_if_dirty") or ""),
    }


def build_provenance_envelope(
    *,
    run_id: str,
    start_provenance: dict[str, Any],
    finish_provenance: dict[str, Any] | None = None,
    source_stability: str = "UNKNOWN",
    run_status: str = "IN_PROGRESS",
) -> dict[str, Any]:
    finish = finish_provenance or {}
    return {
        "schema_version": PROVENANCE_ENVELOPE_SCHEMA_VERSION,
        "run_id": run_id,
        "run_status": run_status,
        "run_started_at": start_provenance.get("run_started_at", ""),
        "run_finished_at": finish.get("run_finished_at", ""),
        "source_stability": source_stability,
        "start_provenance": start_provenance,
        "finish_provenance": finish,
        "start_repository_identity": repository_identity(start_provenance),
        "finish_repository_identity": repository_identity(finish) if finish else {},
    }


def split_provenance_envelope(provenance: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """Return start provenance, finish provenance, source stability, and run status.

    Older run artifacts stored a single provenance object. For those artifacts we
    treat the one object as both start and finish so historical readers stay
    compatible, while new exactness checks can still reject non-authoritative
    statuses.
    """
    value = provenance or {}
    if "start_provenance" in value or value.get("schema_version") == PROVENANCE_ENVELOPE_SCHEMA_VERSION:
        start = value.get("start_provenance") if isinstance(value.get("start_provenance"), dict) else {}
        finish = value.get("finish_provenance") if isinstance(value.get("finish_provenance"), dict) else {}
        run_status = str(value.get("run_status") or finish.get("run_status") or start.get("run_status") or "UNKNOWN")
        source_stability = str(value.get("source_stability") or source_stability_from_provenance(start, finish))
        return start, finish, source_stability, run_status
    run_status = str(value.get("run_status") or "UNKNOWN")
    source_stability = "CHANGED" if run_status == "SOURCE_CHANGED_DURING_RUN" else "UNCHANGED"
    return value, value, source_stability, run_status


def source_stability_from_provenance(start: dict[str, Any] | None, finish: dict[str, Any] | None) -> str:
    if not start or not finish:
        return "UNKNOWN"
    return "UNCHANGED" if repository_identity(start) == repository_identity(finish) else "CHANGED"


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def collect_git_state(root: Path) -> dict[str, Any]:
    root = root.resolve()
    full_head_sha = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1", *SOURCE_PATHSPEC)
    dirty = bool(status.strip()) and not status.startswith("<")
    return {
        "full_head_sha": full_head_sha,
        "short_head_sha": full_head_sha[:12] if full_head_sha and not full_head_sha.startswith("<") else "",
        "branch": _git(root, "branch", "--show-current") or os.getenv("GITHUB_REF_NAME", ""),
        "commit_subject": _git(root, "log", "-1", "--format=%s", "HEAD"),
        "worktree_dirty": dirty,
        "worktree_status": status.splitlines() if status and not status.startswith("<") else [],
        "worktree_diff_fingerprint_if_dirty": worktree_fingerprint(root) if dirty else "",
        "remote_origin": _git(root, "remote", "get-url", "origin"),
    }


def worktree_fingerprint(root: Path) -> str:
    root = root.resolve()
    digest = hashlib.sha256()
    for args in (
        ("status", "--porcelain=v1", "-z", *SOURCE_PATHSPEC),
        ("diff", "--binary", "--full-index", *SOURCE_PATHSPEC),
        ("diff", "--cached", "--binary", "--full-index", *SOURCE_PATHSPEC),
    ):
        value = _git_bytes(root, *args)
        digest.update("git ".encode("utf-8") + " ".join(args).encode("utf-8") + b"\0")
        digest.update(value)
        digest.update(b"\0")
    for path in _untracked_files(root):
        candidate = (root / path).resolve()
        if not candidate.is_file():
            continue
        try:
            digest.update(path.as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(candidate.read_bytes()).hexdigest().encode("ascii") + b"\0")
        except OSError:
            digest.update(path.as_posix().encode("utf-8") + b"\0<unreadable>\0")
    return digest.hexdigest()


def hash_observability_code(root: Path) -> str:
    base = root / "distributed-backend" / "observability" / "ci"
    if not base.exists():
        return "<unavailable>"
    return hash_files(sorted(path for path in base.rglob("*.py") if path.is_file()), root)


def hash_files(paths: Iterable[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root.resolve()).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(resolved.read_bytes())
            digest.update(b"\0")
        except (OSError, ValueError):
            continue
    return digest.hexdigest()


def collect_tool_versions(root: Path) -> dict[str, str]:
    commands = {
        "python": [os.sys.executable, "--version"],
        "git": ["git", "--version"],
        "go": ["go", "version"],
        "rustc": ["rustc", "--version"],
        "cargo": ["cargo", "--version"],
        "encore": ["encore", "version"],
        "buf": ["buf", "--version"],
        "terraform": ["terraform", "version", "-json"],
        "docker": ["docker", "version", "--format", "{{json .}}"],
        "kubectl": ["kubectl", "version", "--client=true", "-o", "json"],
    }
    versions = {name: _capture(argv, root) for name, argv in commands.items()}
    versions["os"] = platform.platform()
    versions["os.name"] = os.name
    versions["architecture"] = platform.machine()
    return versions


def _untracked_files(root: Path) -> list[Path]:
    output = _git(root, "ls-files", "--others", "--exclude-standard", "-z", *SOURCE_PATHSPEC)
    if not output or output.startswith("<"):
        return []
    return [Path(item) for item in output.split("\0") if item]


def _git(root: Path, *args: str) -> str:
    output = _git_bytes(root, *args)
    return output.decode("utf-8", errors="replace").strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            return f"<error:{completed.returncode}> ".encode("utf-8") + completed.stdout
        return completed.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<unavailable:{type(exc).__name__}>".encode("utf-8")


def _capture(argv: list[str], cwd: Path) -> str:
    if not shutil.which(argv[0]) and not Path(argv[0]).exists():
        return "<unavailable>"
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else f"<error:{result.returncode}> {result.stdout.strip()}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<unavailable:{type(exc).__name__}>"
