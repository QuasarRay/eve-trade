from __future__ import annotations

import hashlib
import json
import re
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Iterable

from .atomic import atomic_write_json
from .constitution import constitutional_contract


class PolicyError(RuntimeError):
    pass


TASK_CLASSES = frozenset(
    {
        "BUG_FIX", "FEATURE", "REFACTOR", "REIMPLEMENTATION", "MIGRATION",
        "PERFORMANCE", "CONCURRENCY", "SECURITY", "DEPENDENCY_CHANGE",
        "BUILD_SYSTEM", "CI_CD", "INFRASTRUCTURE", "API_CHANGE", "FFI_CHANGE",
        "DATABASE_CHANGE", "DOCUMENTATION", "TEST_ONLY",
    }
)
TDD_MODES = frozenset({"RED_REQUIRED", "CHARACTERIZATION_REQUIRED", "NON_BEHAVIORAL_TEST_FIRST"})
GATE_SEVERITIES = frozenset({"HARD", "REQUIRED", "ADVISORY"})


WEAKENING_RULES: dict[str, tuple[object, str]] = {
    "independent_review": (False, "AEGIS-I007"),
    "strict_evidence": (False, "AEGIS-I009"),
    "production_path": (False, "AEGIS-I005"),
    "tdd": (False, "AEGIS-I019"),
    "test_first": (False, "AEGIS-I019"),
    "red_required": (False, "AEGIS-I019"),
    "property_testing": (False, "AEGIS-I021"),
    "allow_test_after_implementation": (True, "AEGIS-I019"),
    "nested_delegation": (True, "AEGIS-I011"),
    "parallel_agents": (True, "AEGIS-I011"),
    "allow_self_waiver": (True, "AEGIS-I003"),
    "allow_test_weakening": (True, "AEGIS-I006"),
    "governance_write": (True, "AEGIS-I001"),
    "verification_optional": (True, "AEGIS-I008"),
    "parent_canonical_state": (False, "AEGIS-I012"),
    "max_reasoning": (False, "AEGIS-I013"),
    "falsification": (False, "AEGIS-I014"),
}


def _gate(gate_id: str, description: str, *, severity: str = "HARD", family: str = "constitutional") -> dict:
    if severity not in GATE_SEVERITIES:
        raise PolicyError(f"unknown gate severity: {severity}")
    return {
        "id": gate_id,
        "description": description,
        "severity": severity,
        "family": family,
        "waivable": severity != "HARD",
    }


CORE_GATES = (
    _gate("GOVERNANCE_INTEGRITY", "Governing policy and instructions match their frozen digest."),
    _gate("TEST_LAW_INTEGRITY", "Frozen tests and laws retain their definition digests."),
    _gate("EVIDENCE_HONESTY", "Claims use current evidence with an honest epistemic status."),
    _gate("PRODUCTION_PATH_TRUTH", "Production claims exercise production behavior."),
    _gate("WORKSPACE_SAFETY", "Writes remain in scope and preserve user work."),
    _gate("SEQUENTIAL_DELEGATION", "At most one child is active and no nested child exists."),
    _gate("TEST_DESIGN_COMPLETE", "The executable test/property contract is designed."),
    _gate("TEST_FIRST_CONTRACT_RECORDED", "Test definition predates behavioral implementation mutation."),
    _gate("BASELINE_EXECUTED", "The frozen contract ran against the pre-implementation baseline."),
    _gate("TEST_CONTRACT_FROZEN", "Test, oracle, and gate contracts are content-addressed and frozen."),
    _gate("IMPLEMENTATION_AUTHORIZED", "Implementation write authority follows the required baseline result."),
    _gate("GREEN_EVIDENCE_CURRENT", "The frozen contract is GREEN for the current implementation epoch."),
    _gate("PROPERTY_FALSIFICATION_COMPLETE", "Required counterexample search and negative validation completed."),
    _gate("INDEPENDENT_REVIEW_CURRENT", "Independent review binds to the current diff and evidence."),
)


