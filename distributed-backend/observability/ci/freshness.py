"""Freshness checks that compare run provenance with the current repository."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provenance import collect_git_state


EXACT = "EXACT"
ANCESTOR = "ANCESTOR"
DIVERGED = "DIVERGED"
DIRTY_WORKTREE_MISMATCH = "DIRTY_WORKTREE_MISMATCH"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Freshness:
    state: str
    report_full_head_sha: str
    current_full_head_sha: str
    report_dirty: bool | None
    current_dirty: bool | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "report_full_head_sha": self.report_full_head_sha,
            "current_full_head_sha": self.current_full_head_sha,
            "report_dirty": self.report_dirty,
            "current_dirty": self.current_dirty,
            "reason": self.reason,
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
    report_sha = str(report_provenance.get("full_head_sha") or report_provenance.get("github_sha") or "")
    current_sha = str(current.get("full_head_sha") or "")
    report_dirty_value = report_provenance.get("worktree_dirty")
    current_dirty_value = current.get("worktree_dirty")
    report_dirty = bool(report_dirty_value) if report_dirty_value is not None else None
    current_dirty = bool(current_dirty_value) if current_dirty_value is not None else None
    if not report_sha or not current_sha or report_sha.startswith("<") or current_sha.startswith("<"):
        return Freshness(UNKNOWN, report_sha, current_sha, report_dirty, current_dirty, "missing or invalid SHA")
    if report_sha == current_sha:
        if report_dirty != current_dirty:
            return Freshness(
                DIRTY_WORKTREE_MISMATCH,
                report_sha,
                current_sha,
                report_dirty,
                current_dirty,
                "same commit SHA but dirty-worktree state differs",
            )
        report_fp = str(report_provenance.get("worktree_diff_fingerprint_if_dirty") or "")
        current_fp = str(current.get("worktree_diff_fingerprint_if_dirty") or "")
        if report_dirty and report_fp != current_fp:
            return Freshness(
                DIRTY_WORKTREE_MISMATCH,
                report_sha,
                current_sha,
                report_dirty,
                current_dirty,
                "same commit SHA but dirty-worktree fingerprint differs",
            )
        return Freshness(EXACT, report_sha, current_sha, report_dirty, current_dirty, "run provenance matches current repository state")
    if _is_ancestor(root, report_sha, current_sha):
        return Freshness(ANCESTOR, report_sha, current_sha, report_dirty, current_dirty, "run SHA is an ancestor of current HEAD")
    if _is_ancestor(root, current_sha, report_sha):
        return Freshness(DIVERGED, report_sha, current_sha, report_dirty, current_dirty, "run SHA is newer than or on another lineage from current HEAD")
    return Freshness(DIVERGED, report_sha, current_sha, report_dirty, current_dirty, "run SHA is not on the current HEAD ancestry")


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
