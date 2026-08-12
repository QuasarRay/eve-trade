#!/usr/bin/env python3
"""Generate classification and deterministic emulation scenario artifacts.

This is a build-time generator.  It never generates executable pytest test
identities.  Future emulation-verification names are Markdown specifications
only, and business test functions remain explicit checked-in Python source.
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from catalog import (
    PROPERTY_ROOT,
    SAFETY_CRITERIA_PATH,
    SCENARIO_ROOT,
    TEST_RULES_PATH,
    UNIVERSAL_CRITERIA,
    category_criteria,
    input_digests,
    load_catalog,
)
from scenario_definitions import (
    ANYSYSTEM_REVISION,
    IMPLEMENTED_PENDING,
    PLANNED,
    SCENARIOS,
    ScenarioDefinition,
    matching_scenarios,
)


CLASSIFICATION_PATH = PROPERTY_ROOT / "classification.json"
PREREQUISITE_INVENTORY_PATH = SCENARIO_ROOT / "prerequisite-inventory.json"
REGISTRY_PATH = SCENARIO_ROOT / "scenario-registry.json"
TIME_DECISION_PATH = SCENARIO_ROOT / "DETERMINISTIC_TIME_DECISION.md"
STATE_PATH = SCENARIO_ROOT / "implementation-state.json"
VARIANT_SENTINEL = "${ANYSYSTEM_SELECTED_VARIANT}"

# These are the real Kubernetes targets named by each scenario contract.  The
# invocation may vary supported fault intensity parameters, but it may not
# redirect a canonical scenario to a different workload.
LITMUS_TARGETS: dict[str, dict[str, str] | str] = {
    "gateway_crash_at_handoff_boundary": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "market_crash_at_persistence_publish_boundary": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "worker_crash_at_processing_boundary": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "settlement_crash_at_transaction_boundary": {"kind": "deployment", "selector": "app.kubernetes.io/name=trade-settlement"},
    "outbox_dispatcher_crash_at_publish_boundary": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "gateway_pod_delete_during_udp_workload": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "worker_pod_delete_during_message_processing": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "settlement_pod_delete_during_database_transaction": {"kind": "deployment", "selector": "app.kubernetes.io/name=trade-settlement"},
    "gateway_single_node_drain": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "worker_single_node_drain": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "market_to_pubsub_directional_partition": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "worker_to_settlement_directional_partition": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "settlement_to_postgres_unknown_commit_partition": {"kind": "deployment", "selector": "app.kubernetes.io/name=trade-settlement"},
    "asymmetric_response_path_partition": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "quilkin_gateway_packet_duplication": {"kind": "deployment", "selector": "app.kubernetes.io/name=quilkin"},
    "mixed_workload_network_delay": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "targeted_network_loss": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "settlement_dns_resolution_failure": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "gateway_cpu_pressure": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "gateway_memory_pressure": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "worker_memory_pressure": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "postgres_disk_full_during_commit": {"kind": "deployment", "selector": "app.kubernetes.io/name=postgres"},
    "application_logging_disk_full": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "postgres_restart_during_settlement": {"kind": "deployment", "selector": "app.kubernetes.io/name=postgres"},
    "nsq_restart_during_delivery": {"kind": "statefulset", "selector": "app.kubernetes.io/name=nsqd"},
    "random_service_kill_mixed_workload": VARIANT_SENTINEL,
    "random_crash_restart_sequence": VARIANT_SENTINEL,
    "database_connection_failure_window": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
    "grpc_connection_failure_window": {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
}

LITMUS_FIXED_ENVIRONMENT: dict[str, dict[str, str]] = {
    "gateway_crash_at_handoff_boundary": {"TARGET_CONTAINER": "encore-backend"},
    "market_crash_at_persistence_publish_boundary": {"TARGET_CONTAINER": "encore-backend"},
    "worker_crash_at_processing_boundary": {"TARGET_CONTAINER": "encore-backend"},
    "settlement_crash_at_transaction_boundary": {"TARGET_CONTAINER": "trade-settlement"},
    "outbox_dispatcher_crash_at_publish_boundary": {"TARGET_CONTAINER": "encore-backend"},
    "market_to_pubsub_directional_partition": {
        "NAMESPACE_SELECTOR": "kubernetes.io/metadata.name=${RUN_NAMESPACE}",
        "POD_SELECTOR": "app.kubernetes.io/name=nsqd",
        "POLICY_TYPES": "egress",
    },
    "worker_to_settlement_directional_partition": {
        "NAMESPACE_SELECTOR": "kubernetes.io/metadata.name=${RUN_NAMESPACE}",
        "POD_SELECTOR": "app.kubernetes.io/name=trade-settlement",
        "POLICY_TYPES": "egress",
    },
    "settlement_to_postgres_unknown_commit_partition": {
        "NAMESPACE_SELECTOR": "kubernetes.io/metadata.name=${RUN_NAMESPACE}",
        "POD_SELECTOR": "app.kubernetes.io/name=postgres",
        "POLICY_TYPES": "egress",
    },
    "asymmetric_response_path_partition": {
        "NAMESPACE_SELECTOR": "kubernetes.io/metadata.name=${RUN_NAMESPACE}",
        "POD_SELECTOR": "app.kubernetes.io/name=nsqd",
        "POLICY_TYPES": "egress",
    },
    "settlement_dns_resolution_failure": {
        "TARGET_HOSTNAMES": "[\"trade-settlement.${RUN_NAMESPACE}.svc.cluster.local\"]",
    },
    "database_connection_failure_window": {
        "NAMESPACE_SELECTOR": "kubernetes.io/metadata.name=${RUN_NAMESPACE}",
        "POD_SELECTOR": "app.kubernetes.io/name=postgres",
        "POLICY_TYPES": "egress",
    },
    "grpc_connection_failure_window": {
        "NAMESPACE_SELECTOR": "kubernetes.io/metadata.name=${RUN_NAMESPACE}",
        "POD_SELECTOR": "app.kubernetes.io/name=trade-settlement",
        "POLICY_TYPES": "egress",
    },
}


def explicit_business_test_sources() -> dict[str, str]:
    """Return checked-in pytest identities; no executable source is generated."""
    result: dict[str, str] = {}
    for path in sorted((PROPERTY_ROOT / "e2e").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            if node.name in result:
                raise ValueError(f"duplicate explicit business test function: {node.name}")
            result[node.name] = path.relative_to(PROPERTY_ROOT).as_posix()
    return result


def reusable_oracle_sources() -> dict[str, str]:
    result: dict[str, str] = {}
    root = PROPERTY_ROOT / "reusable-oracles"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("assert_"):
                continue
            if node.name in result:
                raise ValueError(f"duplicate reusable oracle function: {node.name}")
            result[node.name] = path.relative_to(PROPERTY_ROOT).as_posix()
    return result

# These names contain more than one independently falsifiable obligation.  The
# original remains an exact catalog identity and becomes a non-executable parent
# contract; children are explicit metadata until implemented in checked-in source.
COMPOSITE_CHILDREN: dict[str, tuple[str, ...]] = {
    "test_gateway_under_memory_pressure_rejects_excess_work_without_process_crash_or_false_success": (
        "test_gateway_under_memory_pressure_rejects_excess_work_without_false_success",
        "test_gateway_under_memory_pressure_does_not_crash_process_while_rejecting_excess_work",
    ),
    "test_authenticated_udp_accept_burst_meets_slo_and_preserves_state": (
        "test_authenticated_udp_accept_burst_meets_slo",
        "test_authenticated_udp_accept_burst_preserves_state",
    ),
    "test_authenticated_udp_cancel_burst_meets_slo_and_preserves_state": (
        "test_authenticated_udp_cancel_burst_meets_slo",
        "test_authenticated_udp_cancel_burst_preserves_state",
    ),
    "test_authenticated_udp_issue_burst_meets_slo_and_preserves_state": (
        "test_authenticated_udp_issue_burst_meets_slo",
        "test_authenticated_udp_issue_burst_preserves_state",
    ),
    "test_mixed_issue_accept_cancel_burst_meets_slo_and_preserves_state": (
        "test_mixed_issue_accept_cancel_burst_meets_slo",
        "test_mixed_issue_accept_cancel_burst_preserves_state",
    ),
    "test_authenticated_udp_edge_rejects_unknown_wrong_key_and_cross_principal_actions": (
        "test_authenticated_udp_edge_rejects_unknown_key_id",
        "test_authenticated_udp_edge_rejects_hmac_signed_with_wrong_key",
        "test_authenticated_udp_edge_rejects_cross_principal_action",
    ),
    "test_chaos_experiment_preserves_item_and_isk_conservation_after_recovery": (
        "test_chaos_experiment_preserves_item_conservation_after_recovery",
        "test_chaos_experiment_preserves_isk_conservation_after_recovery",
    ),
    "test_issue_then_full_accept_preserves_global_items_and_isk": (
        "test_issue_then_full_accept_preserves_global_items",
        "test_issue_then_full_accept_preserves_global_isk",
    ),
    "test_multiple_partial_accepts_and_cancel_race_conserves_items_and_isk": (
        "test_multiple_partial_accepts_and_cancel_race_conserves_items",
        "test_multiple_partial_accepts_and_cancel_race_conserves_isk",
    ),
    "test_partial_accept_and_cancel_race_conserves_items_and_isk": (
        "test_partial_accept_and_cancel_race_conserves_items",
        "test_partial_accept_and_cancel_race_conserves_isk",
    ),
    "test_concurrent_full_accept_and_cancel_have_exactly_one_winner_and_conserve_state": (
        "test_concurrent_full_accept_and_cancel_have_exactly_one_terminal_winner",
        "test_concurrent_full_accept_and_cancel_conserve_items",
        "test_concurrent_full_accept_and_cancel_conserve_isk",
    ),
    "test_runtime_database_role_has_exact_required_privileges_and_no_administration_rights": (
        "test_runtime_database_role_has_every_declared_required_privilege",
        "test_runtime_database_role_has_no_database_administration_right",
    ),
}

PROVENANCE_CLASSES = (
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
)

# Concrete use of the catalog-quality oracle demonstrated that these outcomes
# do not uniquely determine pass/fail.  Under first-false-wins, U04 is reached
# before any leaf criterion and the contract must remain unresolved.
UNSAFE_FIRST_FALSE: dict[str, tuple[str, str]] = {
    name: (
        "U04_AMBIGUOUS_REQUIRED_RELATION",
        "TEST_NAME_IDENTIFIES_THE_REQUIRED_OUTCOME_OR_PASS_FAIL_RELATION_UNAMBIGUOUSLY",
    )
    for name in (
        "test_udp_pool_handles_delayed_duplicate_after_session_reuse",
        "test_udp_pool_handles_more_concurrent_callers_than_capacity",
        "test_udp_pool_handles_stale_datagram_after_session_reuse",
        "test_udp_pool_close_during_receive_is_safe",
        "test_udp_pool_reset_during_checkout_is_safe",
        "test_accept_trade_returns_clear_error_for_cancelled_trade",
        "test_accept_trade_returns_clear_error_for_completed_trade",
        "test_accept_trade_returns_clear_error_for_insufficient_wallet_balance",
        "test_accept_trade_returns_clear_error_for_unavailable_trade_quantity",
        "test_cancel_trade_returns_clear_error_for_non_seller_caller",
        "test_create_trade_offer_returns_clear_error_for_insufficient_item_quantity",
        "test_create_trade_offer_returns_clear_error_for_invalid_item_stack_owner",
    )
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write_or_check(path: Path, content: str, check: bool, stale: list[str]) -> None:
    if check:
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            stale.append(path.relative_to(PROPERTY_ROOT).as_posix())
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _safety_trace(
    leaf_code: str,
    first_false: tuple[str, str] | None = None,
) -> list[dict[str, object]]:
    trace: list[dict[str, object]] = []
    for code, text in UNIVERSAL_CRITERIA:
        result = first_false is None or code != first_false[0]
        trace.append({"reason_code": code, "criterion_text": text, "result": result})
        if not result:
            return trace
    trace.extend(
        {"reason_code": code, "criterion_text": text, "result": True}
        for code, text in category_criteria()[leaf_code]
    )
    return trace


def _derived_child_records(
    parent: dict[str, Any],
    digests: dict[str, object],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name in COMPOSITE_CHILDREN[parent["name"]]:
        matches = matching_scenarios(name)
        scenario = matches[0] if matches else None
        records.append(
            {
                "name": name,
                "derived_from": parent["name"],
                "leaf_category": parent["leaf_category"],
                "leaf_code": parent["leaf_code"],
                "oracle_inference": "SAFE",
                "primary_false_reason": None,
                "primary_false_criterion_text": None,
                "representation": "ATOMIC",
                "context": "CONTEXT_SPECIFIC" if scenario else "CONTEXT_INDEPENDENT",
                "execution_mode": "REAL_DEPLOYMENT_EMULATION" if scenario else "STATIC_OR_DIRECT",
                "emulation_scenario_id": scenario.scenario_id if scenario else None,
                "emulation_status": scenario.status if scenario else "NOT_APPLICABLE",
                "oracle_status": "NOT_IMPLEMENTED",
                "test_status": "NOT_IMPLEMENTED",
                "implementation_path": None,
                "safety_evaluation": _safety_trace(parent["leaf_code"]),
                "test_rules_digest": digests["test_rules_sha256"],
                "safety_criteria_digest": digests["safety_criteria_sha256"],
                "source_catalog_digest": digests["source_catalog_sha256"],
                "notes": [
                    "Derived without adding semantics from a SAFE composite parent; safety evaluation restarted at U01."
                ],
            }
        )
    return records


def build_classification() -> dict[str, Any]:
    digests = input_digests()
    implemented_sources = explicit_business_test_sources()
    records: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    overlap_diagnostics: list[dict[str, object]] = []
    for entry in load_catalog():
        first_false = UNSAFE_FIRST_FALSE.get(entry.name)
        is_safe = first_false is None
        is_composite = is_safe and entry.name in COMPOSITE_CHILDREN
        matches = matching_scenarios(entry.name) if is_safe and not is_composite else []
        scenario = matches[0] if matches else None
        if len(matches) > 1:
            overlap_diagnostics.append(
                {
                    "name": entry.name,
                    "selected_by_precedence": matches[0].scenario_id,
                    "also_matched": [match.scenario_id for match in matches[1:]],
                }
            )
        implementation_path = implemented_sources.get(entry.name)
        record = {
            "name": entry.name,
            "leaf_category": entry.leaf_category,
            "leaf_category_title": entry.leaf_title,
            "leaf_code": entry.leaf_code,
            "source_path": entry.source_path,
            "oracle_inference": "SAFE" if is_safe else "UNSAFE",
            "primary_false_reason": first_false[0] if first_false else None,
            "primary_false_criterion_text": first_false[1] if first_false else None,
            "representation": "COMPOSITE" if is_composite else "ATOMIC" if is_safe else "UNRESOLVED",
            "context": (
                "NOT_APPLICABLE"
                if is_composite or not is_safe
                else "CONTEXT_SPECIFIC"
                if scenario
                else "CONTEXT_INDEPENDENT"
            ),
            "execution_mode": (
                "UNRESOLVED"
                if is_composite or not is_safe
                else "REAL_DEPLOYMENT_EMULATION"
                if scenario
                else "STATIC_OR_DIRECT"
            ),
            "emulation_scenario_id": scenario.scenario_id if scenario else None,
            "emulation_status": scenario.status if scenario else "NOT_APPLICABLE",
            "oracle_status": (
                "UNRESOLVED_FIRST_FALSE"
                if not is_safe
                else "DECOMPOSITION_REQUIRED"
                if is_composite
                else "IMPLEMENTED_REUSABLE"
                if implementation_path
                else "NOT_IMPLEMENTED"
            ),
            "test_status": (
                "UNRESOLVED_NO_EXECUTABLE_TEST"
                if not is_safe
                else "NON_EXECUTABLE_COMPOSITE_PARENT"
                if is_composite
                else "IMPLEMENTED_BLOCKED_REAL_DEPLOYMENT"
                if implementation_path
                else "NOT_IMPLEMENTED"
            ),
            "implementation_path": implementation_path,
            "safety_evaluation": _safety_trace(entry.leaf_code, first_false),
            "decomposed_children": list(COMPOSITE_CHILDREN.get(entry.name, ())),
            "test_rules_digest": digests["test_rules_sha256"],
            "safety_criteria_digest": digests["safety_criteria_sha256"],
            "source_catalog_digest": digests["source_catalog_sha256"],
            "notes": (
                [
                    "SAFE name contains independently falsifiable obligations and is retained as a non-executable parent contract."
                ]
                if is_composite
                else [
                    "First-false evaluation stopped at U04; no oracle, execution mode, or scenario was inferred."
                ]
                if not is_safe
                else [
                    "All U01-U09 and assigned leaf criteria evaluated true in first-false order."
                ]
            ),
        }
        records.append(record)
        if is_composite:
            derived.extend(_derived_child_records(record, digests))

    classified_names = {record["name"] for record in [*records, *derived]}
    unknown_implementations = sorted(set(implemented_sources) - classified_names)
    if unknown_implementations:
        raise ValueError(
            "explicit business test function is not an original or derived catalog identity: "
            + ", ".join(unknown_implementations)
        )

    representation_counts = Counter(record["representation"] for record in records)
    context_counts = Counter(record["context"] for record in records)
    execution_counts = Counter(record["execution_mode"] for record in records)
    safety_counts = Counter(record["oracle_inference"] for record in records)
    return {
        "schema_version": "eve-trade.business-classification/v1",
        "authoritative_inputs": digests,
        "evaluation_semantics": {
            "universal_order": [code for code, _ in UNIVERSAL_CRITERIA],
            "first_false_wins": True,
            "unknown_counts_as_false": True,
            "atomicity_evaluated_after_safety": True,
            "scenario_match_precedence": [scenario.scenario_id for scenario in SCENARIOS],
        },
        "counts": {
            "total_original_business_test_names": len(records),
            "safe_names": safety_counts["SAFE"],
            "unsafe_names": safety_counts["UNSAFE"],
            "non_test_contracts": sum(record["representation"] == "NON_TEST" for record in records),
            "safe_atomic_tests": representation_counts["ATOMIC"],
            "safe_composite_parents": representation_counts["COMPOSITE"],
            "context_specific_tests": context_counts["CONTEXT_SPECIFIC"],
            "context_independent_tests": context_counts["CONTEXT_INDEPENDENT"],
            "static_or_direct_tests": execution_counts["STATIC_OR_DIRECT"],
            "real_deployment_emulation_tests": execution_counts["REAL_DEPLOYMENT_EMULATION"],
            "unresolved_execution_mode": execution_counts["UNRESOLVED"],
            "derived_atomic_test_names": len(derived),
        },
        "unsafe_by_first_false_reason_code": dict(
            sorted(
                Counter(
                    record["primary_false_reason"]
                    for record in records
                    if record["oracle_inference"] == "UNSAFE"
                ).items()
            )
        ),
        "scenario_selector_overlap_diagnostics": overlap_diagnostics,
        "records": records,
        "derived_business_tests": derived,
    }


def _action_plan(scenario: ScenarioDefinition) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {
            "action_id": "deploy-real-components",
            "kind": "DAGGER_VERIFY_REAL_DEPLOYMENT",
            "requires_barrier": "deployment-ready",
        },
        {
            "action_id": "initialize-scenario-fixture",
            "kind": "DAGGER_INITIALIZE_FIXTURE",
            "requires_barrier": "initial-state-witnessed",
        },
    ]
    if scenario.uses_litmus:
        activation_action: dict[str, Any] = {
            "action_id": "activate-scenario-chaos",
            "kind": scenario.action_kind,
            "litmus_experiment": scenario.litmus_experiment,
            "requires_barrier": "chaos-control-plane-acknowledged",
        }
        if scenario.scenario_id in {
            "random_crash_restart_sequence",
            "random_service_kill_mixed_workload",
        }:
            # These are real Kubernetes selectors, not modeled nodes.  The
            # controller chooses one deterministically and the runner requires
            # the selected value to be bound into the Litmus request.
            activation_action["seeded_variants"] = [
                {"kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"},
                {"kind": "deployment", "selector": "app.kubernetes.io/name=trade-settlement"},
                {"kind": "deployment", "selector": "app.kubernetes.io/name=postgres"},
                {"kind": "statefulset", "selector": "app.kubernetes.io/name=nsqd"},
            ]
        actions.extend(
            [
                activation_action,
                {
                    "action_id": "witness-scenario-effect",
                    "kind": "WAIT_FOR_INDEPENDENT_EFFECT_WITNESS",
                    "requires_barrier": "independent-effect-witnessed",
                },
            ]
        )
    else:
        actions.append(
            {
                "action_id": "establish-controlled-prerequisite",
                "kind": scenario.action_kind,
                "requires_barrier": "controlled-prerequisite-witnessed",
            }
        )
    actions.extend(
        [
            {
                "action_id": "execute-identified-workload",
                "kind": "DAGGER_EXECUTE_WORKLOAD",
                "requires_barrier": "workload-path-witnessed",
            },
            {
                "action_id": "release-controlled-condition",
                "kind": "DAGGER_RELEASE_SCENARIO_ACTION",
                "requires_barrier": "recovery-witnessed",
            },
            {
                "action_id": "collect-factual-observations",
                "kind": "DAGGER_COLLECT_OBSERVATIONS",
                "requires_barrier": "observations-complete",
            },
            {
                "action_id": "cleanup-scenario-owned-state",
                "kind": "DAGGER_SCOPED_CLEANUP",
                "requires_barrier": "cleanup-complete",
            },
        ]
    )
    primitive_adapters = ["KUBERNETES", "HTTP_JSON", "UDP_JSON", "POSTGRES_SQL", "NSQ_HTTP"]
    for action in actions:
        if action["kind"].startswith("LITMUS_"):
            try:
                target = LITMUS_TARGETS[scenario.scenario_id]
            except KeyError as error:
                raise ValueError(
                    f"{scenario.scenario_id}: Litmus scenario has no exact target policy"
                ) from error
            fixed_environment = LITMUS_FIXED_ENVIRONMENT.get(scenario.scenario_id, {})
            required_fields = [
                "operation",
                "experiment",
                "target",
                "independent_witness",
            ]
            if fixed_environment:
                required_fields.append("environment")
            action["adapter_policy"] = {
                "adapter": "LITMUS",
                "request_allowed_fields": [
                    *required_fields,
                    "acknowledgement_timeout_seconds",
                ],
                "request_required_fields": required_fields,
                "request_fixed_fields": {
                    "operation": "ACTIVATE",
                    "experiment": scenario.litmus_experiment,
                    "target": target,
                },
                "environment_fixed_fields": fixed_environment,
            }
        elif action["action_id"] == "release-controlled-condition" and scenario.uses_litmus:
            action["adapter_policy"] = {
                "adapter": "LITMUS",
                "request_allowed_fields": ["operation", "activation_action_id", "independent_witness"],
                "request_required_fields": ["operation", "activation_action_id", "independent_witness"],
                "request_fixed_fields": {
                    "operation": "RELEASE",
                    "activation_action_id": "activate-scenario-chaos",
                },
            }
        else:
            action["adapter_policy"] = {
                "adapter": "ACTION_SEQUENCE",
                "request_allowed_fields": ["actions"],
                "request_required_fields": ["actions"],
                "request_fixed_fields": {},
                "allowed_nested_adapters": primitive_adapters,
            }
    return actions


def _contract_clauses(scenario: ScenarioDefinition) -> dict[str, str]:
    clauses = {
        "deployment.components": "The real deployed components exactly match the registered scenario component set.",
        "deployment.revisions": "Source, images, manifests, schema, dependencies, Dagger, Litmus, and AnySystem revisions are recorded.",
        "deployment.namespace": "The run uses a unique non-production chaos-safe namespace and exact run identity.",
        "initial_state.fixture": "Every material fixture entity and initial observation is present before controlled actions begin.",
        "workload.identity": "Every workload action carries exact scenario, request, actor, and entity identities.",
        "controller.seed": "The AnySystem seed and canonical scenario parameters determine one replayable logical plan.",
        "controller.transitions": "The controller emits only declared actions in declared logical order.",
        "controller.barrier_gating": "The controller cannot advance until the current action's declared barrier outcome is supplied.",
        "dagger.execution": "Dagger executes the exact controller command and preserves scenario IDs, seed, parameters, and order.",
        "prerequisite.witness": "The structured prerequisite witness is true only after every declared condition and exact entity is observed.",
        "prerequisite.negative_control": "The prerequisite witness remains false when any critical declared component is absent.",
        "workload.phase": "The identified workload begins only in the declared scenario phase and targets the registered deployment.",
        "time.logical_ticks": "Controller logical ticks order phases and never claim to control physical infrastructure time.",
        "time.physical": "Wall-clock durations are recorded as physical observations and do not choose controller transitions.",
        "recovery.condition": scenario.release_barrier + ".",
        "termination.condition": "Completion is emitted only after observations and scoped cleanup barriers succeed.",
        "reset.isolation": "Scenario-owned Kubernetes, broker, database, controller, and evidence state cannot bleed into another run.",
        "observations.completeness": "Every observation required by consuming oracles is present and bound to exact entities.",
        "observations.provenance": "Every exported fact has one allowed provenance class and source reference.",
        "observations.no_business_answers": "Scenario outputs contain facts and never derived expected business answers or pass/fail fields.",
    }
    if scenario.uses_litmus:
        clauses.update(
            {
                "chaos.target": f"Litmus targets exactly {scenario.target} and the affected path is {scenario.affected_path}.",
                "chaos.control_plane": "A scenario/action-bound Litmus control-plane acknowledgement is required but is not sufficient.",
                "chaos.independent_effect": scenario.independent_witness + ".",
                "chaos.activation": scenario.activation_barrier + ".",
                "chaos.release": "The scenario-specific Litmus effect is removed before recovery can be witnessed.",
                "chaos.unaffected_control": "Declared unaffected control resources remain independently observable when the scenario requires them.",
            }
        )
    return clauses


def _witness_adapter(scenario: ScenarioDefinition) -> str:
    experiment = scenario.litmus_experiment or ""
    if experiment in {"pod-delete", "container-kill"}:
        return "KUBERNETES_PROCESS_IDENTITY_TRANSITION"
    if experiment == "node-drain":
        return "KUBERNETES_NODE_AND_POD_RELOCATION"
    if experiment in {
        "pod-network-partition",
        "pod-network-loss",
        "pod-network-latency",
        "pod-network-duplication",
        "pod-dns-error",
    }:
        return "INDEPENDENT_NETWORK_PROBE"
    if experiment in {"pod-cpu-hog", "pod-memory-hog", "disk-fill"}:
        return "INDEPENDENT_RESOURCE_PROBE"
    return "SCENARIO_SPECIFIC_REAL_OBSERVATION"


def _verification_specs(scenario: ScenarioDefinition) -> list[dict[str, Any]]:
    prefix = scenario.scenario_id
    specs: list[tuple[str, str, tuple[str, ...]]] = [
        ("A", f"test_{prefix}_deploys_every_declared_real_component_in_the_registered_namespace", ("deployment.components", "deployment.namespace")),
        ("A", f"test_{prefix}_records_exact_source_image_manifest_schema_dependency_and_tool_revisions", ("deployment.revisions",)),
        ("A", f"test_{prefix}_records_exact_declared_replica_counts_pod_identities_and_deployment_topology", ("deployment.components", "deployment.revisions")),
        ("A", f"test_{prefix}_registered_service_endpoints_resolve_only_to_declared_scenario_pods", ("deployment.components", "deployment.namespace")),
        ("B", f"test_{prefix}_initial_state_witness_identifies_every_declared_fixture_entity_before_controlled_actions", ("initial_state.fixture", "prerequisite.witness")),
        ("B", f"test_{prefix}_prerequisite_witness_remains_false_when_one_required_initial_entity_is_absent", ("prerequisite.negative_control",)),
        ("B", f"test_{prefix}_initial_database_and_broker_observations_identify_the_declared_fixture_state", ("initial_state.fixture", "observations.provenance")),
        ("B", f"test_{prefix}_initial_process_pod_and_controller_phase_identities_are_recorded_before_activation", ("initial_state.fixture", "prerequisite.witness")),
        ("C", f"test_{prefix}_same_seed_parameters_and_barrier_outcomes_replay_identical_controller_transitions", ("controller.seed", "controller.transitions")),
        ("C", f"test_{prefix}_controller_emits_no_action_outside_the_declared_scenario_plan", ("controller.transitions",)),
        ("C", f"test_{prefix}_controller_does_not_advance_before_the_current_barrier_outcome_is_supplied", ("controller.barrier_gating",)),
        ("C", f"test_{prefix}_controller_trace_replay_reconstructs_the_same_logical_tick_sequence", ("controller.seed", "time.logical_ticks")),
        ("D", f"test_{prefix}_dagger_executes_each_controller_action_without_reordering", ("dagger.execution", "controller.transitions")),
        ("D", f"test_{prefix}_dagger_propagates_exact_scenario_revision_seed_parameters_and_action_identity", ("dagger.execution",)),
        ("D", f"test_{prefix}_failed_orchestration_does_not_emit_a_successful_prerequisite_witness", ("dagger.execution", "prerequisite.negative_control")),
        ("D", f"test_{prefix}_cleanup_removes_only_state_owned_by_the_exact_scenario_run", ("reset.isolation",)),
        ("D", f"test_{prefix}_each_declared_action_adapter_reports_a_real_boundary_receipt_bound_to_its_action", ("dagger.execution", "observations.provenance")),
        ("F", f"test_{prefix}_prerequisite_witness_identifies_the_exact_affected_entities_and_controller_phase", ("prerequisite.witness",)),
        ("G", f"test_{prefix}_prerequisite_witness_stays_false_before_the_declared_condition_is_established", ("prerequisite.negative_control",)),
        ("H", f"test_{prefix}_workload_uses_exact_registered_request_actor_and_entity_identities", ("workload.identity",)),
        ("H", f"test_{prefix}_workload_starts_only_after_the_declared_prerequisite_phase", ("workload.phase", "controller.barrier_gating")),
        ("H", f"test_{prefix}_workload_targets_the_registered_namespace_and_not_an_unrelated_deployment", ("workload.phase", "deployment.namespace")),
        ("H", f"test_{prefix}_workload_records_exact_declared_concurrency_and_completion_target", ("workload.identity", "workload.phase")),
        ("I", f"test_{prefix}_logical_tick_progression_is_identical_for_identical_seed_and_barrier_inputs", ("time.logical_ticks", "controller.seed")),
        ("I", f"test_{prefix}_ambient_wall_clock_delay_cannot_advance_a_barrier_gated_controller_phase", ("time.physical", "controller.barrier_gating")),
        ("J", f"test_{prefix}_recovery_witness_remains_false_until_the_declared_recovery_condition_is_observed", ("recovery.condition",)),
        ("J", f"test_{prefix}_controller_does_not_enter_post_recovery_phase_before_recovery_witness", ("recovery.condition", "controller.barrier_gating")),
        ("J", f"test_{prefix}_recovery_witness_identifies_the_declared_real_process_connectivity_and_resource_state", ("recovery.condition", "observations.provenance")),
        ("K", f"test_{prefix}_controller_emits_completion_only_after_every_required_action_and_barrier", ("termination.condition",)),
        ("L", f"test_{prefix}_second_run_inherits_no_database_broker_kubernetes_or_controller_state_from_first_run", ("reset.isolation",)),
        ("L", f"test_{prefix}_old_prerequisite_witness_is_invalid_for_a_new_run_identity", ("reset.isolation", "prerequisite.negative_control")),
        ("L", f"test_{prefix}_second_run_controller_uses_only_the_second_run_seed_and_parameters", ("reset.isolation", "controller.seed")),
        ("M", f"test_{prefix}_exports_every_oracle_consumed_observation_with_exact_entity_binding", ("observations.completeness",)),
        ("M", f"test_{prefix}_exports_each_observation_with_an_allowed_independent_provenance_class", ("observations.provenance",)),
        ("M", f"test_{prefix}_does_not_substitute_controller_or_orchestrator_values_for_real_application_observations", ("observations.provenance", "observations.no_business_answers")),
        ("N", f"test_{prefix}_scenario_output_contains_no_derived_expected_business_answer_or_pass_fail_field", ("observations.no_business_answers",)),
        ("N", f"test_{prefix}_changing_an_external_oracle_expectation_does_not_change_the_scenario_control_plan", ("observations.no_business_answers", "controller.seed")),
    ]
    if scenario.uses_litmus:
        specs.extend(
            [
                ("E", f"test_{prefix}_litmus_targets_exactly_the_registered_resource_and_affected_path", ("chaos.target",)),
                ("E", f"test_{prefix}_litmus_applies_exactly_the_registered_fault_primitive_and_parameters", ("chaos.target", "chaos.control_plane")),
                ("E", f"test_{prefix}_litmus_activates_only_after_the_declared_activation_barrier", ("chaos.activation", "controller.barrier_gating")),
                ("E", f"test_{prefix}_litmus_failure_cannot_be_recorded_as_successful_chaos_activation", ("chaos.control_plane", "prerequisite.negative_control")),
                ("E", f"test_{prefix}_litmus_effect_is_released_before_recovery_can_be_witnessed", ("chaos.release", "recovery.condition")),
                ("E", f"test_{prefix}_declared_unaffected_control_resource_remains_observable_during_chaos", ("chaos.unaffected_control",)),
                ("F", f"test_{prefix}_litmus_control_plane_acknowledgement_without_independent_effect_keeps_witness_false", ("chaos.control_plane", "prerequisite.negative_control")),
                ("F", f"test_{prefix}_independent_effect_witness_is_absent_before_chaos_activation", ("chaos.independent_effect", "prerequisite.negative_control")),
                ("F", f"test_{prefix}_independent_effect_witness_identifies_exact_affected_entities_during_active_phase", ("chaos.independent_effect", "prerequisite.witness")),
                ("F", f"test_{prefix}_independent_effect_witness_is_absent_after_declared_recovery", ("chaos.independent_effect", "recovery.condition")),
                ("L", f"test_{prefix}_second_run_contains_no_litmus_object_owned_by_the_first_run", ("reset.isolation", "chaos.release")),
            ]
        )
    return [
        {
            "dimension": dimension,
            "name": name,
            "covers": list(covers),
            "oracle_inference": "SAFE",
            "safety_evaluation": [
                {"reason_code": code, "criterion_text": text, "result": True}
                for code, text in UNIVERSAL_CRITERIA
            ],
            "emulation_specific_safety_gate": {
                "component_under_test_unambiguous": True,
                "scenario_precondition_unambiguous_or_unconditional": True,
                "required_real_deployment_observation_unambiguous": True,
                "required_contract_observation_relation_unambiguous": True,
                "required_witness_source_unambiguous": True,
            },
        }
        for dimension, name, covers in specs
    ]


def _scenario_markdown(
    scenario: ScenarioDefinition,
    consumers: list[str],
    clauses: dict[str, str],
) -> str:
    components = "\n".join(f"- `{component}`" for component in scenario.deployed_components)
    consumer_lines = "\n".join(f"- `{name}`" for name in consumers) or "- None"
    action_lines = "\n".join(
        f"{index}. `{action['action_id']}` — `{action['kind']}`; barrier `{action['requires_barrier']}`."
        for index, action in enumerate(_action_plan(scenario), start=1)
    )
    clause_lines = "\n".join(f"- `{key}` — {value}" for key, value in clauses.items())
    litmus = (
        f"`{scenario.litmus_experiment}` against `{scenario.target}` on `{scenario.affected_path}`"
        if scenario.uses_litmus
        else "None; the declared Dagger/application control action establishes the prerequisite."
    )
    runtime_target_binding = ""
    if scenario.uses_litmus:
        activation = next(
            action for action in _action_plan(scenario)
            if str(action["kind"]).startswith("LITMUS_")
        )
        policy = activation["adapter_policy"]
        target_policy = json.dumps(
            policy["request_fixed_fields"]["target"],
            sort_keys=True,
            separators=(",", ":"),
        )
        environment_policy = json.dumps(
            policy["environment_fixed_fields"],
            sort_keys=True,
            separators=(",", ":"),
        )
        runtime_target_binding = (
            f"- Runtime-enforced Kubernetes target policy: `{target_policy}`.\n"
            f"- Runtime-enforced fixed Litmus environment: `{environment_policy}`.\n"
        )
    blocker = scenario.blocker or "None. Implementation remains unverified by the intentionally future test catalog."
    return f"""# Scenario contract: `{scenario.scenario_id}`