CLASS_GATES: dict[str, tuple[dict, ...]] = {
    "BUG_FIX": (_gate("ORIGINAL_FAILURE_REPRODUCED", "The original defect is reproduced before remediation."), _gate("REGRESSION_GREEN", "The regression is GREEN after remediation.")),
    "FEATURE": (_gate("REQUIREMENT_CONTRACT", "Positive, negative, and boundary behavior is executable."),),
    "REFACTOR": (_gate("BEHAVIOR_EQUIVALENCE", "The frozen characterization remains equivalent."),),
    "REIMPLEMENTATION": (_gate("REFERENCE_INTEGRITY", "The reference digest and read-only status are current."), _gate("DIFFERENTIAL_ORACLE", "Reference and candidate normalized observations agree.")),
    "MIGRATION": (_gate("MIGRATION_RECOVERY", "Migration is compatible, idempotent, and recovers safely."),),
    "CONCURRENCY": (_gate("CONCURRENCY_MODEL", "Ordering, cancellation, duplication, failure, and cleanup are modeled."),),
    "PERFORMANCE": (_gate("PERFORMANCE_METHODOLOGY", "Correctness, baseline, noise, and threshold are reproducible."),),
    "SECURITY": (_gate("SECURITY_ABUSE_CASES", "Hostile inputs, path boundaries, secrets, and failures close safely."),),
    "DEPENDENCY_CHANGE": (_gate("DEPENDENCY_DELTA", "Direct/transitive impact and lifecycle behavior are reviewed."),),
    "BUILD_SYSTEM": (_gate("BUILD_PLAN_VALIDATED", "Build changes render and validate without an unrequested apply."),),
    "CI_CD": (_gate("CI_PLAN_VALIDATED", "CI/CD changes render and validate without an unrequested apply."),),
    "INFRASTRUCTURE": (_gate("INFRASTRUCTURE_PLAN_VALIDATED", "Infrastructure changes plan without live apply."),),
    "API_CHANGE": (_gate("API_CONTRACT", "Public API compatibility and errors are executable."),),
    "FFI_CHANGE": (_gate("FFI_CONTRACT", "ABI, ownership, lifetime, and errors are executable."),),
    "DATABASE_CHANGE": (_gate("DATABASE_MIGRATION", "Schema/data preservation and transactional recovery are executable."),),
}


PACKS: dict[str, dict] = {
    "rust-systems": {"classes": ["SECURITY"], "gates": ["MEMORY_SAFETY", "UNSAFE_BOUNDARY_AUDIT"]},
    "rust-concurrency": {"classes": ["CONCURRENCY"], "gates": ["NO_SLEEP_SYNCHRONIZATION", "CANCELLATION_SHUTDOWN", "BACKPRESSURE"]},
    "rust-ffi": {"classes": ["FFI_CHANGE"], "gates": ["ABI_OWNERSHIP_LIFETIME"]},
    "go-service": {"classes": ["API_CHANGE"], "gates": ["SERVICE_SHUTDOWN", "REQUEST_CANCELLATION"]},
    "python-control-plane": {"classes": [], "gates": ["HYPOTHESIS_PROPERTY_SUITE"]},
    "distributed-system": {"classes": ["CONCURRENCY"], "gates": ["IDEMPOTENCY", "DUPLICATE_REORDER_RETRY", "DURABILITY_RECOVERY"]},
    "reimplementation": {"classes": ["REIMPLEMENTATION"], "gates": ["REFERENCE_INTEGRITY", "DIFFERENTIAL_ORACLE", "DIVERGENCE_REGISTRY"]},
    "compatibility-preserving-refactor": {"classes": ["REFACTOR"], "gates": ["BEHAVIOR_EQUIVALENCE", "PUBLIC_SURFACE_AUDIT"]},
    "performance-critical": {"classes": ["PERFORMANCE"], "gates": ["PERFORMANCE_METHODOLOGY", "CORRECTNESS_BEFORE_BENCHMARK"]},
    "terraform": {"classes": ["INFRASTRUCTURE"], "gates": ["TERRAFORM_VALIDATE_PLAN", "NO_LIVE_APPLY"]},
    "kubernetes": {"classes": ["INFRASTRUCTURE"], "gates": ["KUBERNETES_RENDER_VALIDATE", "ROLLOUT_RECOVERY"]},
    "database": {"classes": ["DATABASE_CHANGE"], "gates": ["DATABASE_MIGRATION", "DATA_PRESERVATION"]},
    "agent-framework": {"classes": ["SECURITY"], "gates": ["HYPOTHESIS_PROPERTY_SUITE", "CONTROL_PLANE_ADVERSARIAL_REVIEW"]},
}

TDD_GATE_IDS = {
    "RED_REQUIRED": "RED_EVIDENCE_CURRENT",
    "CHARACTERIZATION_REQUIRED": "CHARACTERIZATION_CURRENT",
    "NON_BEHAVIORAL_TEST_FIRST": "NON_BEHAVIORAL_TEST_FIRST",
}

