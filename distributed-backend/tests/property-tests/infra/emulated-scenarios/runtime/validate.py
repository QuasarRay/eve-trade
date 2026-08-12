#!/usr/bin/env python3
"""Fail-closed deterministic validators for catalog and emulation artifacts."""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from catalog import PROPERTY_ROOT, SCENARIO_ROOT, UNIVERSAL_CRITERIA, input_digests, load_catalog
from scenario_definitions import IMPLEMENTED_PENDING


CLASSIFICATION_PATH = PROPERTY_ROOT / "classification.json"
REGISTRY_PATH = SCENARIO_ROOT / "scenario-registry.json"
ALLOWED_EXECUTION_MODES = {"STATIC_OR_DIRECT", "REAL_DEPLOYMENT_EMULATION", "UNRESOLVED"}
ALLOWED_PROVENANCE = {
    "SCENARIO_INPUT",
    "ANYSYSTEM_CONTROLLER",
    "DAGGER_ORCHESTRATOR",
    "LITMUS_CONTROL_PLANE",
    "KUBERNETES_CONTROL_PLANE",
    "REAL_APPLICATION",
    "REAL_DATABASE",
    "REAL_BROKER",
    "INDEPENDENT_NETWORK_PROBE",
    "INDEPENDENT_RESOURCE_PROBE",
    "PHYSICAL_CLOCK",
    "INJECTED_APPLICATION_CLOCK",
}
FORBIDDEN_OUTPUT_FIELDS = {
    "business_valid",
    "invariant_satisfied",
    "test_passed",
    "correct_trade_state",
    "should_accept",
    "should_reject",
}


class ValidationFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON document must be an object: {path}")
    return value


