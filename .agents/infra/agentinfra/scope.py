from __future__ import annotations

from copy import deepcopy
import fnmatch
import hashlib
import json
from pathlib import PurePosixPath
import re
from pathlib import Path
from typing import Iterable


PHASES = {"TEST_DESIGN", "IMPLEMENTATION"}
CHANGE_DIMENSIONS = {"files", "lines_added", "lines_deleted", "generated_churn", "lockfile_churn"}
SEMANTIC_DIMENSIONS = {
    "public_apis",
    "public_types",
    "dependencies",
    "unsafe_blocks",
    "threads_executors",
    "global_mutable_state",
    "feature_flags",
    "environment_variables",
    "network_ports",
    "external_services",
    "persistent_formats",
    "database_objects",
    "operational_requirements",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STATIC_SCOPE_FIELDS = {
    "schema",
    "allow",
    "deny",
    "test_paths",
    "production_paths",
    "generated_paths",
    "reference_paths",
    "user_dirty",
    "nested_repositories",
    "governance_digest",
    "digest",
}


class ScopeError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normalize(value: str, *, pattern: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScopeError("scope path must be a non-empty string")
    text = value.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if path.is_absolute() or ":" in path.parts[0] or ".." in path.parts:
        raise ScopeError(f"scope path is absolute or traversing: {value!r}")
    if not pattern and any(marker in text for marker in ("*", "?", "[")):
        raise ScopeError("concrete write target must not contain a glob")
    return path.as_posix().casefold()


def _patterns(values: Iterable[str]) -> list[str]:
    return sorted({_normalize(value, pattern=True) for value in values})


def _covered(path: str, pattern: str) -> bool:
    pattern = pattern.rstrip("/")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)


def _matches_any(path: str, patterns: Iterable[str]) -> bool:
    return any(_covered(path, pattern) for pattern in patterns)


def _boundary_patterns(values: Iterable[str]) -> list[str]:
    patterns: set[str] = set()
    for supplied in values:
        value = _normalize(supplied, pattern=True).rstrip("/")
        patterns.add(value)
        if not any(marker in value for marker in ("*", "?", "[")):
            patterns.add(value + "/**")
    return sorted(patterns)


def compile_write_scope(
    *,
    allow: Iterable[str],
    deny: Iterable[str],
    test_paths: Iterable[str],
    production_paths: Iterable[str],
    generated_paths: Iterable[str],
    reference_paths: Iterable[str],
    user_dirty: Iterable[str],
    nested_repositories: Iterable[str],
    governance_digest: str,
) -> dict:
    if not isinstance(governance_digest, str) or not _SHA256_RE.fullmatch(governance_digest):
        raise ScopeError("write scope requires a canonical governance digest")
    value = {
        "schema": 2,
        "allow": _patterns(allow),
        "deny": sorted(set(_patterns(deny)) | {".agents", ".agents/**", "agents.md", "**/agents.md"}),
        "test_paths": _patterns(test_paths),
        "production_paths": _patterns(production_paths),
        "generated_paths": _patterns(generated_paths),
        "reference_paths": _boundary_patterns(reference_paths),
        "user_dirty": _boundary_patterns(user_dirty),
        "nested_repositories": _boundary_patterns(nested_repositories),
        "governance_digest": governance_digest,
    }
    value["digest"] = hashlib.sha256(_canonical(value)).hexdigest()
    return value


def validate_write_scope(scope: dict) -> None:
    if not isinstance(scope, dict) or scope.get("schema") != 2:
        raise ScopeError("invalid write-scope schema")
    if set(scope) != _STATIC_SCOPE_FIELDS:
        raise ScopeError("write scope must contain only the exact static boundary fields")
    supplied = deepcopy(scope)
    claimed = supplied.pop("digest", None)
    if claimed != hashlib.sha256(_canonical(supplied)).hexdigest():
        raise ScopeError("write-scope integrity digest mismatch")
    if not isinstance(scope.get("governance_digest"), str) or not _SHA256_RE.fullmatch(scope["governance_digest"]):
        raise ScopeError("write scope has invalid governance binding")


def _static_scope_allows(scope: dict, path: str, *, phase: str) -> bool:
    try:
        validate_write_scope(scope)
        target = _normalize(path)
    except ScopeError:
        return False
    if phase not in PHASES:
        return False
    parts = target.split("/")
    if (parts and parts[0] == ".agents") or (parts and parts[-1] == "agents.md"):
        return False
    boundaries = (
        scope["deny"],
        scope["reference_paths"],
        scope["user_dirty"],
        scope["nested_repositories"],
    )
    if any(_matches_any(target, patterns) for patterns in boundaries):
        return False
    if not _matches_any(target, scope["allow"]):
        return False
    if phase == "TEST_DESIGN":
        return _matches_any(target, scope["test_paths"]) and not _matches_any(target, scope["production_paths"])
    return _matches_any(target, scope["production_paths"]) and not _matches_any(target, scope["test_paths"])


def write_authorized(
    root: Path,
    path: str,
    *,
    task_id: str | None = None,
) -> bool:
    """Derive managed-write authority exclusively from canonical task state."""

    try:
        from .assurance import validate_tdd_cycle
        from .governance import verify_governance
        from .state_store import StateStore
        from .tdd_runtime import _current_snapshots

        project = Path(root).resolve(strict=True)
        task = StateStore(project).load(task_id)
        precheck = task.get("precheck", {})
        scope = precheck.get("write_scope")
        validate_write_scope(scope)
        verified = verify_governance(project, precheck.get("governance_snapshot"))
        if verified.get("digest") != scope.get("governance_digest"):
            return False
        state = task.get("state")
        if state == "TEST_DESIGN":
            return _static_scope_allows(scope, path, phase="TEST_DESIGN")
        if state not in {"IMPLEMENT", "REMEDIATE"} or not _static_scope_allows(
            scope, path, phase="IMPLEMENTATION"
        ):
            return False
        tdd = task.get("tdd", {})
        active = tdd.get("active_cycle_id")
        cycle = next(
            (
                item
                for item in tdd.get("cycles", [])
                if isinstance(item, dict) and item.get("cycle_id") == active
            ),
            None,
        )
        if not isinstance(cycle, dict):
            return False
        validate_tdd_cycle(cycle)
        baseline_epoch = cycle.get("baseline_epoch")
        if (
            cycle.get("authority_source") != "FRAMEWORK_OBSERVED"
            or cycle.get("status") not in {"RED_OBSERVED", "CHARACTERIZATION_OBSERVED", "TEST_FIRST_OBSERVED"}
            or tdd.get("baseline_observed_by_framework") is not True
            or cycle.get("write_scope_digest") != scope.get("digest")
            or cycle.get("governance_digest") != verified.get("digest")
            or not isinstance(baseline_epoch, int)
            or baseline_epoch + 1 != task.get("change_epoch")
            or cycle.get("test_contract_digest") != cycle.get("frozen_test_contract_digest")
            or cycle.get("oracle_digest") != cycle.get("frozen_oracle_digest")
            or cycle.get("adapter_digest") != cycle.get("frozen_adapter_digest")
        ):
            return False
        current = _current_snapshots(project, cycle)
        designed = cycle.get("design_snapshots", {})
        if any(
            current[name].get("digest") != designed.get(name, {}).get("digest")
            for name in ("tests", "oracles", "user_dirty")
        ):
            return False
        latest = next(
            (
                item
                for item in reversed(task.get("transitions", []))
                if item.get("to") in {"IMPLEMENT", "REMEDIATE"}
            ),
            None,
        )
        return bool(
            isinstance(latest, dict)
            and latest.get("to") == state
            and latest.get("epoch") == task.get("change_epoch")
        )
    except (RuntimeError, OSError, KeyError, TypeError, ValueError):
        return False


def assert_write_allowed(root: Path, path: str, *, task_id: str | None = None) -> None:
    if not write_authorized(root, path, task_id=task_id):
        raise ScopeError(f"canonical task state does not authorize managed write: {path}")


def _enforce_budget(allowed: dict, observed: dict, dimensions: set[str], label: str) -> dict:
    if not isinstance(allowed, dict) or not isinstance(observed, dict):
        raise ScopeError(f"{label} budget and observation must be objects")
    unknown = sorted((set(allowed) | set(observed)) - dimensions)
    if unknown:
        raise ScopeError(f"unknown {label} budget dimensions: {', '.join(unknown)}")
    report: dict[str, dict] = {}
    for field in sorted(dimensions):
        limit = allowed.get(field, 0)
        actual = observed.get(field, 0)
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
            raise ScopeError(f"{label} budget {field} must be null or non-negative")
        if not isinstance(actual, int) or isinstance(actual, bool) or actual < 0:
            raise ScopeError(f"observed {label} {field} must be non-negative")
        if limit is not None and actual > limit:
            raise ScopeError(f"{label} budget exceeded for {field}: observed {actual}, allowed {limit}")
        report[field] = {"observed": actual, "allowed": limit, "ok": True}
    return report


def enforce_change_budget(budget: dict, observed: dict) -> dict:
    return _enforce_budget(budget, observed, CHANGE_DIMENSIONS, "change")


def enforce_semantic_budget(budget: dict, observed: dict) -> dict:
    return _enforce_budget(budget, observed, SEMANTIC_DIMENSIONS, "semantic")