- Scenario revision: `1`
- Status/trust: `{scenario.status}`
- AnySystem revision: `{ANYSYSTEM_REVISION}`
- Controller seed semantics: unsigned 64-bit seed; same scenario, revision, parameters, and barrier outcomes replay the same logical action order.
- Controller time: AnySystem logical phase ticks.
- Application-visible clock: `{scenario.application_clock_mode}`.

## Purpose

{scenario.purpose}

## Consuming context-specific business tests

{consumer_lines}

Context-independent atomic tests are selected from `classification.json` and are scheduled in every implemented scenario without duplicating that list here.

## Real deployment

Components:

{components}

- Namespace: unique per run, labelled with the exact run/scenario identity and explicitly rejected when its context resembles production.
- Artifact inputs: source commit, built image digests, rendered manifest digest, configuration digest, schema version, dependency/tool pins.
- External dependencies: {", ".join(scenario.deployed_components)}.
- Workload actors/input schema: `{scenario.workload}` with exact request, actor, entity, scenario, seed, and action identities.

## Initial state

The scenario runner creates only the minimal fixture requested by the consuming test, proves every required entity exists through real application/database/broker observations, records the selected real process/pod identities, and rejects empty or stale fixture evidence.

## Deterministic controller and action order

{action_lines}

The controller cannot advance on elapsed wall-clock time. Each barrier has a stable name, bounded timeout, success witness, failure artifact, and deterministic failure transition.

