"""Behavioral model derived from actual original and refactored workflow YAML."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import yaml


@dataclass(frozen=True)
class Job:
    name: str
    needs: tuple[str, ...]
    timeout: int
    always: bool = False
    matrix_fail_fast: bool | None = None
    matrix_rows: tuple[str, ...] = ()


def _needs(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def load_graph(path: Path) -> dict[str, Job]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    return {
        name: Job(
            name=name,
            needs=_needs(spec.get("needs")),
            timeout=int(spec.get("timeout-minutes", 360)),
            always="always()" in str(spec.get("if", "")),
            matrix_fail_fast=(spec.get("strategy") or {}).get("fail-fast"),
            matrix_rows=tuple(
                str(row.get("provider", ""))
                for row in ((spec.get("strategy") or {}).get("matrix") or {}).get("include", [])
            ),
        )
        for name, spec in jobs.items()
    }


def would_run(graph: dict[str, Job], name: str, outcomes: dict[str, str]) -> bool:
    job = graph[name]
    if job.always:
        return True
    return all(outcomes.get(dep) == "success" for dep in job.needs)


def release_allowed(event: str, ref: str, sha: str, e2e_result: str, o11y_result: str, verified_sha: str) -> bool:
    return (
        event == "push"
        and ref == "refs/heads/main"
        and e2e_result == "success"
        and o11y_result == "success"
        and verified_sha == sha
    )




def new_release_allowed(event: str, ref: str, sha: str, reliability_result: str, e2e_result: str, o11y_result: str, verified_sha: str) -> bool:
    return (
        reliability_result == "success"
        and release_allowed(event, ref, sha, e2e_result, o11y_result, verified_sha)
    )

def root_jobs(graph: dict[str, Job]) -> set[str]:
    return {name for name, job in graph.items() if not job.needs}


def executable_runs_in_workflow(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"^\s*run:\s*(.+?)\s*$", text, re.M)]


PRODUCER_JOBS = (
    "proto",
    "go",
    "rust-trade-settlement",
    "terraform",
    "kubernetes",
    "python",
    "architecture",
    "gui-contract",
    "security",
    "canonical-regression",
    "e2e",
)


def paths() -> tuple[Path, Path]:
    tests = Path(__file__).resolve().parent
    original = tests / "fixtures" / "original_verify.yaml"
    refactored = tests.parents[1] / "workflows" / "verify.yaml"
    return original, refactored


ORIGINAL_PATH, NEW_PATH = paths()
ORIGINAL = load_graph(ORIGINAL_PATH)
NEW = load_graph(NEW_PATH)
