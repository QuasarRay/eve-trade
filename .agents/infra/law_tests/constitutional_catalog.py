from __future__ import annotations

"""Exact constitutional-law catalog with reviewed property subsumption.

The 834-name historical backlog remains a separate exact inventory.  This
catalog records the additional constitutional and TDD law names required by
the source architecture and binds every name to executable test methods.  Each
mapped method is validated statically and observed individually in an isolated
interpreter, so a passing sibling cannot conceal its skip, failure, or error.
"""

import ast
import hashlib
import json
from pathlib import Path


def _group(
    names: tuple[str, ...],
    *,
    targets: tuple[tuple[str, str, tuple[str, ...]], ...],
    invariants: tuple[str, ...],
    boundary: str,
    tdd_mode: str = "RED_REQUIRED",
) -> dict:
    return {
        "names": names,
        "targets": targets,
        "invariants": invariants,
        "boundary": boundary,
        "tdd_mode": tdd_mode,
    }


GROUPS = (
    _group(
        (
            "test_project_policy_cannot_disable_max_reasoning",
            "test_project_policy_cannot_enable_parallel_children",
            "test_project_policy_cannot_allow_nested_delegation",
            "test_project_policy_cannot_disable_parent_canonical_state",
            "test_project_policy_cannot_disable_production_path_laws",
            "test_project_policy_cannot_disable_evidence_requirements",
            "test_project_policy_cannot_disable_governance_immutability",
            "test_project_policy_cannot_disable_independent_review",
            "test_project_policy_cannot_allow_self_waivers",
            "test_project_policy_cannot_make_hard_gates_waivable",
            "test_project_policy_cannot_allow_test_weakening",
            "test_project_policy_cannot_disable_falsification_for_substantial_work",
        ),
        targets=(("property", "test_policy_compiler_properties.py", ("test_project_policy_weakening_is_rejected_with_invariant", "test_compiler_assigns_conservative_tdd_mode_and_hard_gates")),),
        invariants=("AEGIS-I001", "AEGIS-I003", "AEGIS-I005", "AEGIS-I006", "AEGIS-I007", "AEGIS-I009", "AEGIS-I011", "AEGIS-I012", "AEGIS-I013", "AEGIS-I014"),
        boundary="project contract validation and compiled HARD-gate set",
    ),
    _group(
        (
            "test_agent_cannot_modify_agents_tree_via_direct_write",
            "test_agent_cannot_modify_agents_tree_via_relative_path",
            "test_agent_cannot_modify_agents_tree_via_case_alias",
            "test_agent_cannot_modify_agents_tree_via_symlink",
            "test_agent_cannot_modify_agents_tree_via_windows_junction",
            "test_agent_cannot_modify_agents_tree_via_rename_into_tree",
            "test_agent_cannot_modify_agents_tree_via_rename_out_of_tree",
            "test_agent_cannot_delete_agents_tree",
            "test_agent_cannot_replace_agents_parent_to_mutate_governance",
            "test_agent_cannot_modify_governance_via_codegen",
            "test_agent_cannot_modify_governance_via_formatter",
            "test_agent_cannot_modify_governance_via_supported_git_operation",
            "test_child_agent_cannot_modify_governance",
            "test_governance_digest_change_invalidates_task",
            "test_out_of_band_governance_mutation_is_detected_and_fails_closed",
        ),
        targets=(
            ("property", "test_governance_runtime_properties.py", ("test_transaction_rejects_every_agents_destination", "test_atomic_write_rejects_active_governing_instruction", "test_governance_guard_rejects_every_managed_mutation_vector")),
            ("property", "test_discovery_integrity_properties.py", ("test_governance_snapshot_detects_out_of_band_change_and_fails_closed", "test_windows_case_aliases_cannot_bypass_governance_guard", "test_symlink_alias_into_governance_is_rejected_when_supported", "test_windows_junction_alias_into_governance_is_rejected_when_supported")),
            ("property", "test_scope_controls_properties.py", ("test_governance_is_unconditionally_denied_under_aliases",)),
        ),
        invariants=("AEGIS-I001", "AEGIS-I006", "AEGIS-I010", "AEGIS-I011", "AEGIS-I015"),
        boundary="all Aegis-managed mutation endpoints plus independent governance snapshot verification",
    ),
    _group(
        (
            "test_current_codex_task_never_requires_agents_write",
            "test_framework_source_build_does_not_synchronize_into_agents",
            "test_policy_compilation_writes_only_runtime_state",
            "test_runtime_state_never_requires_agents_mutation",
        ),
        targets=(
            ("property", "test_governance_runtime_properties.py", ("test_state_store_writes_runtime_only_under_dot_aegis", "test_runtime_helpers_never_resolve_below_agents")),
            ("property", "test_release_source_properties.py", ("test_source_build_is_deterministic_and_never_writes_active_agents",)),
            ("property", "test_policy_compiler_properties.py", ("test_compilation_writes_only_content_addressed_dot_aegis_artifact",)),
            ("property", "test_operational_runtime_properties.py", ("test_operational_cli_does_not_expose_governance_mutators",)),
        ),
        invariants=("AEGIS-I001", "AEGIS-I005", "AEGIS-I010"),
        boundary="source build, policy compilation, CLI, and runtime storage paths",
    ),
    _group(
        (
            "test_mutating_task_cannot_skip_plan",
            "test_implementation_mutation_invalidates_verification",
            "test_implementation_mutation_invalidates_review",
            "test_stale_review_digest_cannot_finalize",
            "test_stale_verification_epoch_cannot_finalize",
        ),
        targets=(
            ("property", "test_tdd_lifecycle_properties.py", ("test_mutating_task_cannot_jump_triage_to_implement", "test_plan_cannot_enter_implement_before_test_design_and_baseline")),
            ("property", "test_state_assurance_integration_properties.py", ("test_green_falsification_and_review_are_enforced_as_current_state",)),
            ("unit", "test_state_machine.py", ("test_implementation_invalidates_stale_proof", "test_finalize_rejects_evidence_from_prior_epoch_even_if_state_is_tampered")),
        ),
        invariants=("AEGIS-I004", "AEGIS-I007", "AEGIS-I019", "AEGIS-I020"),
        boundary="task transition authority and current-epoch proof/review bindings",
    ),
    _group(
        (
            "test_agent_cannot_modify_existing_gate_after_failure_to_make_it_pass",
            "test_agent_cannot_downgrade_gate_severity_after_failure",
            "test_agent_cannot_self_waive_required_gate",
            "test_agent_cannot_self_waive_hard_gate",
            "test_user_waiver_requires_external_trusted_provenance",
            "test_hard_gate_cannot_be_waived",
        ),
        targets=(
            ("property", "test_policy_compiler_properties.py", ("test_compiler_assigns_conservative_tdd_mode_and_hard_gates",)),
            ("property", "test_state_assurance_integration_properties.py", ("test_gate_waiver_requires_current_external_trusted_provenance", "test_state_store_rejects_caller_labelled_required_gate_waiver")),
            ("unit", "test_state_machine.py", ("test_critical_gate_cannot_be_waived_by_a_caller_supplied_policy_string", "test_gate_and_risk_history_cannot_be_deleted_or_replaced", "test_proven_gate_requires_nonempty_evidence")),
        ),
        invariants=("AEGIS-I002", "AEGIS-I003", "AEGIS-I004", "AEGIS-I009"),
        boundary="append-only acceptance-gate history and trusted waiver provenance",
    ),
    _group(
        (
            "test_existing_contract_test_digest_must_remain_stable",
            "test_existing_law_digest_must_remain_stable",
            "test_reference_repository_digest_must_remain_stable",
        ),
        targets=(
            ("property", "test_law_runtime_properties.py", ("test_definition_change_invalidates_completion",)),
            ("property", "test_law_runner_source_layout_properties.py", ("test_portable_agents_paths_resolve_to_source_authority",)),
            ("unit", "test_laws.py", ("test_transient_protected_test_rewrite_is_detected_after_bytes_and_mtime_restore",)),
            ("property", "test_scope_controls_properties.py", ("test_user_dirty_nested_and_reference_boundaries_override_allow",)),
        ),
        invariants=("AEGIS-I006", "AEGIS-I017", "AEGIS-I020"),
        boundary="frozen definition hashes, read-only reference scope, and transient mutation watcher",
    ),
    _group(
        (
            "test_unavailable_capability_cannot_be_reported_as_pass",
            "test_untested_cannot_be_reported_as_proven",
            "test_inferred_cannot_be_reported_as_observed",
            "test_assumed_cannot_be_reported_as_proven_without_evidence",
        ),
        targets=(
            ("property", "test_assurance_review_properties.py", ("test_epistemic_shortcuts_require_framework_evidence",)),
            ("property", "test_law_runtime_properties.py", ("test_unavailable_blocked_or_unstarted_is_never_pass", "test_completion_is_truthful_and_definition_bound")),
        ),
        invariants=("AEGIS-I008", "AEGIS-I009", "AEGIS-I018"),
        boundary="epistemic state transitions and lifecycle-true law outcome records",
    ),
    _group(
        (
            "test_review_receipt_binds_to_current_diff",
            "test_review_receipt_binds_to_current_requirements",
            "test_review_receipt_binds_to_current_evidence",
            "test_rubber_stamp_review_is_rejected",
            "test_reviewer_must_record_falsification_attempts",
            "test_critical_security_change_requires_specialist_review",
        ),
        targets=(
            ("property", "test_assurance_review_properties.py", ("test_review_receipt_is_stale_after_any_bound_surface_changes", "test_rubber_stamp_and_self_review_are_rejected")),
            ("property", "test_state_assurance_integration_properties.py", ("test_green_falsification_and_review_are_enforced_as_current_state",)),
            ("unit", "test_state_machine.py", ("test_final_audit_requires_resolved_gates_and_critical_risks",)),
        ),
        invariants=("AEGIS-I004", "AEGIS-I007", "AEGIS-I014"),
        boundary="independent review receipt schema, current bindings, and specialist role enforcement",
    ),
    _group(
        (
            "test_write_outside_compiled_scope_is_rejected",
            "test_unexpected_semantic_expansion_requires_replan",
            "test_unexpected_dependency_requires_replan",
            "test_unexpected_lockfile_change_requires_replan",
            "test_user_owned_dirty_file_is_preserved",
        ),
        targets=(("property", "test_scope_controls_properties.py", ("test_user_dirty_nested_and_reference_boundaries_override_allow", "test_change_budget_is_fail_closed_at_every_dimension", "test_semantic_expansion_requires_budget_and_adr", "test_dependency_delta_is_explicit_and_classified")),),
        invariants=("AEGIS-I010", "AEGIS-I015", "AEGIS-I016"),
        boundary="compiled write scope, change/semantic budgets, ADRs, dependency and lockfile deltas",
    ),
    _group(
        (
            "test_reimplementation_requires_reference_contract",
            "test_reimplementation_requires_differential_oracle",
            "test_reference_mutation_blocks_finalization",
            "test_intentional_compatibility_divergence_requires_explicit_decision",
        ),
        targets=(
            ("property", "test_policy_compiler_properties.py", ("test_reimplementation_requires_complete_read_only_oracle_contract",)),
            ("property", "test_scope_controls_properties.py", ("test_user_dirty_nested_and_reference_boundaries_override_allow", "test_semantic_expansion_requires_budget_and_adr")),
        ),
        invariants=("AEGIS-I006", "AEGIS-I010", "AEGIS-I017"),
        boundary="read-only reference contract, differential oracle gate, divergence ADR, and reference scope",
    ),
    _group(
        (
            "test_generated_instruction_like_content_has_no_governance_authority",
            "test_external_reference_instructions_have_no_governance_authority",
        ),
        targets=(("property", "test_policy_compiler_properties.py", ("test_untrusted_instruction_like_content_never_acquires_authority",)),),
        invariants=("AEGIS-I001", "AEGIS-I009"),
        boundary="instruction provenance classifier",
    ),
    _group(
        (
            "test_project_policy_cannot_disable_tdd",
            "test_project_policy_cannot_disable_test_first_development",
            "test_project_policy_cannot_allow_implementation_before_test_contract",
            "test_project_policy_cannot_disable_red_requirement_when_compiler_requires_red",
            "test_project_policy_cannot_disable_characterization_before_refactor",
            "test_project_policy_cannot_disable_property_testing_when_required",
        ),
        targets=(("property", "test_policy_compiler_properties.py", ("test_project_policy_weakening_is_rejected_with_invariant", "test_compiler_assigns_conservative_tdd_mode_and_hard_gates")),),
        invariants=("AEGIS-I019", "AEGIS-I020", "AEGIS-I021"),
        boundary="constitutional project-policy rejection and conservative TDD compilation",
    ),
    _group(
        (
            "test_mutating_behavior_task_cannot_enter_implement_before_test_design",
            "test_mutating_behavior_task_cannot_enter_implement_before_baseline_execution",
            "test_red_required_task_cannot_enter_implement_without_red_evidence",
            "test_refactor_cannot_enter_refactor_without_characterization",
            "test_test_only_definition_created_after_production_mutation_cannot_retroactively_satisfy_tdd",
        ),
        targets=(("property", "test_tdd_lifecycle_properties.py", ("test_plan_cannot_enter_implement_before_test_design_and_baseline", "test_every_tdd_mode_requires_its_exact_baseline_outcome", "test_fake_or_irrelevant_red_cannot_authorize_implementation")),),
        invariants=("AEGIS-I019", "AEGIS-I020", "AEGIS-I022"),
        boundary="lifecycle transition predicate for implementation authority",
    ),
    _group(
        (
            "test_red_evidence_binds_to_preimplementation_baseline",
            "test_red_evidence_binds_to_test_contract_digest",
            "test_red_evidence_binds_to_oracle_digest",
            "test_fake_red_from_broken_harness_is_rejected",
            "test_fake_red_from_intentionally_broken_baseline_is_rejected",
            "test_irrelevant_failure_cannot_satisfy_red_gate",
        ),
        targets=(
            ("property", "test_assurance_review_properties.py", ("test_baseline_mode_requires_exact_observation", "test_fake_red_is_rejected")),
            ("property", "test_tdd_lifecycle_properties.py", ("test_fake_or_irrelevant_red_cannot_authorize_implementation",)),
        ),
        invariants=("AEGIS-I004", "AEGIS-I018", "AEGIS-I019", "AEGIS-I020"),
        boundary="pre-implementation baseline receipt and implementation authority",
    ),
    _group(
        (
            "test_test_contract_is_frozen_between_red_and_green",
            "test_oracle_is_frozen_between_red_and_green",
            "test_gate_expectation_is_frozen_between_red_and_green",
            "test_test_contract_mutation_invalidates_tdd_cycle",
            "test_oracle_mutation_invalidates_tdd_cycle",
        ),
        targets=(
            ("property", "test_assurance_review_properties.py", ("test_cycle_operations_are_append_only_and_do_not_rewrite_input", "test_green_binds_same_contract_and_current_epoch")),
            ("property", "test_tdd_lifecycle_properties.py", ("test_frozen_contract_or_baseline_mutation_revokes_implementation_authority",)),
            ("property", "test_state_assurance_integration_properties.py", ("test_recorded_cycle_identity_cannot_be_replaced_after_baseline",)),
        ),
        invariants=("AEGIS-I002", "AEGIS-I004", "AEGIS-I006", "AEGIS-I020"),
        boundary="append-only TDD cycle identity and frozen RED/GREEN contracts",
    ),
    _group(
        (
            "test_green_must_use_same_test_contract_as_red",
            "test_green_must_use_same_oracle_contract_as_red",
            "test_green_must_bind_to_current_implementation_epoch",
            "test_green_from_stale_implementation_cannot_finalize",
        ),
        targets=(
            ("property", "test_assurance_review_properties.py", ("test_green_binds_same_contract_and_current_epoch",)),
            ("property", "test_state_assurance_integration_properties.py", ("test_green_falsification_and_review_are_enforced_as_current_state",)),
            ("unit", "test_state_machine.py", ("test_finalize_rejects_evidence_from_prior_epoch_even_if_state_is_tampered",)),
        ),
        invariants=("AEGIS-I004", "AEGIS-I020"),
        boundary="GREEN receipt and finalization transition",
    ),
    _group(
        (
            "test_refactor_requires_green_characterization_baseline",
            "test_refactor_must_preserve_frozen_characterization_contract",
            "test_behavior_changing_refactor_requires_red_for_changed_behavior",
        ),
        targets=(
            ("property", "test_policy_compiler_properties.py", ("test_compiler_assigns_conservative_tdd_mode_and_hard_gates",)),
            ("property", "test_tdd_lifecycle_properties.py", ("test_every_tdd_mode_requires_its_exact_baseline_outcome", "test_frozen_contract_or_baseline_mutation_revokes_implementation_authority")),
        ),
        invariants=("AEGIS-I017", "AEGIS-I019", "AEGIS-I020"),
        boundary="refactor classification, characterization baseline, and frozen equivalence contract",
    ),
    _group(
        (
            "test_new_review_finding_requires_regression_first",
            "test_new_hypothesis_counterexample_requires_regression_cycle_before_fix",
            "test_remediation_cannot_modify_production_before_new_red_when_red_is_required",
        ),
        targets=(("property", "test_assurance_review_properties.py", ("test_remediation_cycle_cannot_authorize_fix_before_regression_red",)),),
        invariants=("AEGIS-I014", "AEGIS-I019", "AEGIS-I022"),
        boundary="remediation TDD-cycle authority",
    ),
    _group(
        (
            "test_hypothesis_state_machine_subsumes_declared_transition_edge_cases",
            "test_hypothesis_failure_counterexample_is_recorded_in_evidence",
            "test_property_test_must_exercise_production_path_when_claiming_production_behavior",
        ),
        targets=(
            ("property", "test_tdd_lifecycle_properties.py", ("implementation_authority_matches_independent_model",)),
            ("property", "test_assurance_review_properties.py", ("authority_is_exactly_the_frozen_conjunction",)),
            ("unit", "test_laws.py", ("test_json_law_failure_records_exact_counterexample",)),
            ("property", "test_law_runner_source_layout_properties.py", ("test_portable_agents_paths_resolve_to_source_authority",)),
        ),
        invariants=("AEGIS-I005", "AEGIS-I014", "AEGIS-I021"),
        boundary="Hypothesis state-machine models, minimized counterexample record, and production-path law execution",
    ),
    _group(
        (
            "test_implementation_write_scope_is_locked_before_red",
            "test_test_design_phase_cannot_modify_corresponding_production_scope",
            "test_implementation_write_authority_appears_only_after_tdd_gate",
        ),
        targets=(("property", "test_scope_controls_properties.py", ("test_test_design_and_implementation_have_distinct_authority", "test_implementation_authority_requires_legitimate_baseline_and_current_governance")),),
        invariants=("AEGIS-I001", "AEGIS-I010", "AEGIS-I019"),
        boundary="phase-separated compiled write authorization",
    ),
    _group(
        ("test_finalization_requires_complete_tdd_provenance_for_every_behavioral_production_change",),
        targets=(
            ("property", "test_final_audit_properties.py", ("test_complete_exact_contract_produces_sealed_pass_receipt", "test_missing_unproved_manual_or_stale_observation_fails_closed")),
            ("property", "test_state_assurance_integration_properties.py", ("test_green_falsification_and_review_are_enforced_as_current_state",)),
            ("unit", "test_state_machine.py", ("test_finalized_load_fails_closed_after_workspace_mutation",)),
        ),
        invariants=("AEGIS-I004", "AEGIS-I007", "AEGIS-I014", "AEGIS-I019", "AEGIS-I020", "AEGIS-I022"),
        boundary="current TDD cycle, falsification, review, verification, final audit, and finalized workspace",
    ),
)