## Scenario-specific controlled action

- Action: `{scenario.action_kind}`
- Exact target: `{scenario.target}`
{runtime_target_binding.rstrip()}
- Affected path/resource: `{scenario.affected_path}`
- Activation barrier: {scenario.activation_barrier}.
- Litmus definition/reference: {litmus}.
- Control-plane acknowledgement: action/scenario/run-bound resource identity and status.
- Independent effect witness: {scenario.independent_witness}.
- Negative control: the prerequisite witness is false before activation and whenever either control-plane acknowledgement or the independent condition is absent.
- Release/recovery barrier: {scenario.release_barrier}.

## Prerequisite witness

The facts-only witness contains `scenario_id`, `scenario_revision`, `AnySystem_seed`, relevant entity IDs, established conditions, controller state, logical tick/phase, physical observation timestamp, trace position, control-plane evidence references, and independent-effect evidence references.

## Exported facts and provenance

Facts may include scenario inputs, request/entity IDs, real initial/final rows, message IDs, pod/process identities, observed status, controller phase, physical timestamps, fault state, and raw probe measurements. Every field records one of the allowed provenance classes from the registry. No `expected_*`, `should_*`, `business_valid`, `invariant_satisfied`, or pass/fail business field is emitted.

## Completion, recovery, and cleanup

