"""Stable run identity and mandatory artifact initialization."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .redaction import redact_mapping
from .storage import RunStorage


@dataclass(frozen=True)
class RunContext:
    run_id: str
    run_dir: Path
    repo_root: Path
    started_at: str
    environment: str
    github_run_id: str = ""
    github_run_attempt: str = ""
    github_workflow: str = ""
    github_job: str = ""
    github_sha: str = ""
    github_ref: str = ""

    @property
    def is_github_actions(self) -> bool:
        return bool(self.github_run_id)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["run_dir"] = str(self.run_dir)
        value["repo_root"] = str(self.repo_root)
        value["observability.run_id"] = self.run_id
        return value


def repository_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / "go.work").exists():
            return candidate
    return current


def create_run_context(repo_root: Path | None = None, *, run_id: str | None = None) -> RunContext:
    root = repository_root(repo_root)
    git = collect_git(root)
    now = datetime.now(timezone.utc)
    resolved_id = run_id or _derive_run_id(now, git.get("sha", "unknown"))
    run_dir = root / ".o11y" / "runs" / resolved_id
    context = RunContext(
        run_id=resolved_id,
        run_dir=run_dir,
        repo_root=root,
        started_at=now.isoformat(),
        environment=os.getenv("OBSERVABILITY_ENV") or ("github-actions" if os.getenv("GITHUB_ACTIONS") else "local"),
        github_run_id=os.getenv("GITHUB_RUN_ID", ""),
        github_run_attempt=os.getenv("GITHUB_RUN_ATTEMPT", ""),
        github_workflow=os.getenv("GITHUB_WORKFLOW", ""),
        github_job=os.getenv("GITHUB_JOB", ""),
        github_sha=os.getenv("GITHUB_SHA", "") or git.get("sha", ""),
        github_ref=os.getenv("GITHUB_REF", ""),
    )
    storage = RunStorage(run_dir)
    storage.write_json("run-context.json", context.to_dict())
    storage.write_json("git.json", git)
    storage.write_json("tool-versions.json", collect_tool_versions(root))
    storage.write_json("env-redacted.json", redact_mapping(dict(os.environ)))
    _update_latest_pointer(root, run_dir)
    return context


def load_run_context(run_dir: Path) -> RunContext:
    resolved_run_dir = run_dir.resolve()
    data = json.loads((resolved_run_dir / "run-context.json").read_text(encoding="utf-8"))
    recorded_root = Path(data["repo_root"])
    resolved_root = recorded_root if recorded_root.exists() else repository_root()
    return RunContext(
        run_id=data["run_id"],
        run_dir=resolved_run_dir,
        repo_root=resolved_root,
        started_at=data["started_at"],
        environment=data["environment"],
        github_run_id=data.get("github_run_id", ""),
        github_run_attempt=data.get("github_run_attempt", ""),
        github_workflow=data.get("github_workflow", ""),
        github_job=data.get("github_job", ""),
        github_sha=data.get("github_sha", ""),
        github_ref=data.get("github_ref", ""),
    )


def collect_git(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return _capture(["git", *args], root)

    sha = os.getenv("GITHUB_SHA") or git("rev-parse", "HEAD")
    status = git("status", "--porcelain=v1")
    remote = git("remote", "get-url", "origin")
    return {
        "sha": sha,
        "short_sha": sha[:12] if sha else "",
        "branch": git("branch", "--show-current") or os.getenv("GITHUB_REF_NAME", ""),
        "ref": os.getenv("GITHUB_REF", ""),
        "dirty": bool(status),
        "status": status.splitlines(),
        "remote_origin": remote,
        "git.branch": git("branch", "--show-current"),
        "git.dirty": bool(status),
    }


def collect_tool_versions(root: Path) -> dict[str, Any]:
    commands = {
        "python": [os.sys.executable, "--version"],
        "git": ["git", "--version"],
        "go": ["go", "version"],
        "rustc": ["rustc", "--version"],
        "cargo": ["cargo", "--version"],
        "docker": ["docker", "version", "--format", "{{.Server.Version}}"],
        "docker_compose": ["docker", "compose", "version", "--short"],
        "kubectl": ["kubectl", "version", "--client"],
    }
    versions = {name: _capture(argv, root) for name, argv in commands.items()}
    versions.update({"os": platform.platform(), "os.name": os.name, "architecture": platform.machine()})
    return versions


def _derive_run_id(now: datetime, sha: str) -> str:
    github_run = os.getenv("GITHUB_RUN_ID", "").strip()
    if github_run:
        attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
        github_sha = os.getenv("GITHUB_SHA", sha or "unknown")[:12]
        ref = os.getenv("GITHUB_REF", "")
        ref_hash = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:6]
        return f"gha-{github_run}-{attempt}-{github_sha}-{ref_hash}"
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"local-{timestamp}-{(sha or 'unknown')[:8]}-{secrets.token_hex(3)}"


def _capture(argv: list[str], cwd: Path) -> str:
    if not shutil.which(argv[0]) and not Path(argv[0]).exists():
        return "<unavailable>"
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False)
        return result.stdout.strip() if result.returncode == 0 else f"<error:{result.returncode}> {result.stdout.strip()}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<unavailable:{type(exc).__name__}>"


def _update_latest_pointer(root: Path, run_dir: Path) -> None:
    runs = root / ".o11y" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    pointer = runs / "latest-local.txt"
    pointer.write_text(str(run_dir.resolve()) + "\n", encoding="utf-8")
    symlink = runs / "latest-local"
    try:
        if symlink.is_symlink() or symlink.exists():
            if symlink.is_dir() and not symlink.is_symlink():
                return
            symlink.unlink()
        symlink.symlink_to(run_dir.resolve(), target_is_directory=True)
    except OSError:
        pass
