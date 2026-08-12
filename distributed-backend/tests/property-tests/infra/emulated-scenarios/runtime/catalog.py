"""Read-only business-catalog and safety-criteria binding utilities.

The authoritative Markdown inputs are never rewritten.  All generated state is
derived from their exact bytes and carries deterministic digests.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parent
SCENARIO_ROOT = RUNTIME_ROOT.parent
INFRA_ROOT = SCENARIO_ROOT.parent
PROPERTY_ROOT = INFRA_ROOT.parent
CATALOG_ROOT = PROPERTY_ROOT / "tests-to-implement"
TEST_RULES_PATH = PROPERTY_ROOT / "test-rules-v1.md"
SAFETY_CRITERIA_PATH = PROPERTY_ROOT / "oracle-inference-safety-criteria.md"

LEAF_CODE_BY_SLUG = {
    "ci_cd_production_gates_evidence_release_and_provenance": "C01",
    "configuration_precedence_reload_validation_and_runtime_settings": "C02",
    "container_runtime_images_hardening_and_artifact_contents": "C03",
    "cross_language_cross_platform_determinism_and_semantic_equivalence": "C04",
    "cross_service_health_readiness_liveness_shutdown_and_generic_resource_cleanup": "C05",
    "data_retention_cleanup_and_background_maintenance": "C06",
    "database_constraints_referential_integrity_sql_safety_and_query_behavior": "C07",
    "database_migrations_schema_upgrade_backup_restore_and_failover": "C08",
    "dependency_build_security_scanning_and_reproducibility": "C09",
    "explicit_concurrency_locking_races_and_serializability": "C10",
    "fuzzing_corpus_parser_properties_and_regression_seeds": "C11",
    "gateway_downstream_handoff_queueing_shutdown_restart_and_failure_recovery": "C12",
    "generic_grpc_transport_deadlines_connections_and_error_recovery": "C13",
    "generic_retry_scheduling_deadline_budget_attempt_tracking_and_backoff": "C14",
    "idempotency_duplicate_suppression_replay_request_identity_and_deterministic_ids": "C15",
    "kubernetes_runtime_security_networking_rollouts_and_deployments": "C16",
    "market_operation_persistence_publish_and_crash_recovery": "C17",
    "market_projection_result_validation_and_replica_consistency": "C18",
    "multi_operation_trade_lifecycle_sequences_conservation_and_equivalence": "C19",
    "non_rate_limit_performance_load_fairness_capacity_and_leak_prevention": "C20",
    "non_udp_authentication_authorization_secrets_and_information_exposure": "C21",
    "observability_metrics_tracing_logging_alerts_and_slos": "C22",
    "outbox_claim_dispatch_retry_ordering_and_retention": "C23",
    "protobuf_wire_schema_compatibility_validation_and_generated_code": "C24",
    "public_error_mapping_failure_classification_and_diagnostics": "C25",
    "pubsub_nsq_delivery_ack_redelivery_and_poison_messages": "C26",
    "quilkin_udp_proxy_routing_payload_and_recovery": "C27",
    "rate_limiting_abuse_control_backpressure_and_load_shedding": "C28",
    "settlement_batch_step_audit_and_ledger_reconciliation": "C29",
    "settlement_plan_intent_operation_and_batch_validation": "C30",
    "settlement_transactions_atomicity_rollback_and_commit_boundaries": "C31",
    "terraform_cloud_platform_state_providers_and_infrastructure_policy": "C32",
    "test_catalog_test_infrastructure_and_test_quality_governance": "C33",
    "test_fixture_isolation_cleanup_synchronization_and_fault_harness_behavior": "C34",
    "tool_driven_and_cross_service_fault_injection_chaos_partitions_and_resilience": "C35",
    "trade_acceptance_partial_fills_and_buyer_settlement": "C36",
    "trade_cancellation_refunds_and_terminal_cancel_state": "C37",
    "trade_offer_creation_issue_and_seller_escrow": "C38",
    "trade_queries_pagination_and_public_read_model": "C39",
    "trade_state_quantity_escrow_and_domain_invariants": "C40",
    "udp_edge_authentication_credentials_key_rotation_and_signatures": "C41",
    "udp_edge_end_to_end_delivery_ordering_and_load": "C42",
    "udp_edge_payload_parsing_schema_validation_and_datagram_limits": "C43",
    "udp_edge_response_privacy_integrity_and_business_commit_boundaries": "C44",
    "udp_session_pool_socket_lifecycle_and_concurrency": "C45",
    "worker_processing_leases_retries_crash_recovery_and_capacity": "C46",
}

UNIVERSAL_CRITERIA = (
    ("U01_NON_TESTABLE_OR_NON_ASSERTIVE_NAME", "TEST_NAME_EXPRESSES_A_TESTABLE_BEHAVIORAL_OR_STRUCTURAL_CONTRACT"),
    ("U02_AMBIGUOUS_SUBJECT_OR_BEHAVIOR", "TEST_NAME_IDENTIFIES_THE_SUBJECT_AND_OPERATION_OR_OBSERVATION_UNAMBIGUOUSLY"),
    ("U03_AMBIGUOUS_PRECONDITION", "TEST_NAME_IDENTIFIES_THE_TRIGGER_OR_PRECONDITION_UNAMBIGUOUSLY_OR_THE_CONTRACT_IS_UNCONDITIONAL"),
    ("U04_AMBIGUOUS_REQUIRED_RELATION", "TEST_NAME_IDENTIFIES_THE_REQUIRED_OUTCOME_OR_PASS_FAIL_RELATION_UNAMBIGUOUSLY"),
    ("U05_AMBIGUOUS_SCOPE_OR_QUANTIFICATION", "TEST_NAME_IDENTIFIES_SCOPE_QUANTIFIERS_CARDINALITY_AND_IDENTITY_SEMANTICS_UNAMBIGUOUSLY_WHEN_THEY_AFFECT_PASS_FAIL"),
    ("U06_AMBIGUOUS_REFERENCE", "ALL_MATERIAL_REFERENTS_EXTERNAL_POLICIES_CONFIGURATIONS_STANDARDS_AND_NAMED_VALUES_ARE_UNIQUELY_IDENTIFIED"),
    ("U07_AMBIGUOUS_BOOLEAN_STRUCTURE", "BOOLEAN_OPERANDS_OPERATORS_GROUPING_AND_PRECEDENCE_REQUIRED_BY_THE_NAME_ARE_UNAMBIGUOUS"),
    ("U08_MISSING_SEMANTICS", "NO_MATERIAL_SEMANTIC_INFORMATION_REQUIRED_TO_CONSTRUCT_THE_PASS_FAIL_PREDICATE_IS_UNSTATED"),
    ("U09_MULTIPLE_REASONABLE_ORACLES", "NO_TWO_MATERIALLY_DIFFERENT_PASS_FAIL_PREDICATES_ARE_BOTH_REASONABLE_INTERPRETATIONS_OF_THE_NAME"),
)


@dataclass(frozen=True)
class CatalogEntry:
    name: str
    leaf_category: str
    leaf_title: str
    leaf_code: str
    source_path: str


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def catalog_files() -> list[Path]:
    files = sorted(
        path
        for path in CATALOG_ROOT.rglob("*.md")
        if path.name != "test-rules.md"
    )
    if len(files) != 46:
        raise ValueError(f"expected 46 business leaf files, found {len(files)}")
    return files


def source_catalog_digest() -> str:
    """Hash sorted POSIX relative paths and exact file bytes with NUL framing."""
    digest = hashlib.sha256()
    for path in catalog_files():
        relative = path.relative_to(CATALOG_ROOT).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def source_tree_digest() -> str:
    """Hash the complete tests-to-implement Markdown tree, including its rules copy."""
    digest = hashlib.sha256()
    for path in sorted(CATALOG_ROOT.rglob("*.md")):
        relative = path.relative_to(CATALOG_ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_catalog() -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    for path in catalog_files():
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        count_match = re.search(r"\*\*Test count:\*\*\s+(\d+)", text)
        if title_match is None or count_match is None:
            raise ValueError(f"catalog leaf lacks title/count: {path}")
        names = re.findall(r"^- `([a-z0-9_]+)`$", text, re.MULTILINE)
        declared_count = int(count_match.group(1))
        if len(names) != declared_count:
            raise ValueError(
                f"catalog count mismatch in {path}: declared={declared_count}, actual={len(names)}"
            )
        slug = path.stem
        try:
            leaf_code = LEAF_CODE_BY_SLUG[slug]
        except KeyError as exc:
            raise ValueError(f"catalog leaf has no safety-criteria branch: {slug}") from exc
        relative = path.relative_to(PROPERTY_ROOT).as_posix()
        category = path.relative_to(CATALOG_ROOT).parent.as_posix()
        entries.extend(
            CatalogEntry(
                name=name,
                leaf_category=f"{category}/{slug}",
                leaf_title=title_match.group(1).strip(),
                leaf_code=leaf_code,
                source_path=relative,
            )
            for name in names
        )
    names = [entry.name for entry in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"business catalog contains duplicate names: {duplicates}")
    if len(entries) != 1484:
        raise ValueError(f"expected 1484 original business names, found {len(entries)}")
    return entries


def category_criteria() -> dict[str, list[tuple[str, str]]]:
    text = SAFETY_CRITERIA_PATH.read_text(encoding="utf-8")
    result: dict[str, list[tuple[str, str]]] = {code: [] for code in LEAF_CODE_BY_SLUG.values()}
    pattern = re.compile(r"([A-Z][A-Z0-9_]+)\s+\[FALSE_REASON=(C\d{2}_\d{2})\]")
    for criterion_text, reason in pattern.findall(text):
        result[reason[:3]].append((reason, criterion_text))
    missing = sorted(code for code, criteria in result.items() if not criteria)
    if missing:
        raise ValueError(f"safety criteria lacks leaf branches: {missing}")
    if sum(map(len, result.values())) != 229:
        raise ValueError("expected exactly 229 category-specific safety criteria")
    return result


def input_digests() -> dict[str, object]:
    files = catalog_files()
    return {
        "test_rules_sha256": sha256_file(TEST_RULES_PATH),
        "safety_criteria_sha256": sha256_file(SAFETY_CRITERIA_PATH),
        "source_catalog_sha256": source_catalog_digest(),
        "source_tree_sha256": source_tree_digest(),
        "source_catalog_digest_algorithm": (
            "SHA-256 over each sorted UTF-8 POSIX path relative to tests-to-implement, "
            "NUL, exact file bytes, NUL; test-rules.md excluded"
        ),
        "source_tree_digest_algorithm": (
            "SHA-256 over each sorted UTF-8 POSIX path relative to tests-to-implement, "
            "NUL, exact file bytes, NUL; all Markdown files included"
        ),
        "source_catalog_files": [
            {
                "path": path.relative_to(PROPERTY_ROOT).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in files
        ],
    }