FALSIFICATION_FAMILIES = frozenset({
    "boundary", "malformed-input", "failure-injection", "repetition",
    "concurrency", "cancellation", "security-abuse",
    "compatibility-differential", "mutation", "recovery",
})


def _required_falsification_families(classes: set[str]) -> list[str]:
    required = {"boundary"}
    if classes & {"BUG_FIX", "MIGRATION", "DEPENDENCY_CHANGE", "BUILD_SYSTEM", "INFRASTRUCTURE"}:
        required.add("failure-injection")
    if "CONCURRENCY" in classes:
        required.update(("concurrency", "cancellation", "repetition"))
    if "SECURITY" in classes:
        required.update(("malformed-input", "security-abuse"))
    if classes & {"REIMPLEMENTATION", "API_CHANGE", "FFI_CHANGE"}:
        required.add("compatibility-differential")
    return sorted(required)


def canonical_hard_gate_ids() -> tuple[str, ...]:
    """Return the complete production-derived HARD-gate universe.

    This includes the three mutually exclusive TDD-mode gates because each is a
    mandatory invariant for a supported task class, even though no one compiled
    task can contain all three at once.
    """

    ids = {gate["id"] for gate in CORE_GATES if gate["severity"] == "HARD"}
    for gates in CLASS_GATES.values():
        ids.update(gate["id"] for gate in gates if gate["severity"] == "HARD")
    for pack in PACKS.values():
        ids.update(pack["gates"])
    ids.update(TDD_GATE_IDS.values())
    return tuple(sorted(ids))


def _expected_gates(classes: set[str], selected_packs: list[str], mode: str) -> list[dict]:
    gates: dict[str, dict] = {item["id"]: deepcopy(item) for item in CORE_GATES}
    tdd_gate_id = TDD_GATE_IDS[mode]
    gates[tdd_gate_id] = _gate(
        tdd_gate_id,
        "The task-specific TDD baseline mode has current executable evidence.",
    )
    for task_class in sorted(classes):
        for item in CLASS_GATES.get(task_class, ()):
            gates.setdefault(item["id"], deepcopy(item))
    for pack in selected_packs:
        for gate_id in PACKS[pack]["gates"]:
            gates.setdefault(
                gate_id,
                _gate(gate_id, f"Mandatory {pack} policy-pack contract.", family=f"pack:{pack}"),
            )
    return [gates[key] for key in sorted(gates)]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _walk(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key).replace("-", "_").casefold(), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def validate_project_contract(contract: dict) -> dict:
    if not isinstance(contract, dict) or contract.get("schema") != 1:
        raise PolicyError("project contract schema must be exactly 1")
    project = contract.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("name"), str) or not project["name"].strip():
        raise PolicyError("project.name must be a non-empty string")
    for key, value in _walk(contract):
        rule = WEAKENING_RULES.get(key)
        if rule is not None and value == rule[0]:
            raise PolicyError(f"{rule[1]}: project policy attempts constitutional weakening via {key}")
    policy = contract.get("policy", {})
    if not isinstance(policy, dict):
        raise PolicyError("policy must be a table/object")
    packs = policy.get("packs", [])
    if not isinstance(packs, list) or any(not isinstance(item, str) for item in packs):
        raise PolicyError("policy.packs must be a string array")
    unknown_packs = sorted(set(packs) - set(PACKS))
    if unknown_packs:
        raise PolicyError("unknown policy packs: " + ", ".join(unknown_packs))
    boundaries = contract.get("boundaries", {})
    if not isinstance(boundaries, dict):
        raise PolicyError("boundaries must be a table/object")
    for field in ("source", "generated", "immutable", "vendor"):
        values = boundaries.get(field, [])
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
            raise PolicyError(f"boundaries.{field} must be a non-empty-string array")
    return deepcopy(contract)


def load_project_contract(path: Path) -> dict:
    try:
        with Path(path).open("rb") as stream:
            value = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"invalid project contract: {exc}") from exc
    return validate_project_contract(value)