Completion requires recovery, factual observation collection, and run-scoped cleanup barriers. Cleanup removes only exact scenario/run-labelled Kubernetes/Litmus resources, scenario-owned broker messages/fixtures, and controller state. Failure artifacts are retained outside the reset boundary.

## Residual nondeterminism

Linux/process scheduling, Kubernetes reconciliation, packet timing, database/broker scheduling, startup latency, and wall-clock duration remain physical observations. They do not choose the next controller action.

## Behavioral clauses

{clause_lines}

## Current blocker

{blocker}
"""


def _verification_markdown(scenario: ScenarioDefinition, specs: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[str]] = defaultdict(list)
    for spec in specs:
        grouped[spec["dimension"]].append(spec["name"])
    sections = []
    for dimension in "ABCDEFGHIJKLMN":
        names = grouped.get(dimension, [])
        if names:
            sections.append(
                f"## Dimension {dimension}\n\n" + "\n".join(f"- `{name}`" for name in names)
            )
    return (
        f"# Future emulation-verification tests: `{scenario.scenario_id}`\n\n"
        "These names specify future tests of the emulation contract. They are intentionally not pytest functions, are not passed, and do not assert business correctness.\n\n"
        + "\n\n".join(sections)
        + "\n"
    )


def _time_decision() -> str:
    return f"""# Deterministic time decision

