"""Stable run identity and mandatory artifact initialization."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .provenance import (
    build_provenance_envelope,
    collect_git_state,
    collect_run_provenance,
    collect_tool_versions as collect_provenance_tool_versions,
    source_stability_from_provenance,
    split_provenance_envelope,
    utc_now,
)
from .redaction import redact_mapping
from .run_index import record_from_provenance, update_run_index
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
    full_head_sha: str = ""
    short_head_sha: str = ""
    commit_subject: str = ""
    branch: str = ""
    worktree_dirty: bool = False
    worktree_diff_fingerprint_if_dirty: str = ""
    status: str = "IN_PROGRESS"
    finished_at: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    start_provenance: dict[str, Any] = field(default_factory=dict)
    finish_provenance: dict[str, Any] = field(default_factory=dict)
    source_stability: str = "UNKNOWN"

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
        if (candidate / ".git").exists() or (candidate / "encore.app").exists() or (candidate / "go.mod").exists():
            return candidate
    return current


def create_run_context(repo_root: Path | None = None, *, run_id: str | None = None) -> RunContext:
    root = repository_root(repo_root)
    git = collect_git(root)
    now = datetime.now(timezone.utc)
    resolved_id = run_id or _derive_run_id(now, git.get("sha", "unknown"))
    run_dir = root / ".o11y" / "runs" / resolved_id
    started_at = now.isoformat()
    start_provenance = collect_run_provenance(root, run_id=resolved_id, run_started_at=started_at)
    provenance = build_provenance_envelope(
        run_id=resolved_id,
        start_provenance=start_provenance,
        source_stability="UNKNOWN",
        run_status="IN_PROGRESS",
    )
    context = RunContext(
        run_id=resolved_id,
        run_dir=run_dir,
        repo_root=root,
        started_at=started_at,
        environment=os.getenv("OBSERVABILITY_ENV") or ("github-actions" if os.getenv("GITHUB_ACTIONS") else "local"),
        github_run_id=os.getenv("GITHUB_RUN_ID", ""),
        github_run_attempt=os.getenv("GITHUB_RUN_ATTEMPT", ""),
        github_workflow=os.getenv("GITHUB_WORKFLOW", ""),
        github_job=os.getenv("GITHUB_JOB", ""),
        github_sha=os.getenv("GITHUB_SHA", "") or git.get("sha", ""),
        github_ref=os.getenv("GITHUB_REF", ""),
        full_head_sha=start_provenance["full_head_sha"],
        short_head_sha=start_provenance["short_head_sha"],
        commit_subject=start_provenance["commit_subject"],
        branch=start_provenance["branch"],
        worktree_dirty=bool(start_provenance["worktree_dirty"]),
        worktree_diff_fingerprint_if_dirty=start_provenance["worktree_diff_fingerprint_if_dirty"],
        status="IN_PROGRESS",
        provenance=provenance,
        start_provenance=start_provenance,
        finish_provenance={},
        source_stability="UNKNOWN",
    )
    storage = RunStorage(run_dir)
    storage.write_json("run-context.json", context.to_dict())
    storage.write_json("git.json", git)
    storage.write_json("tool-versions.json", collect_tool_versions(root))
    storage.write_json("env-redacted.json", redact_mapping(dict(os.environ)))
    storage.write_json("start-provenance.json", start_provenance)
    storage.write_json("provenance.json", provenance)
    storage.write_json("run-status.json", {"run_id": resolved_id, "status": "IN_PROGRESS", "updated_at": utc_now()})
    update_run_index(root, record_from_provenance(root, provenance))
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
        full_head_sha=data.get("full_head_sha", data.get("github_sha", "")),
        short_head_sha=data.get("short_head_sha", data.get("github_sha", "")[:12]),
        commit_subject=data.get("commit_subject", ""),
        branch=data.get("branch", ""),
        worktree_dirty=bool(data.get("worktree_dirty", False)),
        worktree_diff_fingerprint_if_dirty=data.get("worktree_diff_fingerprint_if_dirty", ""),
        status=data.get("status", "UNKNOWN"),
        finished_at=data.get("finished_at", ""),
        provenance=data.get("provenance", {}),
        start_provenance=data.get("start_provenance", {}),
        finish_provenance=data.get("finish_provenance", {}),
        source_stability=data.get("source_stability", "UNKNOWN"),
    )


def collect_git(root: Path) -> dict[str, Any]:
    state = collect_git_state(root)
    sha = state["full_head_sha"]
    status_lines = state.get("worktree_status", [])
    return {
        "sha": sha,
        "short_sha": sha[:12] if sha else "",
        "full_head_sha": sha,
        "short_head_sha": state["short_head_sha"],
        "commit_subject": state["commit_subject"],
        "branch": state["branch"],
        "ref": os.getenv("GITHUB_REF", ""),
        "dirty": bool(state["worktree_dirty"]),
        "status": status_lines,
        "worktree_dirty": bool(state["worktree_dirty"]),
        "worktree_diff_fingerprint_if_dirty": state["worktree_diff_fingerprint_if_dirty"],
        "remote_origin": state["remote_origin"],
        "git.branch": state["branch"],
        "git.dirty": bool(state["worktree_dirty"]),
    }


def collect_tool_versions(root: Path) -> dict[str, Any]:
    return collect_provenance_tool_versions(root)


def finalize_run_context(
    context: RunContext,
    *,
    status: str,
    command: str = "",
    exit_code: int = 0,
    commands_executed: list[dict[str, Any]] | None = None,
    diagnosis_path: str = "",
    report_path: str = "",
    storage: RunStorage | None = None,
) -> dict[str, Any]:
    storage = storage or RunStorage(context.run_dir)
    final_status = status
    finished_at = utc_now()
    finish_provenance = collect_run_provenance(
        context.repo_root,
        run_id=context.run_id,
        run_started_at=context.started_at,
        run_finished_at=finished_at,
        status=final_status,
        commands_executed=commands_executed or [],
    )
    start_provenance, _previous_finish, _previous_stability, _previous_status = split_provenance_envelope(context.provenance or {})
    if not start_provenance:
        start_provenance = context.start_provenance or {}
    source_stability = source_stability_from_provenance(start_provenance, finish_provenance)
    source_changed = source_stability == "CHANGED"
    if source_changed and final_status == "COMPLETE":
        final_status = "SOURCE_CHANGED_DURING_RUN"
        finish_provenance["run_status"] = final_status
    else:
        finish_provenance["run_status"] = final_status
    provenance = build_provenance_envelope(
        run_id=context.run_id,
        start_provenance=start_provenance,
        finish_provenance=finish_provenance,
        source_stability=source_stability,
        run_status=final_status,
    )
    provenance["source_changed_during_run"] = source_changed
    context_value = context.to_dict()
    context_value.update(
        {
            "status": final_status,
            "finished_at": finished_at,
            "provenance": provenance,
            "start_provenance": start_provenance,
            "finish_provenance": finish_provenance,
            "source_stability": source_stability,
            "full_head_sha": start_provenance.get("full_head_sha", ""),
            "short_head_sha": start_provenance.get("short_head_sha", ""),
            "commit_subject": start_provenance.get("commit_subject", ""),
            "branch": start_provenance.get("branch", ""),
            "worktree_dirty": start_provenance.get("worktree_dirty", False),
            "worktree_diff_fingerprint_if_dirty": start_provenance.get("worktree_diff_fingerprint_if_dirty", ""),
        }
    )
    storage.write_json("run-context.json", context_value)
    storage.write_json("start-provenance.json", start_provenance)
    storage.write_json("finish-provenance.json", finish_provenance)
    storage.write_json("provenance.json", provenance)
    storage.write_json(
        "run-status.json",
        {
            "run_id": context.run_id,
            "status": final_status,
            "updated_at": finished_at,
            "source_changed_during_run": source_changed,
            "source_stability": source_stability,
            "exit_code": exit_code,
        },
    )
    record = record_from_provenance(
        context.repo_root,
        provenance,
        command=command,
        exit_code=exit_code,
        diagnosis_path=diagnosis_path,
        report_path=report_path,
    )
    update_run_index(context.repo_root, record)
    return provenance


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


def _source_changed(start: dict[str, Any], finish: dict[str, Any]) -> bool:
    keys = ("full_head_sha", "worktree_dirty", "worktree_diff_fingerprint_if_dirty")
    return any(start.get(key) != finish.get(key) for key in keys)