def validate_catalog() -> dict[str, int]:
    catalog = load_catalog()
    classification = load_json(CLASSIFICATION_PATH)
    records = classification.get("records")
    require(isinstance(records, list), "classification records must be a list")
    names = [entry.name for entry in catalog]
    classified_names = [record.get("name") for record in records]
    require(len(classified_names) == len(set(classified_names)), "classification contains duplicate original names")
    require(names == classified_names, "classification does not preserve exact source order and identity")

    current_digests = input_digests()
    bound = classification.get("authoritative_inputs", {})
    for key in (
        "test_rules_sha256",
        "safety_criteria_sha256",
        "source_catalog_sha256",
        "source_tree_sha256",
    ):
        require(bound.get(key) == current_digests[key], f"classification input digest is stale: {key}")

    first_false_counts: Counter[str] = Counter()
    for record in records:
        name = record["name"]
        require(record.get("leaf_category"), f"{name}: missing leaf category")
        require(record.get("oracle_inference") in {"SAFE", "UNSAFE"}, f"{name}: invalid safety result")
        trace = record.get("safety_evaluation")
        require(isinstance(trace, list) and trace, f"{name}: missing ordered safety evaluation")
        false_criteria = [item for item in trace if item.get("result") is not True]
        if record["oracle_inference"] == "UNSAFE":
            require(len(false_criteria) >= 1, f"{name}: unsafe without a false criterion")
            first = false_criteria[0]
            require(
                record.get("primary_false_reason") == first.get("reason_code"),
                f"{name}: primary reason is not the first false criterion",
            )
            require(
                record.get("primary_false_criterion_text") == first.get("criterion_text"),
                f"{name}: primary criterion text is not exact",
            )
            first_false_counts[str(first["reason_code"])] += 1
        else:
            require(not false_criteria, f"{name}: SAFE record contains a false/unknown criterion")
            require(record.get("primary_false_reason") is None, f"{name}: SAFE record has unsafe reason")
            require(record.get("representation") in {"ATOMIC", "COMPOSITE"}, f"{name}: SAFE record lacks representation")
        if record.get("representation") == "ATOMIC" and record["oracle_inference"] == "SAFE":
            require(record.get("execution_mode") in ALLOWED_EXECUTION_MODES, f"{name}: invalid execution mode")
        if record.get("execution_mode") == "REAL_DEPLOYMENT_EMULATION":
            require(record.get("emulation_scenario_id"), f"{name}: emulation mode has no canonical scenario")
        require(
            not (
                record.get("test_status") == "IMPLEMENTED"
                and record.get("execution_mode") == "UNRESOLVED"
            ),
            f"{name}: unresolved contract is executable",
        )

    derived = classification.get("derived_business_tests")
    require(isinstance(derived, list), "derived business-test list is missing")
    derived_names = [record.get("name") for record in derived]
    require(len(derived_names) == len(set(derived_names)), "derived child names are not unique")
    require(not set(derived_names) & set(names), "derived child silently reuses an original identity")
    for child in derived:
        require(child.get("derived_from") in set(names), f"orphan derived child {child.get('name')}")
        require(child.get("oracle_inference") == "SAFE", f"derived child is not safe: {child.get('name')}")
        require(child.get("representation") == "ATOMIC", f"derived child is not atomic: {child.get('name')}")

    classified_by_name = {record["name"]: record for record in [*records, *derived]}
    explicit_business_sources: dict[str, str] = {}
    for source_path, function_names in _python_test_functions().items():
        if not source_path.startswith("e2e/"):
            continue
        for function_name in function_names:
            require(function_name not in explicit_business_sources, f"duplicate explicit business function {function_name}")
            require(function_name in classified_by_name, f"explicit pytest identity is not classified: {function_name}")
            record = classified_by_name[function_name]
            require(record.get("oracle_inference") == "SAFE", f"unsafe contract was implemented: {function_name}")
            require(record.get("representation") == "ATOMIC", f"composite parent was implemented: {function_name}")
            require(record.get("implementation_path") == source_path, f"implementation path is stale: {function_name}")
            require(
                str(record.get("test_status", "")).startswith("IMPLEMENTED_"),
                f"explicit business function has non-implemented status: {function_name}",
            )
            explicit_business_sources[function_name] = source_path

    counts = classification.get("counts", {})
    require(counts.get("total_original_business_test_names") == len(names), "classification total is stale")
    require(
        counts.get("safe_names", 0) + counts.get("unsafe_names", 0) == len(names),
        "SAFE/UNSAFE counts do not partition original names",
    )
    require(
        classification.get("unsafe_by_first_false_reason_code") == dict(sorted(first_false_counts.items())),
        "unsafe first-false summary differs from records",
    )
    require(
        classification.get("scenario_selector_overlap_diagnostics") == [],
        "one or more atomic business tests match multiple canonical emulation scenarios",
    )
    return {
        "original_names": len(names),
        "derived_names": len(derived_names),
        "safe": counts.get("safe_names", 0),
        "unsafe": counts.get("unsafe_names", 0),
        "implemented_business_tests": len(explicit_business_sources),
    }


def _python_test_functions() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for path in sorted(PROPERTY_ROOT.rglob("*.py")):
        if "e2e-deprecated" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            raise ValidationFailure(f"Python syntax error in {path}: {exc}") from exc
        names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        ]
        if names:
            result[path.relative_to(PROPERTY_ROOT).as_posix()] = names
    return result


def _future_names(path: Path) -> list[str]:
    return re.findall(r"^- `(test_[a-z0-9_]+)`$", path.read_text(encoding="utf-8"), re.MULTILINE)


def _looks_emulation_safe(name: str, scenario_id: str) -> bool:
    if not re.fullmatch(r"test_[a-z0-9_]+", name):
        return False
    if not name.startswith(f"test_{scenario_id}_"):
        return False
    forbidden = ("_works", "_is_correct", "_properly", "_reliable")
    if any(token in name for token in forbidden):
        return False
    # Curated names must expose a subject and an assertive relation; this gate
    # complements, rather than replaces, the recorded U01-U09 review.
    relations = (
        "_deploys_", "_records_", "_identifies_", "_remains_", "_replay_",
        "_emits_", "_does_not_", "_executes_", "_propagates_", "_removes_",
        "_stays_", "_uses_", "_starts_", "_targets_", "_progression_",
        "_cannot_", "_exports_", "_contains_", "_changing_", "_activates_",
        "_failure_", "_effect_", "_is_absent_",
        "_inherits_", "_is_invalid_",
        "_resolve_", "_reports_", "_applies_", "_identify_", "_are_recorded_",
    )
    return any(token in name for token in relations)