REQUIRED_CONSTITUTIONAL_LAW_NAMES = tuple(
    name for group in GROUPS for name in group["names"]
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _test_roots(root: Path) -> dict[str, Path]:
    source = root / "infra"
    deployed = root / ".agents" / "infra"
    infra = source if (source / "property_tests").is_dir() else deployed
    if not (infra / "property_tests").is_dir() or not (infra / "tests").is_dir():
        raise RuntimeError("constitutional catalog cannot locate executable test roots")
    return {"property": infra / "property_tests", "unit": infra / "tests"}


def _definitions(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node.lineno
    return found


def _runtime_observation_ids(path: Path) -> dict[str, str]:
    """Map state-machine rule/invariant symbols to their generated unittest case."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    runtime: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {
            base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
            for base in node.bases
        }
        if "RuleBasedStateMachine" not in bases:
            continue
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                runtime[child.name] = "runTest"
    return runtime


def build_constitutional_catalog(root: Path) -> dict:
    project = Path(root).resolve(strict=True)
    roots = _test_roots(project)
    if len(REQUIRED_CONSTITUTIONAL_LAW_NAMES) != 105 or len(set(REQUIRED_CONSTITUTIONAL_LAW_NAMES)) != 105:
        raise RuntimeError("constitutional law inventory must be an exact 105-name bijection")
    laws: list[dict] = []
    for group in GROUPS:
        property_tests: list[dict] = []
        observations: list[str] = []
        for category, filename, method_names in group["targets"]:
            path = roots[category] / filename
            if not path.is_file():
                raise RuntimeError(f"constitutional oracle module is missing: {path}")
            definitions = _definitions(path)
            runtime_ids = _runtime_observation_ids(path)
            missing = sorted(set(method_names) - set(definitions))
            if missing:
                raise RuntimeError(f"constitutional oracle definitions missing from {filename}: {missing}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            relative = path.relative_to(project).as_posix()
            for method in method_names:
                runtime_id = runtime_ids.get(method, method)
                property_tests.append(
                    {
                        "category": category,
                        "id": runtime_id,
                        "source_symbol": method,
                        "file": relative,
                        "line": definitions[method],
                        "definition_sha256": digest,
                    }
                )
                observations.append(
                    f"source-assurance::{category}-test-{path.stem}-{runtime_id}"
                )
        observations = list(dict.fromkeys(observations))
        for name in group["names"]:
            entry = {
                "id": name,
                "invariants": list(group["invariants"]),
                "observation_boundary": group["boundary"],
                "falsifier": (
                    name.removeprefix("test_").replace("_", " ")
                    + " is absent, bypassable, stale, or accepted without the mapped production-path property"
                ),
                "severity": "HARD",
                "oracle": [f"{item['file']}::{item['id']}" for item in property_tests],
                "tdd_applicability": True,
                "tdd_mode": group["tdd_mode"],
                "red_required": group["tdd_mode"] == "RED_REQUIRED",
                "property_tests": property_tests,
                "required_observations": observations,
                "subsumption": "each mapped definition exists and its isolated module must execute at least one successful oracle with no failure or error",
                "status": "PENDING_EXECUTION",
            }
            entry["definition_sha256"] = hashlib.sha256(_canonical(entry)).hexdigest()
            laws.append(entry)
    if [item["id"] for item in laws] != list(REQUIRED_CONSTITUTIONAL_LAW_NAMES):
        raise RuntimeError("constitutional law catalog order or identity drifted")
    body = {"schema": 1, "suite": "aegis-constitutional-laws", "law_count": len(laws), "laws": laws}
    body["digest"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def evaluate_constitutional_catalog(
    catalog: dict,
    observations: dict[str, tuple[bool, str]],
) -> dict:
    if (
        not isinstance(catalog, dict)
        or catalog.get("schema") != 1
        or catalog.get("law_count") != 105
        or not isinstance(catalog.get("laws"), list)
        or len(catalog["laws"]) != 105
    ):
        raise RuntimeError("constitutional execution requires the exact 105-law catalog")
    if not isinstance(observations, dict):
        raise RuntimeError("constitutional observations must be a binding map")
    records: list[dict] = []
    for definition in catalog["laws"]:
        selected: list[dict] = []
        missing: list[str] = []
        for binding in definition["required_observations"]:
            observed = observations.get(binding)
            if (
                not isinstance(observed, tuple)
                or len(observed) != 2
                or not isinstance(observed[0], bool)
                or not isinstance(observed[1], str)
            ):
                missing.append(binding)
                continue
            passed, detail = observed
            outcome = "PASS" if passed else "FAIL"
            capability_status = "AVAILABLE"
            justification = None
            if not passed:
                try:
                    exact = json.loads(detail)
                except (TypeError, json.JSONDecodeError):
                    exact = None
                if (
                    isinstance(exact, dict)
                    and exact.get("outcome") == "SKIP"
                    and isinstance(exact.get("capability_status"), str)
                    and exact["capability_status"] != "AVAILABLE"
                ):
                    outcome = "UNAVAILABLE"
                    capability_status = exact["capability_status"]
                    justification = str(exact.get("detail") or detail)
            selected.append(
                {
                    "binding": binding,
                    "passed": passed,
                    "outcome": outcome,
                    "capability_status": capability_status,
                    "detail": detail,
                    "justification": justification,
                }
            )
        failures = [item for item in selected if item["outcome"] == "FAIL"]
        limitations = [item for item in selected if item["outcome"] == "UNAVAILABLE"]
        if missing or not selected:
            outcome = "UNTESTED"
            capability_status = "UNOBSERVED"
        elif failures:
            outcome = "FAIL"
            capability_status = "AVAILABLE"
        elif limitations:
            outcome = "UNAVAILABLE"
            capability_status = "PARTIALLY_UNAVAILABLE"
        else:
            outcome = "PASS"
            capability_status = "AVAILABLE"
        record = {
            **definition,
            "started": True,
            "completed": True,
            "outcome": outcome,
            "capability_status": capability_status,
            "oracle_count": len(selected),
            "execution_detail": (
                f"{len(selected)} required isolated module observation(s) passed"
                if outcome == "PASS"
                else "missing=" + repr(missing)
                + "; failed=" + repr([item["binding"] for item in failures])
                + "; unavailable=" + repr([item["binding"] for item in limitations])
            ),
            "executed_observations": selected,
            "justification": (
                "; ".join(item["justification"] for item in limitations if item["justification"])
                if limitations
                else "required observation was not supplied"
                if missing or not selected
                else None
            ),
        }
        record["evidence_sha256"] = hashlib.sha256(
            _canonical(
                {
                    "id": record["id"],
                    "definition_sha256": record["definition_sha256"],
                    "outcome": record["outcome"],
                    "observations": selected,
                    "missing": missing,
                }
            )
        ).hexdigest()
        records.append(record)
    counts: dict[str, int] = {}
    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
    report = {
        "schema": 1,
        "suite": "aegis-constitutional-laws",
        "catalog_digest": catalog["digest"],
        "law_count": len(records),
        "started": len(records),
        "completed": len(records),
        "counts": dict(sorted(counts.items())),
        "outcome": (
            "FAIL"
            if counts.get("FAIL") or counts.get("UNTESTED")
            else "PASS_WITH_EXPLICIT_CAPABILITY_LIMITATIONS"
            if counts.get("UNAVAILABLE")
            else "PASS"
            if counts == {"PASS": 105}
            else "FAIL"
        ),
        "laws": records,
    }
    report["digest"] = hashlib.sha256(_canonical(report)).hexdigest()
    return report


def constitutional_report_is_acceptable(report: dict) -> bool:
    """Accept only an internally consistent pass or explicit capability limit."""

    if (
        not isinstance(report, dict)
        or report.get("schema") != 1
        or report.get("law_count") != 105
        or report.get("started") != 105
        or report.get("completed") != 105
        or not isinstance(report.get("laws"), list)
        or len(report["laws"]) != 105
        or not isinstance(report.get("counts"), dict)
    ):
        return False
    calculated: dict[str, int] = {}
    for law in report["laws"]:
        if not isinstance(law, dict) or law.get("started") is not True or law.get("completed") is not True:
            return False
        outcome = law.get("outcome")
        if outcome not in {"PASS", "UNAVAILABLE"}:
            return False
        calculated[outcome] = calculated.get(outcome, 0) + 1
        if outcome == "UNAVAILABLE" and not law.get("justification"):
            return False
    expected = (
        "PASS_WITH_EXPLICIT_CAPABILITY_LIMITATIONS"
        if calculated.get("UNAVAILABLE")
        else "PASS"
    )
    return report["counts"] == dict(sorted(calculated.items())) and report.get("outcome") == expected


def render_constitutional_markdown(catalog: dict) -> str:
    catalog_digest = catalog.get("catalog_digest", catalog.get("digest"))
    lines = [
        "# Aegis Constitutional Law Traceability",
        "",
        f"Catalog digest: `{catalog_digest}`",
        "",
        f"Exact law count: **{catalog['law_count']}**",
        "",
        "| Law ID | Invariants | TDD mode | Status | Property subsumption |",
        "|---|---|---|---|---|",
    ]
    for item in catalog["laws"]:
        oracles = "<br>".join(f"`{value}`" for value in item["oracle"])
        lines.append(
            f"| `{item['id']}` | {', '.join(item['invariants'])} | {item['tdd_mode']} | {item.get('outcome', item['status'])} | {oracles} |"
        )
    return "\n".join(lines) + "\n"