def _classify_paths(paths: Iterable[str]) -> set[str]:
    classes: set[str] = set()
    for raw in paths:
        normalized = raw.replace("\\", "/").casefold()
        name = normalized.rsplit("/", 1)[-1]
        if name in {"requirements.txt", "poetry.lock", "cargo.lock", "go.sum", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"}:
            classes.add("DEPENDENCY_CHANGE")
        if normalized.startswith(".github/") or "/ci/" in "/" + normalized:
            classes.add("CI_CD")
        if name in {"dockerfile", "makefile"} or name.endswith((".tf", ".yaml", ".yml")):
            classes.add("INFRASTRUCTURE")
        if name.endswith(".sql"):
            classes.add("DATABASE_CHANGE")
        if any(marker in normalized for marker in ("security", "auth", "credential", "secret")):
            classes.add("SECURITY")
        if normalized.startswith("tests/") or "/tests/" in "/" + normalized:
            classes.add("TEST_ONLY")
        if name.endswith(".md"):
            classes.add("DOCUMENTATION")
    return classes


def _tdd_mode(classes: set[str]) -> str:
    behavioral = classes - {"DOCUMENTATION", "TEST_ONLY"}
    if not behavioral:
        return "NON_BEHAVIORAL_TEST_FIRST"
    if behavioral == {"REFACTOR"}:
        return "CHARACTERIZATION_REQUIRED"
    return "RED_REQUIRED"


def _validate_references(references: object, *, required: bool) -> list[dict]:
    if references is None:
        references = []
    if not isinstance(references, list):
        raise PolicyError("references must be an array")
    normalized: list[dict] = []
    for item in references:
        if not isinstance(item, dict):
            raise PolicyError("reference entry must be an object")
        mandatory = ("id", "path", "revision", "sha256", "read_only", "command", "observation", "normalization", "permitted_divergence")
        if any(key not in item for key in mandatory):
            raise PolicyError("reference entry is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])) or item["read_only"] is not True:
            raise PolicyError("AEGIS-I017: reference must have a canonical digest and read_only=true")
        if not isinstance(item["command"], list) or not item["command"] or any(not isinstance(part, str) for part in item["command"]):
            raise PolicyError("reference command must be a non-empty argv array")
        normalized.append(deepcopy(item))
    if required and not normalized:
        raise PolicyError("AEGIS-I017: reimplementation requires a complete read-only reference contract")
    return normalized


def compile_contract(
    contract: dict,
    *,
    declared_classes: Iterable[str],
    changed_paths: Iterable[str],
    risk: str,
    prior_classes: Iterable[str] = (),
) -> dict:
    contract = validate_project_contract(contract)
    classes = {str(item).upper() for item in declared_classes} | {str(item).upper() for item in prior_classes}
    unknown = sorted(classes - TASK_CLASSES)
    if unknown:
        raise PolicyError("unknown task classes: " + ", ".join(unknown))
    paths = sorted(set(str(item) for item in changed_paths))
    classes |= _classify_paths(paths)
    selected_packs = sorted(set(contract.get("policy", {}).get("packs", [])))
    for pack in selected_packs:
        classes.update(PACKS[pack]["classes"])
    if not classes:
        classes.add("FEATURE")  # conservative default for an unclassified mutating task
    mode = _tdd_mode(classes)
    references = _validate_references(contract.get("references"), required="REIMPLEMENTATION" in classes)

    gates = _expected_gates(classes, selected_packs, mode)

    boundaries = deepcopy(contract.get("boundaries", {}))
    allow = sorted(set(boundaries.get("source", [])) | {"infra/property_tests/**", "infra/tests/**", "infra/law_tests/**"})
    deny = sorted(set(boundaries.get("immutable", [])) | {".agents/**", "**/AGENTS.md"})
    compiled = {
        "schema": 1,
        "constitution": constitutional_contract(),
        "project": deepcopy(contract["project"]),
        "task": {"classes": sorted(classes), "risk": risk, "tdd_mode": mode, "changed_paths": paths},
        "policy_packs": selected_packs,
        "gates": gates,
        "scope": {
            "allow": allow,
            "deny": deny,
            "immutable": sorted(set(boundaries.get("immutable", [])) | {".agents/**", "**/AGENTS.md"}),
            "generated": sorted(set(boundaries.get("generated", []))),
            "reference": sorted(item["path"] for item in references),
            "user_dirty": [],
            "nested_repositories": [],
        },
        "change_budget": {"files": None, "lines_added": None, "lines_deleted": None, "generated_churn": 0, "lockfile_churn": 0},
        "semantic_budget": {
            key: 0 for key in (
                "public_apis", "public_types", "dependencies", "unsafe_blocks", "threads_executors",
                "global_mutable_state", "feature_flags", "environment_variables", "network_ports",
                "external_services", "persistent_formats", "database_objects", "operational_requirements",
            )
        },
        "references": references,
        "commands": deepcopy(contract.get("commands", {})),
        "falsification": {"required_families": _required_falsification_families(classes)},
        "review": {"independent_required": True, "tdd_provenance_audit": True, "specialist_security": "SECURITY" in classes},
    }
    compiled["digest"] = hashlib.sha256(_canonical(compiled)).hexdigest()
    validate_compiled_policy(compiled)
    return compiled


def validate_compiled_policy(compiled: dict) -> dict:
    """Reject self-consistent policy artifacts that weaken canonical invariants."""

    if not isinstance(compiled, dict) or compiled.get("schema") != 1:
        raise PolicyError("compiled policy schema must be exactly 1")
    supplied = deepcopy(compiled)
    claimed = supplied.pop("digest", None)
    actual = hashlib.sha256(_canonical(supplied)).hexdigest()
    if claimed != actual:
        raise PolicyError("compiled policy digest mismatch")

    task = compiled.get("task")
    if not isinstance(task, dict):
        raise PolicyError("compiled policy task contract is missing")
    classes_value = task.get("classes")
    if (
        not isinstance(classes_value, list)
        or not classes_value
        or classes_value != sorted(set(classes_value))
        or any(item not in TASK_CLASSES for item in classes_value)
    ):
        raise PolicyError("compiled policy task classes are invalid")
    classes = set(classes_value)
    mode = task.get("tdd_mode")
    if mode not in TDD_MODES or mode != _tdd_mode(classes):
        raise PolicyError("compiled policy TDD mode is inconsistent with task classes")

    packs = compiled.get("policy_packs")
    if (
        not isinstance(packs, list)
        or packs != sorted(set(packs))
        or any(pack not in PACKS for pack in packs)
    ):
        raise PolicyError("compiled policy pack selection is invalid")
    required_classes = {task_class for pack in packs for task_class in PACKS[pack]["classes"]}
    if not required_classes <= classes:
        raise PolicyError("compiled policy omits a class required by a selected policy pack")

    expected_gates = _expected_gates(classes, packs, mode)
    if compiled.get("gates") != expected_gates:
        expected_ids = [gate["id"] for gate in expected_gates]
        actual_gates = compiled.get("gates")
        actual_ids = (
            [gate.get("id") for gate in actual_gates if isinstance(gate, dict)]
            if isinstance(actual_gates, list)
            else []
        )
        raise PolicyError(
            "compiled policy HARD gate registry mismatch; "
            f"missing={sorted(set(expected_ids) - set(actual_ids))} "
            f"extra={sorted(set(actual_ids) - set(expected_ids))}"
        )
    if compiled.get("constitution") != constitutional_contract():
        raise PolicyError("compiled policy constitution is not canonical")
    if compiled.get("falsification") != {
        "required_families": _required_falsification_families(classes)
    }:
        raise PolicyError("compiled policy falsification requirements are not canonical")
    if compiled.get("review") != {
        "independent_required": True,
        "tdd_provenance_audit": True,
        "specialist_security": "SECURITY" in classes,
    }:
        raise PolicyError("compiled policy review requirements are not canonical")
    scope = compiled.get("scope")
    if not isinstance(scope, dict):
        raise PolicyError("compiled policy scope is missing")
    for field in ("deny", "immutable"):
        values = scope.get(field)
        if not isinstance(values, list) or not {".agents/**", "**/AGENTS.md"} <= set(values):
            raise PolicyError(f"compiled policy {field} scope omits immutable governance")
    return deepcopy(compiled)


def write_compiled_policy(root: Path, compiled: dict) -> Path:
    root = Path(root).resolve(strict=True)
    validated = validate_compiled_policy(compiled)
    actual = validated["digest"]
    destination = root / ".aegis" / "compiled-policy" / f"{actual}.json"
    atomic_write_json(destination, validated, root=root, mode=0o600)
    return destination


def classify_instruction_source(*, kind: str, source: str, content: str) -> dict:
    normalized = str(kind).strip().casefold().replace("_", "-")
    ranks = {
        "host": 70,
        "system": 70,
        "user": 60,
        "constitution": 50,
        "project-governance": 40,
        "subsystem-governance": 30,
        "source": 10,
        "comment": 10,
    }
    rank = ranks.get(normalized, 0)
    return {
        "kind": normalized,
        "source": source,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "authoritative": rank >= 30,
        "authority_rank": rank,
    }