## Decision

Use AnySystem logical phase ticks for deterministic controller ordering only. Do not inject application-visible time in this implementation. Treat physical wall-clock time as an observed nondeterministic input.

Pinned AnySystem revision: `{ANYSYSTEM_REVISION}`.

## `CONTROLLER_LOGICAL_TIME`

The controller increments one logical tick for every emitted action and every accepted barrier outcome. Identical scenario revision, parameters, seed, and barrier outcomes reproduce the same tick/action trace. Logical ticks never sleep and never stand in for a real witness.

## `APPLICATION_VISIBLE_TIME`

Mode: `REAL_UNINJECTED`.

Repository inspection found isolated unit-test clocks (`internal/testkit.ManualClock` and injectable rate-limiter/replay callbacks), but the real deployed path still uses Go `time.Now`, Rust `Utc::now`, and PostgreSQL `clock_timestamp()` at material trade-expiry, lease, retry, and settlement boundaries. There is no single safe observable deployment-only clock adapter spanning those components. Adding one in this task would risk divergent production semantics and would not control PostgreSQL or unrelated infrastructure clocks.

Time-sensitive business contracts therefore use authoritative database time or bounded real monotonic observations unless their execution remains unresolved. No scenario claims deterministic application time.

## `PHYSICAL_WALL_CLOCK_TIME`