def validate_scenarios() -> dict[str, int]:
    classification = load_json(CLASSIFICATION_PATH)
    registry = load_json(REGISTRY_PATH)
    scenarios = registry.get("scenarios")
    require(isinstance(scenarios, list), "scenario registry records must be a list")
    ids = [record.get("scenario_id") for record in scenarios]
    require(len(ids) == len(set(ids)), "scenario registry contains duplicate IDs")
    registered = set(ids)
    litmus_contracts = load_json(PROPERTY_ROOT / "infra" / "litmus-contracts.json")
    pinned_experiments = {
        item["fault_injection"]["experiment"] for item in litmus_contracts.get("contracts", [])
    }
    implemented_context_independent = {
        record["name"]
        for record in [*classification["records"], *classification["derived_business_tests"]]
        if record.get("context") == "CONTEXT_INDEPENDENT"
        and str(record.get("test_status", "")).startswith("IMPLEMENTED_")
    }

    expected_consumers: dict[str, set[str]] = {scenario_id: set() for scenario_id in registered}
    for record in [*classification["records"], *classification["derived_business_tests"]]:
        if record.get("execution_mode") == "REAL_DEPLOYMENT_EMULATION" and record.get("representation") == "ATOMIC":
            scenario_id = record.get("emulation_scenario_id")
            require(scenario_id in registered, f"{record['name']}: maps to unregistered scenario {scenario_id}")
            expected_consumers[str(scenario_id)].add(record["name"])

    all_future_names: set[str] = set()
    implemented = 0
    using_litmus = 0
    future_count = 0
    python_tests = _python_test_functions()
    implemented_python_names = {name for names in python_tests.values() for name in names}
    for record in scenarios:
        scenario_id = str(record["scenario_id"])
        contract_path = PROPERTY_ROOT / record["scenario_contract_path"]
        implementation_path = PROPERTY_ROOT / record["scenario_implementation_path"]
        require(contract_path.is_file(), f"{scenario_id}: scenario-contract.md missing")
        require(contract_path.name == "scenario-contract.md", f"{scenario_id}: incorrect contract filename")
        require(implementation_path.is_file(), f"{scenario_id}: scenario implementation missing")
        implementation = load_json(implementation_path)
        require(implementation.get("scenario_id") == scenario_id, f"{scenario_id}: implementation identity mismatch")
        require(implementation.get("scenario_revision"), f"{scenario_id}: missing scenario revision")
        require(implementation.get("AnySystem_revision"), f"{scenario_id}: missing AnySystem revision")
        require(implementation.get("uses_controller_logical_ticks") is True, f"{scenario_id}: logical tick metadata missing")
        plan = implementation.get("action_plan")
        require(isinstance(plan, list) and bool(plan), f"{scenario_id}: controller plan is empty")
        for action in plan:
            require(isinstance(action, dict), f"{scenario_id}: malformed controller action")
            action_id = action.get("action_id")
            policy = action.get("adapter_policy")
            require(isinstance(policy, dict), f"{scenario_id}/{action_id}: adapter policy is absent")
            allowed_fields = policy.get("request_allowed_fields")
            required_fields = policy.get("request_required_fields")
            fixed_fields = policy.get("request_fixed_fields")
            require(isinstance(allowed_fields, list), f"{scenario_id}/{action_id}: request allowlist is absent")
            require(isinstance(required_fields, list), f"{scenario_id}/{action_id}: required request fields are absent")
            require(isinstance(fixed_fields, dict), f"{scenario_id}/{action_id}: fixed request fields are absent")
            require(
                set(required_fields) <= set(allowed_fields),
                f"{scenario_id}/{action_id}: required request field is not allowlisted",
            )
            require(
                set(fixed_fields) <= set(required_fields),
                f"{scenario_id}/{action_id}: fixed request field is not required",
            )
            if str(action.get("kind", "")).startswith("LITMUS_"):
                require(policy.get("adapter") == "LITMUS", f"{scenario_id}/{action_id}: Litmus adapter is not mandatory")
                require(
                    fixed_fields.get("experiment") == action.get("litmus_experiment"),
                    f"{scenario_id}/{action_id}: invocation can substitute a different Litmus experiment",
                )
                target_policy = fixed_fields.get("target")
                require(target_policy, f"{scenario_id}/{action_id}: exact Litmus target policy is absent")
                if target_policy == "${ANYSYSTEM_SELECTED_VARIANT}":
                    variants = action.get("seeded_variants")
                    require(isinstance(variants, list) and bool(variants), f"{scenario_id}/{action_id}: target variants are absent")
                    require(
                        all(
                            isinstance(variant, dict)
                            and set(variant) == {"kind", "selector"}
                            and all(isinstance(value, str) and value for value in variant.values())
                            for variant in variants
                        ),
                        f"{scenario_id}/{action_id}: target variant is not an exact Kubernetes kind/selector",
                    )
                else:
                    require(
                        isinstance(target_policy, dict)
                        and set(target_policy) == {"kind", "selector"}
                        and all(isinstance(value, str) and value for value in target_policy.values()),
                        f"{scenario_id}/{action_id}: Litmus target is not an exact Kubernetes kind/selector",
                    )
                fixed_environment = policy.get("environment_fixed_fields", {})
                require(isinstance(fixed_environment, dict), f"{scenario_id}/{action_id}: fixed Litmus environment is malformed")
                if fixed_environment:
                    require("environment" in required_fields, f"{scenario_id}/{action_id}: fixed Litmus environment is optional")
            else:
                require(
                    policy.get("adapter") in {"ACTION_SEQUENCE", "LITMUS"},
                    f"{scenario_id}/{action_id}: action adapter is not closed by the scenario",
                )
        require(
            set(implementation.get("observation_schema", {}).get("allowed_provenance_classes", ())) == ALLOWED_PROVENANCE,
            f"{scenario_id}: provenance class set is incomplete",
        )
        forbidden = set(implementation.get("observation_schema", {}).get("forbidden_derived_fields", ()))
        require(FORBIDDEN_OUTPUT_FIELDS - {"should_accept", "should_reject"} <= forbidden, f"{scenario_id}: forbidden answer fields incomplete")
        consumers = set(record.get("consuming_business_tests", ()))
        require(consumers == expected_consumers[scenario_id], f"{scenario_id}: consuming tests differ from classification")
        independent_contract = record.get("context_independent_business_tests", {})
        require(
            set(independent_contract.get("implemented_test_names", ())) == implemented_context_independent,
            f"{scenario_id}: implemented context-independent suite is incomplete",
        )
        require(
            independent_contract.get("required_in_every_implemented_scenario") is True,
            f"{scenario_id}: context-independent execution rule is missing",
        )

        chaos_actions = record.get("chaos_actions", [])
        if chaos_actions:
            using_litmus += 1
            require(len(chaos_actions) == 1, f"{scenario_id}: scenario must declare exactly one chaos behavior")
            chaos = chaos_actions[0]
            require(chaos.get("action_kind", "").startswith("LITMUS_"), f"{scenario_id}: chaos action kind is not explicit")
            require(chaos.get("action_kind") != "LITMUS_CHAOS", f"{scenario_id}: generic Litmus behavior is prohibited")
            require(chaos.get("target") and chaos.get("affected_path"), f"{scenario_id}: chaos target/path is incomplete")
            require(chaos.get("litmus_experiment") in pinned_experiments, f"{scenario_id}: Litmus primitive is not pinned")
            litmus_plan = [
                action for action in implementation.get("action_plan", [])
                if str(action.get("kind", "")).startswith("LITMUS_")
            ]
            require(len(litmus_plan) == 1, f"{scenario_id}: controller plan does not have one Litmus dispatch")
            require(
                litmus_plan[0].get("litmus_experiment") == chaos.get("litmus_experiment"),
                f"{scenario_id}: controller and registry Litmus primitives differ",
            )
            activation_policy = litmus_plan[0]["adapter_policy"]
            require(
                chaos.get("runtime_target_policy")
                == activation_policy.get("request_fixed_fields", {}).get("target"),
                f"{scenario_id}: registry and runtime Litmus target policies differ",
            )
            require(
                chaos.get("runtime_fixed_environment")
                == activation_policy.get("environment_fixed_fields", {}),
                f"{scenario_id}: registry and runtime Litmus environment policies differ",
            )
            release_plan = [action for action in plan if action.get("action_id") == "release-controlled-condition"]
            require(len(release_plan) == 1, f"{scenario_id}: Litmus release action is missing")
            release_policy = release_plan[0].get("adapter_policy", {})
            require(release_policy.get("adapter") == "LITMUS", f"{scenario_id}: Litmus release adapter is not mandatory")
            require(
                release_policy.get("request_fixed_fields") == {
                    "operation": "RELEASE",
                    "activation_action_id": "activate-scenario-chaos",
                },
                f"{scenario_id}: Litmus release is not bound to the activation action",
            )
            require(
                set(release_policy.get("request_allowed_fields", []))
                == {"operation", "activation_action_id", "independent_witness"},
                f"{scenario_id}: Litmus release accepts undeclared request fields",
            )
            require(
                "independent_witness" in release_policy.get("request_required_fields", []),
                f"{scenario_id}: Litmus release lacks an independent recovery witness",
            )
            witnesses = record.get("witnesses", {})
            require(witnesses.get("control_plane"), f"{scenario_id}: chaos lacks control-plane witness")
            require(witnesses.get("independent_effect"), f"{scenario_id}: chaos lacks independent effect witness")
            require(witnesses.get("negative_control_required") is True, f"{scenario_id}: chaos lacks negative-control requirement")

        if record.get("status") != IMPLEMENTED_PENDING:
            require(record.get("verification_test_catalog_path") is None, f"{scenario_id}: planned scenario has verification catalog")
            require(record.get("verification_coverage_path") is None, f"{scenario_id}: planned scenario has verification coverage")
            continue
        implemented += 1
        catalog_path = PROPERTY_ROOT / record["verification_test_catalog_path"]
        coverage_path = PROPERTY_ROOT / record["verification_coverage_path"]
        require(catalog_path.is_file(), f"{scenario_id}: future verification-name catalog missing")
        require(coverage_path.is_file(), f"{scenario_id}: future coverage map missing")
        names = _future_names(catalog_path)
        require(names, f"{scenario_id}: no future emulation-verification names")
        require(len(names) == len(set(names)), f"{scenario_id}: duplicate future names")
        require(all(_looks_emulation_safe(name, scenario_id) for name in names), f"{scenario_id}: future name fails safety gate")
        require(not (set(names) & all_future_names), f"{scenario_id}: future name duplicates another scenario")
        all_future_names.update(names)
        future_count += len(names)
        require(not (set(names) & implemented_python_names), f"{scenario_id}: future verification test function was implemented")
        coverage = load_json(coverage_path)
        require(coverage.get("test_function_count") == 0, f"{scenario_id}: coverage claims implemented test functions")
        future_specs = coverage.get("future_tests")
        require(isinstance(future_specs, list) and len(future_specs) == len(names), f"{scenario_id}: future safety specifications are incomplete")
        expected_universal = [(code, text) for code, text in UNIVERSAL_CRITERIA]
        for specification in future_specs:
            name = specification.get("name")
            require(specification.get("oracle_inference") == "SAFE", f"{scenario_id}/{name}: future name is not classified SAFE")
            evaluation = specification.get("safety_evaluation")
            require(isinstance(evaluation, list), f"{scenario_id}/{name}: U01-U09 evaluation is absent")
            require(
                [(item.get("reason_code"), item.get("criterion_text")) for item in evaluation] == expected_universal,
                f"{scenario_id}/{name}: U01-U09 evaluation is incomplete or out of order",
            )
            require(all(item.get("result") is True for item in evaluation), f"{scenario_id}/{name}: future name failed U01-U09")
            emulation_gate = specification.get("emulation_specific_safety_gate")
            require(
                isinstance(emulation_gate, dict) and len(emulation_gate) == 5 and all(value is True for value in emulation_gate.values()),
                f"{scenario_id}/{name}: emulation-specific safety gate is incomplete",
            )
        clauses = coverage.get("clauses")
        require(isinstance(clauses, dict) and clauses, f"{scenario_id}: coverage lacks clauses")
        require(
            set(clauses) == set(implementation.get("behavioral_clauses", {})),
            f"{scenario_id}: coverage does not exactly span every behavioral clause",
        )
        for clause, covered_by in clauses.items():
            require(isinstance(covered_by, list) and covered_by, f"{scenario_id}: uncovered behavioral clause {clause}")
            require(set(covered_by) <= set(names), f"{scenario_id}: coverage references an unknown future name")
        independent_clause = clauses.get("prerequisite.negative_control", [])
        require(independent_clause, f"{scenario_id}: critical prerequisite has no negative-control future test")

    counts = registry.get("counts", {})
    require(counts.get("canonical_scenarios_identified") == len(scenarios), "registry scenario count is stale")
    require(counts.get("canonical_scenarios_implemented") == implemented, "registry implemented count is stale")
    require(counts.get("scenarios_using_litmus") == using_litmus, "registry Litmus count is stale")
    require(
        counts.get("scenarios_with_independent_effect_witnesses") == len(scenarios),
        "registry independent-witness count is stale",
    )
    require(
        counts.get("scenarios_using_anysystem_logical_ticks") == len(scenarios),
        "registry AnySystem logical-tick count is stale",
    )
    require(
        counts.get("scenarios_using_application_visible_clock_injection") == sum(
            record.get("application_clock_mode") == "INJECTED" for record in scenarios
        ),
        "registry application clock-injection count is stale",
    )
    require(counts.get("emulation_verification_test_names_generated") == future_count, "registry future-name count is stale")
    require(counts.get("emulation_verification_test_functions_implemented") == 0, "future verification function count must be zero")
    return {
        "identified": len(scenarios),
        "implemented_pending": implemented,
        "using_litmus": using_litmus,
        "future_names": future_count,
        "future_functions": 0,
    }


def validate_determinism_metadata() -> dict[str, int]:
    registry = load_json(REGISTRY_PATH)
    required_run_fields = {
        "scenario_id",
        "scenario_revision",
        "run_id",
        "namespace",
        "AnySystem_revision",
        "AnySystem_seed",
        "controller_trace",
        "source_revision",
        "deployment_artifact_revisions",
        "witness_outcomes",
    }
    schema_path = SCENARIO_ROOT / "runtime" / "run-evidence.schema.json"
    require(schema_path.is_file(), "run-evidence schema is missing")
    schema = load_json(schema_path)
    required = set(schema.get("required", ()))
    require(required_run_fields <= required, "run-evidence schema lacks required determinism metadata")
    require(
        registry.get("AnySystem", {}).get("role") == "deterministic scenario controller only",
        "AnySystem role boundary is not explicit",
    )
    return {"required_run_fields": len(required_run_fields)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = {
        "schema_version": "eve-trade.emulation-validation/v1",
        "business_catalog": validate_catalog(),
        "emulation_scenarios": validate_scenarios(),
        "determinism_metadata": validate_determinism_metadata(),
        "status": "passed",
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
