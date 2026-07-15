"""Freshness checks that compare run provenance with the current repository."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provenance import collect_git_state, repository_identity, split_provenance_envelope


EXACT = "EXACT"
ANCESTOR = "ANCESTOR"
DIVERGED = "DIVERGED"
DIRTY_WORKTREE_MISMATCH = "DIRTY_WORKTREE_MISMATCH"
SOURCE_CHANGED = "SOURCE_CHANGED"
UNKNOWN = "UNKNOWN"
AUTHORITATIVE_RUN_STATUSES = {"COMPLETE"}


@dataclass(frozen=True)
class Freshness:
    state: str
    report_full_head_sha: str
    current_full_head_sha: str
    report_dirty: bool | None
    current_dirty: bool | None
    reason: str
    source_stability: str = "UNKNOWN"
    run_status: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "report_full_head_sha": self.report_full_head_sha,
            "current_full_head_sha": self.current_full_head_sha,
            "report_dirty": self.report_dirty,
            "current_dirty": self.current_dirty,
            "reason": self.reason,
            "source_stability": self.source_stability,
            "run_status": self.run_status,
        }


def current_repository_state(root: Path) -> dict[str, Any]:
    return collect_git_state(root)


def classify_freshness(
    report_provenance: dict[str, Any] | None,
    *,
    root: Path,
    current_state: dict[str, Any] | None = None,
) -> Freshness:
    if not report_provenance:
        return Freshness(UNKNOWN, "", "", None, None, "missing run provenance")
    current = current_state or current_repository_state(root)
    start_provenance, _finish_provenance, source_stability, run_status = split_provenance_envelope(report_provenance)
    report_identity = repository_identity(start_provenance)
    current_identity = repository_identity(current)
    report_sha = str(report_identity.get("full_head_sha") or "")
    current_sha = str(current.get("full_head_sha") or "")
    report_dirty = report_identity.get("worktree_dirty")
    current_dirty = current_identity.get("worktree_dirty")
    if not report_sha or not current_sha or report_sha.startswith("<") or current_sha.startswith("<"):
        return Freshness(UNKNOWN, report_sha, current_sha, report_dirty, current_dirty, "missing or invalid SHA", source_stability, run_status)
    if source_stability == "CHANGED":
        return Freshness(
            SOURCE_CHANGED,
            report_sha,
            current_sha,
            report_dirty,
            current_dirty,
            "run source identity changed between start and finish; evidence cannot be exact for either revision",
            source_stability,
            run_status,
        )
    if source_stability != "UNCHANGED":
        return Freshness(
            UNKNOWN,
            report_sha,
            current_sha,
            report_dirty,
            current_dirty,
            "run source stability is unknown; evidence cannot be exact",
            source_stability,
            run_status,
        )
    if run_status != "UNKNOWN" and run_status not in AUTHORITATIVE_RUN_STATUSES:
        return Freshness(
            UNKNOWN,
            report_sha,
            current_sha,
            report_dirty,
            current_dirty,
            f"run status {run_status} is not authoritative current evidence",
            source_stability,
            run_status,
        )
    if report_sha == current_sha:
        if report_dirty != current_dirty:
            return Freshness(
                DIRTY_WORKTREE_MISMATCH,
                report_sha,
                current_sha,
                report_dirty,
                current_dirty,
                "same commit SHA but dirty-worktree state differs",
                source_stability,
                run_status,
            )
        report_fp = str(report_identity.get("worktree_diff_fingerprint_if_dirty") or "")
        current_fp = str(current_identity.get("worktree_diff_fingerprint_if_dirty") or "")
        if report_dirty and report_fp != current_fp:
            return Freshness(
                DIRTY_WORKTREE_MISMATCH,
                report_sha,
                current_sha,
                report_dirty,
                current_dirty,
                "same commit SHA but dirty-worktree fingerprint differs",
                source_stability,
                run_status,
            )
        return Freshness(
            EXACT,
            report_sha,
            current_sha,
            report_dirty,
            current_dirty,
            "run provenance matches current repository state and source was stable during the run",
            source_stability,
            run_status,
        )
    if _is_ancestor(root, report_sha, current_sha):
        return Freshness(ANCESTOR, report_sha, current_sha, report_dirty, current_dirty, "run SHA is an ancestor of current HEAD", source_stability, run_status)
    if _is_ancestor(root, current_sha, report_sha):
        return Freshness(DIVERGED, report_sha, current_sha, report_dirty, current_dirty, "run SHA is newer than or on another lineage from current HEAD", source_stability, run_status)
    return Freshness(DIVERGED, report_sha, current_sha, report_dirty, current_dirty, "run SHA is not on the current HEAD ancestry", source_stability, run_status)


def is_authoritative_current_candidate(record: dict[str, Any], current_state: dict[str, Any]) -> bool:
    """Return true only for a completed run whose start identity exactly matches now."""
    run_status = str(record.get("run_status", ""))
    if run_status not in AUTHORITATIVE_RUN_STATUSES:
        return False
    source_stability = str(record.get("source_stability") or ("UNCHANGED" if run_status == "COMPLETE" else "UNKNOWN"))
    if source_stability != "UNCHANGED":
        return False
    record_identity = {
        "full_head_sha": str(record.get("start_full_head_sha") or record.get("full_head_sha") or ""),
        "worktree_dirty": bool(record.get("start_worktree_dirty", record.get("worktree_dirty", False))),
        "worktree_diff_fingerprint_if_dirty": str(
            record.get("start_worktree_diff_fingerprint_if_dirty")
            or record.get("worktree_diff_fingerprint_if_dirty")
            or ""
        ),
    }
    current_identity = repository_identity(current_state)
    if record_identity["full_head_sha"] != current_identity["full_head_sha"]:
        return False
    if record_identity["worktree_dirty"] != current_identity["worktree_dirty"]:
        return False
    if record_identity["worktree_dirty"] and record_identity["worktree_diff_fingerprint_if_dirty"] != current_identity["worktree_diff_fingerprint_if_dirty"]:
        return False
    return True


def _is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