Pod scheduling, network timing, database locks, process scheduling, broker scheduling, and recovery latency remain physical. Every timeout is a bounded failure guard; controller progress uses named witness/barrier outcomes rather than sleeping for an assumed duration.

## Future decision boundary

Application clock injection may be reconsidered only after a production-safe explicit clock interface exists for every affected real component, the emulation deployment makes it observable, PostgreSQL-time semantics are addressed, and future verification names can prove affected and unaffected clock domains independently.
"""


def build_scenarios(classification: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, tuple[str, str]]]:
    by_scenario: dict[str, list[str]] = defaultdict(list)
    for record in [*classification["records"], *classification["derived_business_tests"]]:
        scenario_id = record.get("emulation_scenario_id")
        if scenario_id and record["representation"] == "ATOMIC":
            by_scenario[scenario_id].append(record["name"])

    implemented_business = [
        record
        for record in [*classification["records"], *classification["derived_business_tests"]]
        if record.get("implementation_path")
    ]
    implemented_context_independent = sorted(
        record["name"] for record in implemented_business if record["context"] == "CONTEXT_INDEPENDENT"
    )

    active_definitions = [scenario for scenario in SCENARIOS if by_scenario.get(scenario.scenario_id)]
    outputs: dict[str, tuple[str, str]] = {}
    registry_records: list[dict[str, Any]] = []
    inventory_records: list[dict[str, Any]] = []
    total_verification_names = 0
    for scenario in active_definitions:
        consumers = sorted(set(by_scenario[scenario.scenario_id]))
        clauses = _contract_clauses(scenario)
        plan = _action_plan(scenario)
        activation_policy: dict[str, Any] = {}
        if scenario.uses_litmus:
            activation_policy = next(
                action["adapter_policy"]
                for action in plan
                if str(action["kind"]).startswith("LITMUS_")
            )
        scenario_dir = SCENARIO_ROOT / scenario.scenario_id
        contract_path = scenario_dir / "scenario-contract.md"
        implementation_path = scenario_dir / "scenario.json"
        action_reference_path = scenario_dir / (
            "chaos-reference.json" if scenario.uses_litmus else "control-action-reference.json"
        )
        verification_path = scenario_dir / "emulation-verification-tests-to-implement.md"
        coverage_path = scenario_dir / "emulation-verification-coverage.json"
        specs = _verification_specs(scenario) if scenario.status == IMPLEMENTED_PENDING else []
        total_verification_names += len(specs)
        implementation = {
            "schema_version": "eve-trade.emulation-scenario/v1",
            "scenario_id": scenario.scenario_id,
            "scenario_revision": "1",
            "status": scenario.status,
            "AnySystem_revision": ANYSYSTEM_REVISION,
            "controller_seed_policy": "required unsigned 64-bit integer",
            "uses_controller_logical_ticks": True,
            "application_clock_mode": scenario.application_clock_mode,
            "deployed_components": list(scenario.deployed_components),
            "workload": scenario.workload,
            "action_plan": plan,
            "barrier_timeout_policy": {
                "default_seconds": 120,
                "deployment_seconds": 600,
                "recovery_seconds": 300,
                "timeout_transition": "FAIL_CLOSED_AND_COLLECT_ARTIFACTS",
            },
            "prerequisite_witness": {
                "required_fields": [
                    "scenario_id",
                    "scenario_revision",
                    "run_id",
                    "namespace",
                    "AnySystem_seed",
                    "relevant_entity_ids",
                    "established_conditions",
                    "controller_state",
                    "logical_tick_or_phase",
                    "real_observation_timestamp",
                    "trace_position",
                    "control_plane_evidence_refs",
                    "independent_effect_evidence_refs",
                ],
                "activation_condition": scenario.activation_barrier,
                "independent_condition": scenario.independent_witness,
            },
            "observation_schema": {
                "facts_only": True,
                "allowed_provenance_classes": list(PROVENANCE_CLASSES),
                "forbidden_derived_field_prefixes": ["expected_", "should_"],
                "forbidden_derived_fields": [
                    "business_valid",
                    "invariant_satisfied",
                    "test_passed",
                    "correct_trade_state",
                ],
            },
            "behavioral_clauses": clauses,
            "blocker": scenario.blocker,
        }
        action_reference = {
            "schema_version": "eve-trade.scenario-action/v1",
            "scenario_id": scenario.scenario_id,
            "action_kind": scenario.action_kind,
            "target": scenario.target,
            "affected_path": scenario.affected_path,
            "activation_barrier": scenario.activation_barrier,
            "release_barrier": scenario.release_barrier,
            "control_plane_witness": (
                "scenario/action/run-bound Litmus resource status"
                if scenario.uses_litmus
                else "scenario/action/run-bound Dagger action receipt"
            ),
            "independent_effect_witness": scenario.independent_witness,
            "independent_effect_witness_adapter": _witness_adapter(scenario),
            "litmus_experiment": scenario.litmus_experiment,
            "runtime_target_policy": activation_policy.get("request_fixed_fields", {}).get("target"),
            "runtime_fixed_environment": activation_policy.get("environment_fixed_fields", {}),
        }
        coverage = {
            "schema_version": "eve-trade.emulation-verification-coverage/v1",
            "scenario_id": scenario.scenario_id,
            "scenario_status": scenario.status,
            "trust_claim_after_future_tests_pass": "EMULATION_VERIFIED_AGAINST_ITS_DECLARED_SCENARIO_CONTRACT",
            "current_trust_claim": scenario.status,
            "test_function_count": 0,
            "clauses": {
                clause: sorted(spec["name"] for spec in specs if clause in spec["covers"])
                for clause in clauses
            },
            "future_tests": specs,
        }
        outputs[contract_path.relative_to(PROPERTY_ROOT).as_posix()] = (
            "text",
            _scenario_markdown(scenario, consumers, clauses),
        )
        outputs[implementation_path.relative_to(PROPERTY_ROOT).as_posix()] = (
            "json",
            canonical_json(implementation),
        )
        outputs[action_reference_path.relative_to(PROPERTY_ROOT).as_posix()] = (
            "json",
            canonical_json(action_reference),
        )
        if specs:
            outputs[verification_path.relative_to(PROPERTY_ROOT).as_posix()] = (
                "text",
                _verification_markdown(scenario, specs),
            )
            outputs[coverage_path.relative_to(PROPERTY_ROOT).as_posix()] = (
                "json",
                canonical_json(coverage),
            )
        registry_records.append(
            {
                "scenario_id": scenario.scenario_id,
                "scenario_contract_path": contract_path.relative_to(PROPERTY_ROOT).as_posix(),
                "scenario_implementation_path": implementation_path.relative_to(PROPERTY_ROOT).as_posix(),
                "status": scenario.status,
                "consuming_business_tests": consumers,
                "context_independent_business_tests": {
                    "source": "classification.json",
                    "selection": "oracle_inference=SAFE AND representation=ATOMIC AND context=CONTEXT_INDEPENDENT",
                    "required_in_every_implemented_scenario": True,
                    "implemented_test_names": implemented_context_independent,
                },
                "deployment_inputs": [
                    "source_revision",
                    "artifact_and_image_digests",
                    "deployment_manifest_digest",
                    "configuration_digest",
                    "schema_version",
                    "dependency_versions",
                    "kubernetes_environment_identity",
                    "Dagger_version",
                    "Litmus_version",
                ],
                "AnySystem_revision": ANYSYSTEM_REVISION,
                "controller_seed_policy": "required unsigned 64-bit integer; seed affects only declared controller choices",
                "uses_controller_logical_ticks": True,
                "application_clock_mode": scenario.application_clock_mode,
                "chaos_actions": (
                    [
                        {
                            "action_kind": scenario.action_kind,
                            "litmus_experiment": scenario.litmus_experiment,
                            "target": scenario.target,
                            "runtime_target_policy": activation_policy["request_fixed_fields"]["target"],
                            "runtime_fixed_environment": activation_policy["environment_fixed_fields"],
                            "affected_path": scenario.affected_path,
                        }
                    ]
                    if scenario.uses_litmus
                    else []
                ),
                "barriers": [action["requires_barrier"] for action in plan],
                "witnesses": {
                    "control_plane": action_reference["control_plane_witness"],
                    "independent_effect": scenario.independent_witness,
                    "negative_control_required": True,
                },
                "observation_outputs": [
                    "controller-trace.json",
                    "dagger-action-trace.json",
                    "prerequisite-witness.json",
                    "observations.json",
                    "litmus-artifacts/" if scenario.uses_litmus else "control-action-artifacts/",
                ],
                "verification_test_catalog_path": (
                    verification_path.relative_to(PROPERTY_ROOT).as_posix() if specs else None
                ),
                "verification_coverage_path": (
                    coverage_path.relative_to(PROPERTY_ROOT).as_posix() if specs else None
                ),
                "future_verification_test_name_count": len(specs),
                "blocker": scenario.blocker,
            }
        )
        inventory_records.append(
            {
                "scenario_id": scenario.scenario_id,
                "status": scenario.status,
                "prerequisite_contract": {
                    "components": list(scenario.deployed_components),
                    "initial_state": "minimal consuming-test fixture plus clean deployment/broker/database baseline",
                    "workload": scenario.workload,
                    "action_kind": scenario.action_kind,
                    "target": scenario.target,
                    "affected_path": scenario.affected_path,
                    "activation_barrier": scenario.activation_barrier,
                    "independent_witness": scenario.independent_witness,
                    "release_barrier": scenario.release_barrier,
                },
                "consuming_business_tests": consumers,
            }
        )

    status_counts = Counter(record["status"] for record in registry_records)
    registry = {
        "schema_version": "eve-trade.emulation-scenario-registry/v1",
        "authoritative_inputs": classification["authoritative_inputs"],
        "AnySystem": {
            "repository": "https://github.com/systems-group/anysystem",
            "branch_inspected": "main",
            "revision": ANYSYSTEM_REVISION,
            "main_at_task_start": ANYSYSTEM_REVISION,
            "role": "deterministic scenario controller only",
            "forbidden_roles": [
                "EVE Trade simulation",
                "business oracle",
                "model checking",
                "simulated network or node failure substituted for real Litmus/Dagger action",
            ],
        },
        "provenance_classes": list(PROVENANCE_CLASSES),
        "counts": {
            "canonical_scenarios_identified": len(registry_records),
            "canonical_scenarios_implemented": status_counts[IMPLEMENTED_PENDING],
            "canonical_scenarios_planned": status_counts[PLANNED],
            "scenarios_using_litmus": sum(bool(record["chaos_actions"]) for record in registry_records),
            "scenarios_with_independent_effect_witnesses": len(registry_records),
            "scenarios_using_anysystem_logical_ticks": len(registry_records),
            "scenarios_using_application_visible_clock_injection": sum(
                record["application_clock_mode"] == "INJECTED" for record in registry_records
            ),
            "emulation_verification_test_names_generated": total_verification_names,
            "emulation_verification_test_functions_implemented": 0,
        },
        "scenarios": registry_records,
    }
    inventory = {
        "schema_version": "eve-trade.emulation-prerequisite-inventory/v1",
        "clustering_rule": "tests share a scenario only when components, state, workload phase, controlled action, barriers, witnesses, recovery, and observations are semantically equivalent",
        "records": inventory_records,
    }
    state = {
        "schema_version": "eve-trade.property-implementation-state/v1",
        "trust_ceiling": IMPLEMENTED_PENDING,
        "emulation_verification_test_functions_implemented": 0,
        "scenario_counts": registry["counts"],
        "business": {
            "oracles_implemented": len(reusable_oracle_sources()),
            "oracle_sources": reusable_oracle_sources(),
            "tests_implemented": len(implemented_business),
            "test_sources": {
                record["name"]: record["implementation_path"] for record in implemented_business
            },
            "tests_passing": 0,
            "tests_failing": 0,
            "tests_blocked": classification["counts"]["safe_atomic_tests"],
            "blocked_breakdown": {
                "implemented_awaiting_real_deployment_execution": len(implemented_business),
                "not_implemented": (
                    classification["counts"]["safe_atomic_tests"]
                    - len(implemented_business)
                ),
            },
            "tests_implemented_on_pending_emulation_verification": len(implemented_business),
            "execution_blocker": (
                "No disposable real-deployment scenario invocation was supplied in this workspace; "
                "context-independent tests are collected but intentionally never run against fabricated evidence."
            ),
        },
        "remaining_state": {
            "unsafe_unresolved_contracts": classification["counts"]["unsafe_names"],
            "safe_composite_parents_awaiting_child_implementation": classification["counts"]["safe_composite_parents"],
            "safe_atomic_tests_not_implemented": (
                classification["counts"]["safe_atomic_tests"] - len(implemented_business)
            ),
            "planned_emulation_scenarios": registry["counts"]["canonical_scenarios_planned"],
            "implemented_scenarios_awaiting_emulation_verification": registry["counts"]["canonical_scenarios_implemented"],
        },
        "validation": {
            "results_path": "infra/emulated-scenarios/validation-results.json",
            "generated_state_does_not_claim_execution": True,
        },
    }
    return registry, inventory, state, outputs


def generate(check: bool) -> None:
    stale: list[str] = []
    classification = build_classification()
    registry, inventory, state, outputs = build_scenarios(classification)
    expected_output_paths = {PROPERTY_ROOT / relative for relative in outputs}
    for scenario_dir in sorted(path for path in SCENARIO_ROOT.iterdir() if path.is_dir()):
        for filename in (
            "emulation-verification-tests-to-implement.md",
            "emulation-verification-coverage.json",
        ):
            candidate = scenario_dir / filename
            if candidate.exists() and candidate not in expected_output_paths:
                relative = candidate.relative_to(PROPERTY_ROOT).as_posix()
                if check:
                    stale.append(relative)
                else:
                    candidate.unlink()
    _write_or_check(CLASSIFICATION_PATH, canonical_json(classification), check, stale)
    _write_or_check(REGISTRY_PATH, canonical_json(registry), check, stale)
    _write_or_check(PREREQUISITE_INVENTORY_PATH, canonical_json(inventory), check, stale)
    _write_or_check(STATE_PATH, canonical_json(state), check, stale)
    _write_or_check(TIME_DECISION_PATH, _time_decision(), check, stale)
    for relative, (_, content) in sorted(outputs.items()):
        _write_or_check(PROPERTY_ROOT / relative, content, check, stale)
    if stale:
        raise SystemExit("stale deterministic emulation artifacts:\n" + "\n".join(stale))
    if not check:
        print(
            canonical_json(
                {
                    "classification": CLASSIFICATION_PATH.relative_to(PROPERTY_ROOT).as_posix(),
                    "scenario_registry": REGISTRY_PATH.relative_to(PROPERTY_ROOT).as_posix(),
                    "classification_counts": classification["counts"],
                    "scenario_counts": registry["counts"],
                }
            ),
            end="",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
