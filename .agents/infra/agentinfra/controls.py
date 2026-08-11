from __future__ import annotations

import re
from typing import Iterable

from .scope import SEMANTIC_DIMENSIONS


class ControlError(RuntimeError):
    pass


GATE_SEVERITIES = {"HARD", "REQUIRED", "ADVISORY"}


def validate_gate_waiver(
    gate: dict,
    evidence_records: dict[str, dict],
    *,
    task_id: str,
    current_epoch: int,
) -> bool:
    """Return true only for a current, externally authorized REQUIRED waiver.

    HARD gates are never waivable.  ADVISORY gates are justified omissions,
    not waivers.  The evidence ledger itself is cryptographically verified by
    the StateStore before this predicate is used.
    """

    if not isinstance(gate, dict) or gate.get("status") != "WAIVED":
        return False
    if gate.get("gate_severity") != "REQUIRED":
        return False
    if not isinstance(gate.get("waiver_reason"), str) or not gate["waiver_reason"].strip():
        return False
    evidence_id = gate.get("waiver_evidence")
    if not isinstance(evidence_id, str) or not evidence_id.startswith("E-"):
        return False
    if not isinstance(evidence_records, dict):
        return False
    record = evidence_records.get(evidence_id)
    if (
        not isinstance(record, dict)
        or record.get("schema") != 2
        or record.get("id") != evidence_id
        or record.get("task_id") != task_id
        or record.get("change_epoch") != current_epoch
        or record.get("provenance") != "external-source"
    ):
        return False
    details = record.get("details")
    if not isinstance(details, dict) or gate.get("id") not in details.get("gate_ids", []):
        return False
    authority = details.get("waiver_authority")
    return bool(
        isinstance(authority, dict)
        and authority.get("kind") in {"user", "host"}
        and authority.get("trusted") is True
        and isinstance(authority.get("identity"), str)
        and authority["identity"].strip()
    )


_ADR_FIELDS = {
    "id",
    "category",
    "decision",
    "alternatives",
    "reason",
    "coupling",
    "evidence",
    "risks",
    "compatibility_impact",
}


def _valid_nonempty_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) and item.strip() for item in value)


def _validate_adr(decision: dict) -> None:
    if not isinstance(decision, dict) or not _ADR_FIELDS <= set(decision):
        raise ControlError("architectural decision is incomplete")
    for field in ("id", "category", "decision", "reason", "compatibility_impact"):
        if not isinstance(decision.get(field), str) or not decision[field].strip():
            raise ControlError(f"architectural decision {field} must be non-empty")
    for field in ("alternatives", "coupling", "evidence", "risks"):
        if not _valid_nonempty_list(decision.get(field)):
            raise ControlError(f"architectural decision {field} must be a non-empty string array")


def require_architectural_decisions(semantic_delta: dict, decisions: list[dict]) -> dict:
    if not isinstance(semantic_delta, dict) or not isinstance(decisions, list):
        raise ControlError("semantic delta and decisions must be structured")
    unknown = sorted(set(semantic_delta) - SEMANTIC_DIMENSIONS)
    if unknown:
        raise ControlError("unknown semantic dimensions: " + ", ".join(unknown))
    by_category: dict[str, list[dict]] = {}
    for decision in decisions:
        _validate_adr(decision)
        by_category.setdefault(decision["category"], []).append(decision)
    missing = []
    for category, count in semantic_delta.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ControlError("semantic delta counts must be non-negative integers")
        if count and category not in by_category:
            missing.append(category)
    if missing:
        raise ControlError("architectural expansion lacks ADR evidence: " + ", ".join(sorted(missing)))
    return {"ok": True, "covered": sorted(category for category, count in semantic_delta.items() if count)}


def validate_dependency_delta(delta: dict, *, task_classes: Iterable[str]) -> dict:
    classes = {str(value).upper() for value in task_classes}
    if "DEPENDENCY_CHANGE" not in classes:
        raise ControlError("dependency delta requires DEPENDENCY_CHANGE classification")
    if not isinstance(delta, dict):
        raise ControlError("dependency delta must be an object")
    for field in ("name", "scope", "reason"):
        if not isinstance(delta.get(field), str) or not delta[field].strip():
            raise ControlError(f"dependency {field} must be non-empty")
    if delta["scope"] not in {"development", "production", "build"}:
        raise ControlError("dependency scope must distinguish development/production/build")
    if not isinstance(delta.get("direct"), bool):
        raise ControlError("dependency direct/transitive status must be explicit")
    if not isinstance(delta.get("lifecycle_scripts_assessed"), bool) or not delta["lifecycle_scripts_assessed"]:
        raise ControlError("dependency lifecycle/build scripts must be assessed")
    if not isinstance(delta.get("transitive_impact"), list) or any(not isinstance(item, str) for item in delta["transitive_impact"]):
        raise ControlError("dependency transitive impact must be an array")
    if not isinstance(delta.get("lockfiles"), list) or any(not isinstance(item, str) for item in delta["lockfiles"]):
        raise ControlError("dependency lockfile delta must be explicit")
    return {
        "ok": True,
        "name": delta["name"],
        "scope": delta["scope"],
        "direct": delta["direct"],
        "lockfile_count": len(delta["lockfiles"]),
    }


_TRIPWIRES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TEST_ENVIRONMENT_DETECTION", re.compile(r"PYTEST_CURRENT_TEST|UNITTEST_CURRENT_TEST", re.IGNORECASE)),
    ("TEST_STACK_DETECTION", re.compile(r"inspect\.stack|currentframe|test_", re.IGNORECASE)),
    ("SWALLOWED_FAILURE", re.compile(r"except(?:\s+[^:]+)?\s*:\s*(?:#.*\n\s*)?pass\b", re.IGNORECASE | re.DOTALL)),
    ("DISABLED_VALIDATION", re.compile(r"(?:validation|verification|checks?)_(?:enabled|required)\s*=\s*False", re.IGNORECASE)),
    ("SLEEP_FOR_ORDERING", re.compile(r"(?:time\.)?sleep\s*\([^)]*\).*?(?:force|order|synchron)", re.IGNORECASE | re.DOTALL)),
)


def scan_anti_cheating(source: str) -> list[dict]:
    if not isinstance(source, str):
        raise ControlError("anti-cheating scan input must be source text")
    findings: list[dict] = []
    for finding_id, pattern in _TRIPWIRES:
        match = pattern.search(source)
        if match is not None:
            line = source.count("\n", 0, match.start()) + 1
            findings.append({"id": finding_id, "line": line, "status": "REJECT"})
    return findings
