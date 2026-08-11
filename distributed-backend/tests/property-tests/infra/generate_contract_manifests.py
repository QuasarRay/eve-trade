#!/usr/bin/env python3
"""Generate exhaustive requirement and Litmus manifests from the authoritative catalog.

The rules in this file are deliberately reviewable.  Generated JSON contains the
resolved result for every exact contract name, while meta-tests compare it back to
``tests-to-implement.md`` and the collected pytest suite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any


INFRA_ROOT = Path(__file__).resolve().parent
PROPERTY_ROOT = INFRA_ROOT.parent
E2E_ROOT = PROPERTY_ROOT / "e2e"
CATALOG_MD = PROPERTY_ROOT / "tests-to-implement.md"
CATALOG_JSON = E2E_ROOT / "eve_trade_hypothesis" / "catalog.json"
LEGACY_AUDIT = E2E_ROOT / "audit_resolution_manifest.json"
SEMANTIC_OVERRIDE_MANIFEST = E2E_ROOT / "semantic_override_manifest.json"

MECHANISMS = {
    "DIRECT_LIVE",
    "LITMUS_CHAOS",
    "PLATFORM_EXTERNAL",
    "REPOSITORY_STATIC",
    "NATIVE_EXISTING",
    "NON_APPLICABLE",
}

STATIC_CATEGORIES = {
    25, 35, 38, 49, 51, 56, 57, 65, 67, 68, 69, 70, 71, 72,
    73, 74, 75, 76, 77, 78, 79, 84, 90, 91, 92, 93, 98, 99, 100,
}
REPO_RUNNER_CATEGORIES = {
    25, 26, 27, 34, 35, 37, 38, 45, 49, 50, 51, 52, 53, 55, 56, 57,
    65, 67, 68, 69, 70, 71, 72, 73, 74, 75, 77, 80, 81, 84, 85, 88, 89,
    90, 91, 92, 93, 98, 99, 100,
}
FAULT_RUNNER_CATEGORIES = {24, 36, 44, 46, 54, 64, 66, 101}

PLATFORM_EXTERNAL_TOKENS = (
    "primary_failover",
    "new_primary",
    "demoted_primary",
    "database_restore",
    "restored_database",
    "restore_to_point",
    "point_after_settlement",
    "point_before_settlement",
    "managed_database",
    "deployed_backend_image_sha",
    "deployed_settlement_image_sha",
    "rendered_manifest_sha_matches_deployed",
)

CHAOS_TOKENS = (
    "network_partition",
    "asymmetric_partition",
    "network_delay",
    "network_loss",
    "packet_loss",
    "packet_duplication",
    "pod_deletion",
    "pod_delete",
    "node_drain",
    "service_kill",
    "process_sigkill",
    "container_kill",
    "cpu_saturation",
    "memory_pressure",
    "disk_full",
    "dns_resolution_failure",
    "half_open_",
    "postgres_restart",
    "nsq_restart",
    "broker_restart",
    "dependency_outage",
    "random_postgres_disconnects",
    "random_service_kill",
    "random_crash_and_restart",
    "crash_after_",
    "crash_before_",
    "crash_during_",
    "worker_replica_crash",
    "worker_pod_deletion",
    "worker_under_memory_pressure",
    "trade_settlement_pod_deletion",
    "gateway_under_memory_pressure",
)

CHAOS_ASSERTION_META = {
    "test_chaos_experiment_failure_to_inject_fault_fails_test_rather_than_reporting_pass",
    "test_chaos_experiment_verifies_at_least_one_request_crossed_targeted_failure_window",
}

# These contracts have repository-owned, observation-based oracles in
# ``e2e/eve_trade_hypothesis/chaos_oracles.py``.  Other Litmus routes have a
# concrete fault mechanism but remain fail-closed until their named business
# workload and authoritative-state oracle exist.
IMPLEMENTED_CHAOS_CONTRACTS = {
    "test_litmus_pod_delete_experiment_targets_only_selected_eve_trade_workload_labels",
    "test_litmus_network_loss_experiment_proves_impairment_is_active_before_workload_assertions_begin",
    "test_litmus_network_delay_experiment_records_injected_latency_range_in_test_evidence",
    "test_litmus_experiment_cleanup_restores_all_affected_network_and_pod_resources",
}

NON_APPLICABLE: dict[str, str] = {
    "test_issue_rejects_item_stack_with_wrong_item_type_claim": (
        "The current authoritative-state API intentionally ignores an untrusted item-type claim and "
        "uses the persisted source-stack item type; the catalog separately requires that behavior in "
        "test_issue_uses_authoritative_item_type_instead_of_client_claim. Both outcomes cannot hold for "
        "the same request contract. The name is retained exactly and classified, never silently renamed."
    ),
    "test_gateway_to_market_network_partition_returns_no_false_business_success": (
        "Gateway and Market are compiled into the same Encore backend process and share one pod/network "
        "namespace in the current deployment. Kubernetes network chaos cannot partition an in-process "
        "function call. A future service split or a narrow production-disabled call-boundary failpoint "
        "would make this contract applicable."
    ),
}


def catalog_names() -> list[str]:
    names = re.findall(r"`(test_[a-z0-9_]+)`", CATALOG_MD.read_text(encoding="utf-8"))
    if len(names) != len(set(names)):
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        raise RuntimeError(f"authoritative catalog has duplicate names: {duplicates[:10]}")
    return names


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_JSON.read_text(encoding="utf-8"))


def catalog_index(catalog: dict[str, Any]) -> tuple[dict[str, tuple[int, str]], set[str]]:
    proposed: dict[str, tuple[int, str]] = {}
    for category_key, category in catalog["categories"].items():
        for name in category["names"]:
            proposed[name] = (int(category_key), str(category["title"]))
    return proposed, set(catalog["existing"])


def classify(name: str, category: int | None, existing: set[str]) -> str:
    if name in existing:
        return "NATIVE_EXISTING"
    if name in NON_APPLICABLE:
        return "NON_APPLICABLE"
    if any(token in name for token in PLATFORM_EXTERNAL_TOKENS) or category in {46, 53}:
        return "PLATFORM_EXTERNAL"
    if name in CHAOS_ASSERTION_META:
        return "REPOSITORY_STATIC"
    if category == 66 and not any(token in name for token in ("pod_deletion", "node_drain")):
        return "REPOSITORY_STATIC"
    if category == 101:
        return "LITMUS_CHAOS"
    if category in {24, 44, 54} or any(token in name for token in CHAOS_TOKENS):
        return "LITMUS_CHAOS"
    if category == 36 and any(token in name for token in ("rolling_", "restart", "replacement")):
        return "LITMUS_CHAOS"
    if category == 64 and any(token in name for token in ("replacement", "unready", "replica_crash")):
        return "LITMUS_CHAOS"
    if category in STATIC_CATEGORIES:
        return "REPOSITORY_STATIC"
    return "DIRECT_LIVE"


def _legacy_records() -> dict[str, dict[str, Any]]:
    document = json.loads(LEGACY_AUDIT.read_text(encoding="utf-8"))
    records: dict[str, dict[str, Any]] = {}
    for record in document["records"]:
        # The previous suite renamed one authoritative contract. Bind its audit
        # record back to original_name so identity remains catalog-controlled.
        records[str(record.get("original_name") or record["name"])] = record
    return records


@lru_cache(maxsize=1)
def _semantic_overrides() -> dict[str, list[str]]:
    document = json.loads(SEMANTIC_OVERRIDE_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("semantic override manifest must be an object")
    return document


def status_for(
    name: str,
    mechanism: str,
    legacy: dict[str, dict[str, Any]],
    category: int | None,
) -> tuple[str, list[str]]:
    if mechanism == "NON_APPLICABLE":
        return "JUSTIFIED_NON_APPLICABLE", []
    if mechanism == "NATIVE_EXISTING":
        return "IMPLEMENTED", []
    if mechanism == "PLATFORM_EXTERNAL":
        return "EXTERNAL_CAPABILITY_REQUIRED", [
            "requires an independently provisioned platform capability; no local or Litmus substitute is accepted"
        ]
    if mechanism == "LITMUS_CHAOS":
        if name in IMPLEMENTED_CHAOS_CONTRACTS:
            return "IMPLEMENTED", []
        return "SEMANTIC_ORACLE_REQUIRED", [
            "Litmus injection/evidence transport is implemented, but this exact business workload/oracle still requires a contract probe"
        ]
    if mechanism == "REPOSITORY_STATIC" and category == 78:
        return "SEMANTIC_ORACLE_REQUIRED", [
            "requires a real parser fuzz harness and crash/acceptance oracle; the repository runner has no category-78 implementation"
        ]
    if name in {
        "test_proposed_test_names_using_retry_identify_whether_business_effect_may_repeat",
        "test_proposed_test_names_using_crash_identify_crash_window_and_post_restart_invariant",
    }:
        return "SEMANTIC_ORACLE_REQUIRED", [
            "requires a reviewed per-name semantic classification; token heuristics false-red on valid contracts and cannot prove the naming rule"
        ]
    if name == "test_every_settlement_operation_enum_value_has_success_and_rejection_test_reference":
        return "SEMANTIC_ORACLE_REQUIRED", [
            "requires an independent enum-to-success-test-and-rejection-test traceability matrix; a single textual token occurrence cannot prove both references"
        ]
    if mechanism == "DIRECT_LIVE" and category in {26, 50}:
        return "SEMANTIC_ORACLE_REQUIRED", [
            "requires schema-specific live transaction setup that attempts the exact invalid row and proves PostgreSQL rejected it without persisted side effects"
        ]
    semantic_overrides = _semantic_overrides()
    if name in semantic_overrides:
        kinds = ", ".join(sorted(str(item) for item in semantic_overrides[name]))
        return "SEMANTIC_ORACLE_REQUIRED", [
            "source audit found unresolved semantic implementation defects: " + kinds
        ]
    record = legacy.get(name)
    findings = list(record.get("findings", [])) if record else []
    semantic_findings = [
        finding for finding in findings if finding.get("kind") != "hypothesis_strategy_precedence"
    ]
    if not semantic_findings:
        return "IMPLEMENTED", []
    kinds = sorted({str(finding.get("kind")) for finding in semantic_findings})
    return "SEMANTIC_ORACLE_REQUIRED", [
        "legacy audit found unresolved semantic implementation defects: " + ", ".join(kinds)
    ]


def runner_for(mechanism: str, category: int | None) -> str:
    if mechanism == "NATIVE_EXISTING":
        return "native"
    if mechanism == "NON_APPLICABLE":
        return "non_applicable"
    if mechanism == "LITMUS_CHAOS":
        return "litmus"
    if mechanism == "PLATFORM_EXTERNAL":
        return "platform"
    if mechanism == "REPOSITORY_STATIC":
        return "repo"
    if category in FAULT_RUNNER_CATEGORIES:
        return "fault"
    if category in REPO_RUNNER_CATEGORIES:
        return "repo"
    if category in {3, 4, 5, 6, 7, 28, 29, 30, 31, 48, 61, 76, 78, 79, 86}:
        return "edge"
    return "trade"


def prerequisite_for(name: str, mechanism: str) -> str:
    if mechanism == "NATIVE_EXISTING":
        return "the native E2E environment and the exact native pytest node"
    if mechanism == "REPOSITORY_STATIC":
        return "the checked-out source/configuration plus any pinned local compiler or validation tool"
    if mechanism == "DIRECT_LIVE":
        return "an isolated seeded EVE Trade deployment with authoritative PostgreSQL state"
    if mechanism == "LITMUS_CHAOS":
        return "an isolated chaos-safe Kubernetes namespace, active Litmus fault, synchronized generated workload, and recovery window"
    if mechanism == "PLATFORM_EXTERNAL":
        return "the named managed platform/control-plane capability with an independent evidence collector"
    return NON_APPLICABLE[name]


def observable_for(name: str, mechanism: str) -> str:
    if mechanism == "REPOSITORY_STATIC":
        return "deterministic command/configuration/schema output evaluated by the local Python oracle"
    if mechanism == "NATIVE_EXISTING":
        return "the native test's assertions and exit status, without a Hypothesis hash-seed wrapper"
    if mechanism == "LITMUS_CHAOS":
        return "Litmus resources, independent target effects, request/fault temporal overlap, recovery, and authoritative post-fault state"
    if mechanism == "PLATFORM_EXTERNAL":
        return "fresh protocol-v3 raw platform observations evaluated by the independent Python oracle"
    if mechanism == "NON_APPLICABLE":
        return "the explicit architecture contradiction and retained catalog identity"
    return "API/transport results plus independently queried PostgreSQL, queue, and service state"


def target_for(name: str) -> dict[str, str]:
    if "quilkin" in name or "between_quilkin" in name:
        return {"workload": "quilkin", "kind": "deployment", "selector": "app.kubernetes.io/name=quilkin"}
    if any(token in name for token in ("postgres", "database_connection", "database_commit", "database_logging")):
        return {"workload": "postgres", "kind": "deployment", "selector": "app.kubernetes.io/name=postgres"}
    if any(token in name for token in ("nsq", "pubsub", "broker")):
        return {"workload": "nsqd", "kind": "statefulset", "selector": "app.kubernetes.io/name=nsqd"}
    if any(token in name for token in ("trade_settlement", "settlement_service", "settlement_to_postgres")):
        return {"workload": "trade-settlement", "kind": "deployment", "selector": "app.kubernetes.io/name=trade-settlement"}
    return {"workload": "encore-backend", "kind": "deployment", "selector": "app.kubernetes.io/name=encore-backend"}


def network_peer_for(name: str) -> dict[str, str] | None:
    if "between_quilkin_and_gateway" in name:
        return {"workload": "encore-backend", "selector": "app.kubernetes.io/name=encore-backend"}
    if "market_to_pubsub" in name or "network_partition_recovery" in name:
        return {"workload": "nsqd", "selector": "app.kubernetes.io/name=nsqd"}
    if "worker_to_settlement" in name or "half_open_grpc" in name or "settlement_endpoint" in name:
        return {"workload": "trade-settlement", "selector": "app.kubernetes.io/name=trade-settlement"}
    if "settlement_to_postgres" in name or "half_open_postgres" in name:
        return {"workload": "postgres", "selector": "app.kubernetes.io/name=postgres"}
    return None


def fault_for(name: str) -> tuple[str, str, dict[str, Any]]:
    if "primary_failover" in name or "failover" in name:
        raise ValueError(f"managed database failover must not be mapped to Litmus: {name}")
    if "network_partition" in name or "asymmetric_partition" in name:
        return "network_partition", "pod-network-partition", {"policy_types": ["all", "egress"]}
    if "packet_duplication" in name:
        return "packet_duplication", "pod-network-duplication", {"duplication_percent": [10, 50, 100]}
    if "packet_loss" in name or "network_loss" in name:
        return "packet_loss", "pod-network-loss", {"network_loss_percent": [10, 50, 100]}
    if "network_delay" in name or "latency" in name and "slo" not in name:
        return "network_latency", "pod-network-latency", {"network_latency_ms": [50, 250, 1000, 2000]}
    if "dns_" in name:
        return "dns_error", "pod-dns-error", {"dns_match_scheme": ["exact", "substring"]}
    if "cpu_saturation" in name:
        return "cpu_pressure", "pod-cpu-hog", {"cpu_load_percent": [25, 75, 100]}
    if "memory_pressure" in name:
        return "memory_pressure", "pod-memory-hog", {"memory_mib": [64, 128, 256]}
    if "disk_full" in name:
        return "disk_pressure", "disk-fill", {"filesystem_utilization_percent": [20, 35, 50]}
    if "node_drain" in name:
        return "node_drain", "node-drain", {"affected_node_count": [1]}
    if any(token in name for token in ("sigkill", "container_kill", "crash_after_", "crash_before_", "crash_during_")):
        return "container_kill", "container-kill", {"signal": ["SIGKILL"]}
    return "pod_delete", "pod-delete", {"pods_affected_percent": [50, 100]}


def workload_for(name: str) -> str:
    if name in IMPLEMENTED_CHAOS_CONTRACTS:
        return "in_cluster_service_probe_batch"
    if "mixed_trade" in name or "random_" in name:
        return "mixed_issue_accept_cancel"
    if "udp" in name or "gateway" in name or "quilkin" in name:
        return "authenticated_udp_requests"
    if "cancel" in name:
        return "cancel_trade"
    if "accept" in name:
        return "accept_trade"
    if "issue" in name or "create_trade" in name:
        return "issue_trade"
    if any(token in name for token in ("outbox", "pubsub", "message", "worker")):
        return "settlement_message_workload"
    return "contract_specific_business_workload"


def invariant_queries(name: str) -> list[str]:
    implemented = {
        "test_litmus_pod_delete_experiment_targets_only_selected_eve_trade_workload_labels": [
            "every_selected_target_resource_matches_the_exact_contract_selector"
        ],
        "test_litmus_network_loss_experiment_proves_impairment_is_active_before_workload_assertions_begin": [
            "clean_baseline_and_raw_effect_batch_prove_active_network_loss"
        ],
        "test_litmus_network_delay_experiment_records_injected_latency_range_in_test_evidence": [
            "raw_effect_batch_median_delta_proves_generated_latency_range"
        ],
        "test_litmus_experiment_cleanup_restores_all_affected_network_and_pod_resources": [
            "zero_execution_scoped_chaos_resources_and_original_logical_readiness_restored"
        ],
    }
    if name in implemented:
        return implemented[name]
    queries = ["authoritative_contract_postcondition"]
    if "item" in name or "conserv" in name:
        queries.append("total_items_before_equals_total_items_after")
    if "isk" in name or "wallet" in name or "conserv" in name:
        queries.append("total_isk_before_equals_total_isk_after")
    if any(token in name for token in ("idempot", "duplicate", "exactly_once", "one_business_effect")):
        queries.append("irreversible_business_effect_count_at_most_one_per_idempotency_key")
    if "stale_processing" in name:
        queries.append("stale_processing_operation_count_equals_zero")
    if "outbox" in name:
        queries.append("permanently_claimed_unpublished_outbox_count_equals_zero")
    if "terminal" in name or "trade_state" in name:
        queries.append("duplicate_terminal_trade_state_change_count_equals_zero")
    return list(dict.fromkeys(queries))


def build_litmus_contract(requirement: dict[str, Any]) -> dict[str, Any]:
    name = requirement["name"]
    infrastructure_oracle = name in IMPLEMENTED_CHAOS_CONTRACTS
    fault_family, experiment, generated = fault_for(name)
    target = target_for(name)
    generated_parameters: dict[str, dict[str, Any]] = {
        "fault_duration_seconds": {"values": [15, 30], "litmus_env": "TOTAL_CHAOS_DURATION"},
        "request_count": {"min": 2, "max": 32, "workload_field": "request_count"},
        "start_offset_ms": {"values": [0, 50, 250, 1000], "workload_field": "start_offset_ms"},
        "recovery_deadline_seconds": {"values": [30, 60, 120, 180], "oracle_field": "recovery_deadline_seconds"},
    }
    litmus_env = {
        "pods_affected_percent": "PODS_AFFECTED_PERC",
        "network_loss_percent": "NETWORK_PACKET_LOSS_PERCENTAGE",
        "duplication_percent": "NETWORK_PACKET_DUPLICATION_PERCENTAGE",
        "network_latency_ms": "NETWORK_LATENCY",
        "dns_match_scheme": "MATCH_SCHEME",
        "cpu_load_percent": "CPU_LOAD",
        "memory_mib": "MEMORY_CONSUMPTION",
        "filesystem_utilization_percent": "FILL_PERCENTAGE",
        "signal": "SIGNAL",
        "policy_types": "POLICY_TYPES",
    }
    workload_only = {"affected_node_count"}
    for parameter, values in generated.items():
        record: dict[str, Any] = {"values": values}
        if parameter in workload_only:
            record["driver_field"] = parameter
        else:
            record["litmus_env"] = litmus_env[parameter]
        generated_parameters[parameter] = record
    effect_signal = {
        "pod-delete": "target_pod_uid_or_deletion_timestamp_changed",
        "container-kill": "target_container_restart_count_increased",
        "pod-network-partition": "independent_network_probe_failed_for_selected_edge",
        "pod-network-loss": "measured_packet_loss_reached_generated_lower_bound",
        "pod-network-duplication": "measured_duplicate_datagrams_increased",
        "pod-network-latency": "measured_latency_reached_generated_lower_bound",
        "pod-dns-error": "independent_dns_probe_observed_generated_error",
        "pod-cpu-hog": "target_container_cpu_usage_increased_during_window",
        "pod-memory-hog": "target_container_memory_working_set_increased_during_window",
        "disk-fill": "target_filesystem_utilization_increased_during_window",
        "node-drain": "selected_node_became_unschedulable_and_target_pod_moved",
    }[experiment]
    static_env: dict[str, str] = {}
    network_peer = network_peer_for(name)
    if experiment == "pod-network-partition":
        if network_peer is None:
            network_peer = {
                "workload": "nsqd",
                "selector": "app.kubernetes.io/name=nsqd",
            }
        static_env.update(
            {
                "POD_SELECTOR": network_peer["selector"],
                "NAMESPACE_SELECTOR": "kubernetes.io/metadata.name=${APP_NAMESPACE}",
            }
        )
    if experiment == "pod-dns-error":
        peer = network_peer or {
            "workload": "trade-settlement",
            "selector": "app.kubernetes.io/name=trade-settlement",
        }
        static_env["TARGET_HOSTNAMES"] = '["' + peer["workload"] + '.${APP_NAMESPACE}.svc.cluster.local"]'
    required_initial_state = (
        [
            "fresh per-run chaos-safe namespace",
            "exact target selector resolves ready pods",
            "all in-cluster control probes reach the encore-backend Service",
        ]
        if infrastructure_oracle
        else [
            "fresh per-run namespace and database",
            "deterministically seeded seller, buyer, wallets, item stack, and trade state required by workload",
            "zero pending settlement messages from prior examples",
        ]
    )
    precondition_probes = (
        [
            "namespace_has_matching_chaos_safe_run_label",
            "target_selector_resolves_at_least_one_ready_pod",
            "clean_in_cluster_service_probe_baseline_succeeds",
        ]
        if infrastructure_oracle
        else [
            "namespace_has_matching_chaos_safe_run_label",
            "target_selector_resolves_at_least_one_ready_pod",
            "service_readiness_and_database_baseline_succeed",
            "contract_workload_dry_run_or_control_case_succeeds",
        ]
    )
    raw_observations = (
        [
            "raw ChaosEngine and UID-bound ChaosResult resources",
            "raw selected target pods with labels, UIDs, and resourceVersions",
            "in-cluster baseline/effect/workload probe timestamps and measurements",
            "runner injection logs plus readiness and execution-scoped cleanup probes",
        ]
        if infrastructure_oracle
        else [
            "ChaosEngine, ChaosResult, runner/helper job identities and timestamps",
            "target pod UID, container IDs, restart counts, resourceVersions, and Kubernetes events",
            "timestamped workload requests/responses with interaction and idempotency IDs",
            "authoritative PostgreSQL before/after rows and aggregate queries required by invariant",
            "readiness/recovery probes and cleanup verification",
        ]
    )
    return {
        "contract": name,
        "catalog": {
            "category": requirement["category"],
            "title": requirement["category_title"],
        },
        "fault_family": fault_family,
        "target": target,
        "generated_parameters": generated_parameters,
        "required_initial_state": required_initial_state,
        "precondition_probes": precondition_probes,
        "fault_injection": {
            "mechanism": "Litmus ChaosEngine referencing a pinned ChaosExperiment",
            "experiment": experiment,
            "engine_state": "active",
            "annotation_check": "false",
            "service_account": "eve-trade-chaos-runner",
            "static_environment": static_env,
            "network_peer": network_peer,
        },
        "fault_effect_proof": {
            "required_distinct_sources": 2,
            "signals": [
                "fresh ChaosEngine and ChaosResult UIDs with experiment phase/verdict",
                effect_signal,
                "Kubernetes event or resourceVersion transition scoped to selected target",
            ],
            "chaos_result_passed_alone_is_sufficient": False,
        },
        "fault_window": {
            "activation": "first independently observed target effect after Litmus runner starts",
            "clearance": "first post-revert probe proving target effect is absent",
        },
        "workload": {
            "action": workload_for(name),
            "generated_case_binding": "all generated_parameters values are recorded as applied actions",
            "overlap_proof": "at least one request interval intersects [fault_active_at, fault_cleared_at]",
            "effect_binding": (
                "network workload requests are exact members of the raw effect-bearing probe batch; "
                "pod-delete workload requires a clean baseline and an observed unavailable response"
                if infrastructure_oracle
                else "requires a contract-specific physical-effect/workload binding before implementation"
            ),
            "synchronization": (
                "a contract-specific production-disabled boundary signal is mandatory before injection"
                if any(token in name for token in ("crash_after_", "crash_before_", "crash_during_", "persistence_boundary"))
                else "physical target-effect observation opens the workload barrier"
            ),
        },
        "raw_observations": raw_observations,
        "recovery": {
            "condition": "target rollout ready, required services healthy, fault effect absent, and contract backlog drained",
            "deadline_parameter": "recovery_deadline_seconds",
        },
        "post_recovery_invariants": invariant_queries(name),
        "cleanup": [
            "delete exact ChaosEngine, ChaosResult, runner/helper jobs, and barrier ConfigMap by execution labels",
            "verify network qdisc/resource stress is reverted",
            "reset deterministic business state",
            "delete per-run namespace when the pipeline owns it",
        ],
        "evidence_schema": "eve-trade.chaos-evidence/v1",
    }


def generate() -> tuple[dict[str, Any], dict[str, Any]]:
    authoritative = catalog_names()
    catalog = load_catalog()
    proposed, existing = catalog_index(catalog)
    registry_names = set(proposed) | existing
    if set(authoritative) != registry_names:
        missing = sorted(set(authoritative) - registry_names)
        extra = sorted(registry_names - set(authoritative))
        raise RuntimeError(
            "generated catalog differs from authoritative tests-to-implement.md; "
            f"missing={missing}, extra={extra}"
        )
    legacy = _legacy_records()
    contracts: list[dict[str, Any]] = []
    for name in authoritative:
        category, title = proposed.get(name, (None, "Existing native E2E contract"))
        mechanism = classify(name, category, existing)
        if mechanism not in MECHANISMS:
            raise AssertionError(mechanism)
        status, blockers = status_for(name, mechanism, legacy, category)
        contracts.append({
            "name": name,
            "category": category,
            "category_title": title,
            "mechanism": mechanism,
            "implementation_status": status,
            "runner": runner_for(mechanism, category),
            "rationale": (
                NON_APPLICABLE[name]
                if mechanism == "NON_APPLICABLE"
                else f"Primary execution route derived from production topology and {title}."
            ),
            "prerequisite": prerequisite_for(name, mechanism),
            "observable": observable_for(name, mechanism),
            "source": (
                "native E2E inventory" if mechanism == "NATIVE_EXISTING" else "tests-to-implement.md"
            ),
            "blockers": blockers,
        })
    counts = Counter(record["mechanism"] for record in contracts)
    status_counts = Counter(record["implementation_status"] for record in contracts)
    requirements = {
        "schema_version": "eve-trade.test-requirements/v1",
        "catalog_sha256": hashlib.sha256(CATALOG_MD.read_bytes()).hexdigest(),
        "catalog_contract_count": len(authoritative),
        "counts": dict(sorted(counts.items())),
        "implementation_status_counts": dict(sorted(status_counts.items())),
        "contracts": contracts,
    }
    litmus_records = [
        build_litmus_contract(record)
        for record in contracts
        if record["mechanism"] == "LITMUS_CHAOS"
    ]
    litmus = {
        "schema_version": "eve-trade.litmus-contracts/v1",
        "litmus_core": {
            "version": "3.31.0",
            "release_tag": "litmus-core-3.31.0",
            "helm_git_commit": "9e409651ce4c2293a44de56ba46ac9ab9fe1d329",
            "chaos_charts_git_commit": "57ffd877b859a71a05821bb66ff9be76d981d7e4",
            "installed_chart": "litmus-agent",
            "installed_chart_version": "3.30.0",
            "operator_image_tag": "3.30.0",
            "runner_image_tag": "3.30.0",
        },
        "contract_count": len(litmus_records),
        "contracts": litmus_records,
    }
    return requirements, litmus


def _canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if checked-in manifests are stale")
    args = parser.parse_args()
    requirements, litmus = generate()
    outputs = {
        INFRA_ROOT / "test-requirements.json": _canonical_json(requirements),
        INFRA_ROOT / "litmus-contracts.json": _canonical_json(litmus),
    }
    stale: list[str] = []
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        else:
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path}")
    if stale:
        raise SystemExit("stale generated contract manifests: " + ", ".join(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
