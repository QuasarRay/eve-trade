from __future__ import annotations

"""Contract-specific evidence specifications for environment-dependent properties.

The first implementation delegated hundreds of properties to a driver that could
return only ``{"ok": true}``.  That made the driver a trust oracle.  Version 2
requires raw observations and evaluates the property in Python.

The vocabulary is deliberately small and stable so Litmus/Kubernetes/Postgres/
NSQ/cloud drivers can collect observations without reimplementing assertion
logic.  Each spec is derived from the *exact* catalog name and contains concrete
predicates over typed observation fields.
"""

from dataclasses import dataclass
from typing import Any, Iterable
import re


@dataclass(frozen=True)
class Predicate:
    path: str
    op: str
    expected: Any = None
    other_path: str | None = None


@dataclass(frozen=True)
class EvidenceSpec:
    name: str
    category: int
    predicates: tuple[Predicate, ...]
    required_sections: tuple[str, ...] = ("scenario", "outcome")


def _p(path: str, op: str, expected: Any = None, other: str | None = None) -> Predicate:
    return Predicate(path, op, expected, other)


def _metric_path(name: str) -> str:
    # Most-specific first.
    mapping = [
        ("total_item", "state.total_items"),
        ("total_items", "state.total_items"),
        ("item_quantity", "state.item_quantity"),
        ("item_stack_quantity", "state.item_stack_quantity"),
        ("escrow_quantity", "state.escrow_quantity"),
        ("remaining_quantity", "state.remaining_quantity"),
        ("available_quantity", "state.available_quantity"),
        ("total_isk", "state.total_isk"),
        ("wallet_balance", "state.wallet_balance"),
        ("isk_amount", "state.isk_amount"),
        ("outbox", "counts.outbox"),
        ("ledger", "counts.ledger"),
        ("trade_state_change", "counts.trade_state_change"),
        ("settlement_step", "counts.settlement_step"),
        ("settlement_batch", "counts.settlement_batch"),
        ("processing_operation", "counts.processing_operation"),
        ("stale_processing", "counts.stale_processing"),
        ("message", "counts.messages"),
        ("trade", "counts.trades"),
        ("wallet", "counts.wallets"),
    ]
    for token, path in mapping:
        if token in name:
            return path
    return "state.value"


def _state_subject(name: str) -> str:
    for token in (
        "trade_state", "remaining_quantity", "available_quantity", "wallet_balance",
        "item_stack_quantity", "item_quantity", "escrow_quantity", "schema_version",
        "request_fingerprint", "idempotency_key", "interaction_id", "message_id",
        "correlation_id", "trade_version", "lease_expiration", "retry_delay",
    ):
        if token in name:
            return token
    return "value"


def _fault_kind(name: str) -> str | None:
    mapping = (
        ("network_partition", "network_partition"),
        ("packet_loss", "packet_loss"),
        ("network_loss", "network_loss"),
        ("network_delay", "network_delay"),
        ("primary_failover", "database_failover"),
        ("failover", "failover"),
        ("postgres_restart", "postgres_restart"),
        ("nsq_restart", "nsq_restart"),
        ("broker_restart", "broker_restart"),
        ("pod_deletion", "pod_deletion"),
        ("pod_delete", "pod_deletion"),
        ("service_kill", "process_kill"),
        ("sigkill", "sigkill"),
        ("crash", "process_crash"),
        ("disk_full", "disk_full"),
        ("memory_pressure", "memory_pressure"),
        ("cpu_saturation", "cpu_saturation"),
        ("connection_loss", "connection_loss"),
        ("half_open", "half_open_connection"),
        ("timeout", "timeout"),
        ("deadlock", "database_deadlock"),
        ("serialization_failure", "serialization_failure"),
    )
    for token, kind in mapping:
        if token in name:
            return kind
    return None


def build_evidence_spec(category: int, name: str) -> EvidenceSpec:
    """Compile a contract name into independently checked raw-observation predicates.

    The compiler intentionally rejects unsupported semantics rather than falling
    back to a single generic success boolean.  This means a new catalog phrase
    must gain an assertion rule before an external driver can make it green.
    """
    ps: list[Predicate] = [
        _p("scenario.action_executed", "eq", True),
        _p("scenario.preconditions_satisfied", "eq", True),
        _p("scenario.generated_case_applied", "eq", True),
    ]
    sections = {"scenario", "outcome"}

    # Fault/recovery proof.  This is independent of the final postcondition.
    fk = _fault_kind(name)
    if fk:
        sections.add("fault")
        ps.extend([
            _p("fault.injected", "eq", True),
            _p("fault.kind", "eq", fk),
            _p("fault.active_during_target_window", "eq", True),
        ])
        if any(t in name for t in ("recover", "recovery", "restart", "after_", "drains_backlog", "resume")):
            sections.add("recovery")
            ps.append(_p("recovery.completed", "eq", True))

    # Core outcome semantics.  Rejection properties must prove an otherwise-valid
    # control and isolate the named invalid dimension; merely observing an error
    # is not sufficient because an unrelated validation failure can false-green.
    rejection_contract = any(t in name for t in (
        "_rejects_", "_fails_", "_is_rejected", "_rejection_",
        "_cannot_be_used", "_cannot_execute", "_cannot_create", "_cannot_write",
        "_cannot_read", "_cannot_mutate", "_cannot_set_", "_cannot_grant_",
        "_cannot_change_",
    ))
    if rejection_contract:
        ps.extend([
            _p("scenario.control_case_valid", "eq", True),
            _p("scenario.named_invalid_condition_present", "eq", True),
            _p("scenario.only_named_dimension_differs", "eq", True),
            _p("outcome.accepted", "eq", False),
        ])
    if any(t in name for t in ("_accepts_", "_succeeds_", "_returns_success", "_is_successful")) and "does_not" not in name:
        ps.append(_p("outcome.accepted", "eq", True))
    if "no_false_success" in name or "does_not_return_success" in name or "returns_no_false_business_success" in name:
        ps.append(_p("outcome.business_success", "eq", False))

    # Exactly-once / cardinality properties.
    if any(t in name for t in (
        "exactly_once", "one_business_effect", "one_durable_business_effect",
        "does_not_duplicate_business_effect", "does_not_duplicate_settlement",
        "without_second_settlement", "execute_business_operation_exactly_once",
    )):
        sections.add("effects")
        ps.append(_p("effects.business_effect_count", "eq", 1))
    if "does_not_duplicate_trade" in name:
        sections.add("effects"); ps.append(_p("effects.trade_create_count", "eq", 1))
    if "does_not_duplicate_item_escrow" in name:
        sections.add("effects"); ps.append(_p("effects.item_escrow_create_count", "eq", 1))
    if "does_not_transfer_items_twice" in name:
        sections.add("effects"); ps.append(_p("effects.item_transfer_count", "eq", 1))
    if "does_not_transfer_isk_twice" in name:
        sections.add("effects"); ps.append(_p("effects.isk_transfer_count", "eq", 1))
    if "does_not_refund_items_twice" in name:
        sections.add("effects"); ps.append(_p("effects.item_refund_count", "eq", 1))
    if "exactly_one_winner" in name or "exactly_one_terminal_outcome" in name or "have_exactly_one_winner" in name:
        sections.add("effects")
        ps.extend([_p("effects.success_count", "eq", 1), _p("effects.terminal_effect_count", "eq", 1)])
    if "exactly_one_corresponding_ledger_entry" in name:
        sections.add("effects"); ps.append(_p("effects.ledger_entries_per_mutation_all_equal_one", "eq", True))
    if "exactly_once_for_completion" in name or "exactly_once_for_cancellation" in name:
        sections.add("effects"); ps.append(_p("effects.trade_state_change_delta", "eq", 1))

    # Atomicity / rollback / partial-state detection.
    if any(t in name for t in ("atomically_", "atomic_", "same_transaction", "rolls_back", "rollback", "no_partial", "partial_commit")):
        sections.update({"state", "effects"})
        ps.append(_p("effects.partial_state_observed", "eq", False))
    if "rolls_back" in name or "rollback" in name or "failed_" in name and "does_not" in name:
        ps.append(_p("state.business_state_changed", "eq", False))

    # Conservation / unchanged relations.
    if "preserves_total_items" in name or "total_item_quantity_is_preserved" in name or "global_item_conservation" in name:
        sections.add("state")
        ps.append(_p("state.before.total_items", "eq_path", other="state.after.total_items"))
    if "preserves_total_isk" in name or "total_isk_amount_is_preserved" in name or "global_isk_conservation" in name:
        sections.add("state")
        ps.append(_p("state.before.total_isk", "eq_path", other="state.after.total_isk"))
    if "sum_of_item" in name and "conserved" in name:
        sections.add("state"); ps.append(_p("state.before.total_items", "eq_path", other="state.after.total_items"))
    if "sum_of_wallet" in name and "conserved" in name:
        sections.add("state"); ps.append(_p("state.before.total_isk", "eq_path", other="state.after.total_isk"))
    if any(t in name for t in ("does_not_change", "unchanged", "preserves_same", "remains_unchanged")):
        sections.add("state")
        subject = _state_subject(name)
        ps.append(_p(f"state.before.{subject}", "eq_path", other=f"state.after.{subject}"))

    # Numeric/state boundary semantics.
    if "never_becomes_negative" in name or "cannot_make" in name and "negative" in name or "never_creates_negative" in name:
        sections.add("state")
        ps.append(_p("state.minimum_observed_value", "ge", 0))
    if "zero_available_quantity" in name or "zero_remaining_quantity" in name:
        sections.add("state"); ps.append(_p("state.after.remaining_quantity", "eq", 0))
    if "zero_item_escrow" in name:
        sections.add("state"); ps.append(_p("state.after.item_escrow_quantity", "eq", 0))
    if "leaves_zero" in name:
        sections.add("state"); ps.append(_p("state.after.value", "eq", 0))
    if "one_byte_above_maximum_packet_size" in name:
        sections.add("transport")
        ps.extend([_p("transport.sent_size", "eq_path", other="transport.maximum_datagram_size_plus_one"), _p("outcome.accepted", "eq", False)])
    if "exactly_at_maximum_packet_size" in name:
        sections.add("transport")
        ps.extend([_p("transport.sent_size", "eq_path", other="transport.maximum_datagram_size"), _p("outcome.accepted", "eq", True)])
    if "remains_within_single_datagram_size_limit" in name:
        sections.add("transport"); ps.append(_p("transport.response_size", "le_path", other="transport.maximum_datagram_size"))

    # Replay / fingerprint / identity.
    if "replay" in name or "interaction_id" in name or "idempotency" in name:
        sections.add("identity")
    if "replay_conflict" in name or "same_interaction_id_with_different" in name or "reuse_across" in name:
        ps.append(_p("outcome.error_code", "eq", "replay_conflict"))
    if "scoped_by_authenticated_principal" in name or "different_principals_cannot_reuse" in name:
        ps.extend([
            _p("identity.first_request_valid", "eq", True),
            _p("identity.second_request_valid_for_second_principal", "eq", True),
            _p("identity.cache_cross_principal_hit", "eq", False),
        ])
    if "fingerprint_is_identical" in name or "same_request_fingerprint" in name:
        ps.append(_p("identity.fingerprint_first", "eq_path", other="identity.fingerprint_second"))
    if "fingerprint_changes_when" in name:
        ps.append(_p("identity.fingerprint_first", "ne_path", other="identity.fingerprint_second"))
    if "fingerprint_is_stable" in name:
        ps.append(_p("identity.fingerprint_first", "eq_path", other="identity.fingerprint_second"))
    if "does_not_include" in name and "fingerprint" in name:
        ps.append(_p("identity.fingerprint_unchanged_after_nonsemantic_field_change", "eq", True))
    if "fingerprint_includes" in name or "includes_" in name and "fingerprint" in name:
        ps.append(_p("identity.fingerprint_changed_after_named_semantic_field_change", "eq", True))

    # Concurrency/race proof requires actual overlap, not sequential calls.
    if any(t in name for t in ("concurrent", "race", "racing")):
        sections.add("concurrency")
        ps.extend([
            _p("concurrency.contenders", "ge", 2),
            _p("concurrency.overlap_at_target_boundary", "eq", True),
        ])

    # Retry / attempt semantics.
    if "retry" in name or "redelivery" in name or "attempt" in name:
        sections.add("retry")
    if "same_idempotency_key" in name or "preserves_same_idempotency_key" in name:
        ps.append(_p("retry.same_idempotency_key_every_attempt", "eq", True))
    if "never_retried" in name or "is_never_retried" in name:
        ps.append(_p("retry.attempt_count", "eq", 1))
    if "attempt_count_never_exceeds" in name:
        ps.append(_p("retry.attempt_count", "le_path", other="retry.configured_maximum"))
    if "attempt_number_monotonically_increasing_from_one" in name:
        ps.append(_p("retry.attempt_numbers_start_at_one_and_are_contiguous", "eq", True))
    if "retry_delay" in name and "maximum" in name:
        ps.append(_p("retry.observed_delay_ms", "le_path", other="retry.configured_maximum_delay_ms"))
    if "retry_delay" in name and "minimum" in name:
        ps.append(_p("retry.observed_delay_ms", "ge_path", other="retry.configured_minimum_delay_ms"))

    # Message ack/redelivery semantics.
    if "message_ack" in name or "acked" in name:
        sections.add("messaging")
    if "ack_is_sent_only_after" in name:
        ps.append(_p("messaging.ack_time", "gt_path", other="messaging.durable_commit_time"))
    if "not_acked_as_success" in name:
        ps.append(_p("messaging.acked_as_success", "eq", False))
    if "redeliverable" in name:
        ps.append(_p("messaging.redeliverable", "eq", True))
    if "duplicate_delivery" in name or "duplicate_redelivery" in name:
        ps.append(_p("messaging.duplicate_delivery_observed", "eq", True))

    # Security and authorization.
    if any(t in name for t in ("authentication", "authorization", "principal", "hmac", "signature", "credential", "key_rotation", "privilege")):
        sections.add("security")
    if "does_not_leak" in name or "absent_from" in name or "does_not_include" in name and "error" in name:
        ps.append(_p("security.sensitive_data_exposed", "eq", False))
    if "constant_time_comparison" in name:
        ps.append(_p("security.constant_time_primitive_verified", "eq", True))
    if "cannot_impersonate" in name or "cross_principal" in name and "reject" in name:
        ps.append(_p("security.cross_principal_action_accepted", "eq", False))

    # Configuration/repository/deployment semantics returned by executable probes.
    if any(t in name for t in ("kubernetes", "terraform", "manifest", "workflow", "container", "image", "provider", "network_policy", "service_account", "security_group", "firewall", "sbom", "provenance", "trivy", "audit", "buf_", "proto", "dependency", "requirements")):
        sections.add("configuration")
    if "same_" in name and any(t in name for t in ("image_digest", "schema_version", "topic", "channel", "resource_requests", "security_context", "probe")):
        ps.append(_p("configuration.left", "eq_path", other="configuration.right"))
    if "only_" in name and "privilege" in name:
        ps.append(_p("security.unexpected_privileges", "eq", []))
    if "no_wildcard" in name or "rejects_wildcard" in name:
        ps.append(_p("security.wildcard_permission_present", "eq", False))
    if "non_root" in name:
        ps.append(_p("security.runs_as_root", "eq", False))
    if "disallows_privilege_escalation" in name:
        ps.append(_p("security.allow_privilege_escalation", "eq", False))
    if "drops_all_linux_capabilities" in name:
        ps.append(_p("security.unexpected_linux_capabilities", "eq", []))
    if "not_publicly_exposed" in name or "cluster_internal" in name:
        ps.append(_p("security.publicly_exposed", "eq", False))

    # Test/CI traceability semantics.
    if "every_" in name and "test_reference" in name:
        sections.add("coverage")
        ps.extend([_p("coverage.authoritative_subject_count", "gt", 0), _p("coverage.uncovered_subjects", "eq", [])])
    if "every_" in name and any(t in name for t in ("requirements_file", "terraform_root", "production_overlay", "provider_lockfile")):
        sections.add("coverage"); ps.append(_p("coverage.uncovered_subjects", "eq", []))
    if "collects_zero_tests" in name or "matches_zero_tests" in name:
        sections.add("coverage"); ps.append(_p("coverage.collected_test_count", "gt", 0))

    # Ordering/monotonicity.
    if "monotonic" in name or "never_decreases" in name:
        sections.add("ordering"); ps.append(_p("ordering.monotonic_non_decreasing", "eq", True))
    if "preserves_per_trade_terminal_event_order" in name or "without_reordering" in name:
        sections.add("ordering"); ps.append(_p("ordering.per_subject_order_preserved", "eq", True))
    if "contiguous" in name and "start_at_one" in name:
        sections.add("ordering"); ps.append(_p("ordering.values_start_at_one_and_are_contiguous", "eq", True))

    # Timing/expiration.
    if "expires_at" in name or "expiration" in name:
        sections.add("time")
    if "equal_to_trade_expires_at" in name or "after_trade_expires_at" in name:
        ps.extend([_p("time.database_now_ge_expiration", "eq", True), _p("outcome.accepted", "eq", False)])
    if "one_microsecond_before" in name:
        ps.extend([_p("time.database_now_before_expiration_us", "eq", 1), _p("outcome.accepted", "eq", True)])
    if "uses_database_time" in name:
        ps.append(_p("time.decision_clock", "eq", "database"))

    # Record semantic predicate count *before* adding anti-vacuity/setup proof.
    # Setup evidence must never suppress the actual named postcondition fallback.
    semantic_count = len(ps) - 3

    # Cross-cutting anti-vacuity evidence.  Negative-effect contracts prove the
    # named action/path was actually attempted; inspection/coverage contracts
    # prove the named subject set was enumerated rather than matching one token.
    if any(t in name for t in ("does_not_", "never_", "cannot_")):
        sections.add("scenario")
        ps.append(_p("scenario.named_forbidden_path_attempted", "eq", True))
    if any(t in name for t in ("contains_", "includes_", "excludes_", "absent_from", "does_not_contain", "does_not_include")):
        sections.add("inspection")
        ps.extend([
            _p("inspection.named_subjects_enumerated", "eq", True),
            _p("inspection.named_subject_count", "gt", 0),
        ])
    if name.startswith("test_every_") or name.startswith("test_all_"):
        sections.add("coverage")
        ps.extend([
            _p("coverage.authoritative_subject_count", "gt", 0),
            _p("coverage.uncovered_subjects", "eq", []),
        ])

    # Broad semantic verbs not covered by the domain-specific clauses above.
    # These still assert raw relations; none accepts a generic success boolean.
    if semantic_count == 0 and any(t in name for t in ("does_not_", "never_")):
        sections.add("effects")
        ps.append(_p("effects.forbidden_effect_observed", "eq", False))
        semantic_count += 1
    if semantic_count == 0 and "cannot_" in name:
        sections.add("outcome")
        ps.append(_p("outcome.forbidden_transition_or_action_observed", "eq", False))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("preserves_", "restores_", "unchanged")):
        sections.add("comparison")
        ps.append(_p("comparison.before", "eq_path", other="comparison.after"))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("matches_", "equals_", "same_", "identical_", "is_bound_to_exact", "records_exact")):
        sections.add("comparison")
        ps.append(_p("comparison.left", "eq_path", other="comparison.right"))
        semantic_count += 1
    if semantic_count == 0 and "different" in name:
        sections.add("comparison")
        ps.append(_p("comparison.left", "ne_path", other="comparison.right"))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("contains_", "includes_", "present", "exists", "defines_", "has_", "emits_")):
        sections.add("inspection")
        ps.append(_p("inspection.match_count", "gt", 0))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("absent", "excludes_", "does_not_contain", "does_not_include", "no_")):
        sections.add("inspection")
        ps.append(_p("inspection.match_count", "eq", 0))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("is_unique", "globally_unique", "prevents_duplicate", "uniqueness")):
        sections.add("identity")
        ps.append(_p("identity.duplicate_count", "eq", 0))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("is_append_only", "immutable")):
        sections.add("effects")
        ps.extend([_p("effects.update_count", "eq", 0), _p("effects.delete_count", "eq", 0)])
        semantic_count += 2
    if semantic_count == 0 and "idempotent" in name:
        sections.update({"effects", "comparison"})
        ps.extend([_p("effects.second_execution_business_effect_delta", "eq", 0), _p("comparison.first_result", "eq_path", other="comparison.second_result")])
        semantic_count += 2
    if semantic_count == 0 and any(t in name for t in ("recovers", "recovery", "resume", "resumes", "restores_forwarding")):
        sections.add("recovery")
        ps.append(_p("recovery.completed", "eq", True))
        semantic_count += 1
    if semantic_count == 0 and "uses_" in name:
        sections.add("comparison")
        ps.append(_p("comparison.observed", "eq_path", other="comparison.expected"))
        semantic_count += 1
    if semantic_count == 0 and "maps_to_" in name:
        sections.add("comparison")
        ps.append(_p("comparison.observed", "eq_path", other="comparison.expected"))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("increments_", "increases_")):
        sections.add("state")
        ps.append(_p("state.after.value", "gt_path", other="state.before.value"))
        semantic_count += 1
    if semantic_count == 0 and "returns_to_zero" in name:
        sections.add("state")
        ps.append(_p("state.after.value", "eq", 0))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("stays_below", "below_configured", "never_exceeds")):
        sections.add("measurement")
        ps.append(_p("measurement.observed", "le_path", other="measurement.limit"))
        semantic_count += 1
    if semantic_count == 0 and "above_" in name and "reject" in name:
        sections.add("boundary")
        ps.extend([_p("boundary.input", "gt_path", other="boundary.maximum"), _p("outcome.accepted", "eq", False)])
        semantic_count += 2
    if semantic_count == 0 and "at_maximum" in name:
        sections.add("boundary")
        ps.append(_p("boundary.input", "eq_path", other="boundary.maximum"))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("only_", "every_", "all_")):
        sections.add("coverage")
        ps.extend([_p("coverage.authoritative_subject_count", "gt", 0), _p("coverage.uncovered_or_unexpected_subjects", "eq", [])])
        semantic_count += 2
    if semantic_count == 0 and any(t in name for t in ("passes_", "pass_", "applies_", "initialize", "validate")):
        sections.add("command")
        ps.append(_p("command.returncode", "eq", 0))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("no_diff", "produces_no_diff", "unchanged_after")):
        sections.add("comparison")
        ps.append(_p("comparison.diff", "eq", ""))
        semantic_count += 1
    if semantic_count == 0 and "uses_index" in name:
        sections.add("database")
        ps.append(_p("database.query_plan_uses_required_index", "eq", True))
        semantic_count += 1
    if semantic_count == 0 and "does_not_block" in name:
        sections.add("concurrency")
        ps.append(_p("concurrency.unrelated_progress_observed", "eq", True))
        semantic_count += 1
    if semantic_count == 0 and "is_readonly" in name:
        sections.add("security")
        ps.append(_p("security.write_attempt_accepted", "eq", False))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("marked_sensitive", "sensitive")):
        sections.add("security")
        ps.append(_p("security.sensitive_flag", "eq", True))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("is_not_exposed", "does_not_expose", "not_exposed")):
        sections.add("security")
        ps.append(_p("security.exposed", "eq", False))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("before_", "after_")):
        sections.add("ordering")
        ps.append(_p("ordering.named_event_order_verified", "eq", True))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("cleanly", "completes_cleanly", "exits_cleanly")):
        sections.add("process")
        ps.append(_p("process.clean_exit", "eq", True))
        semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("without_panicking", "never_panics", "without_process_panic")):
        sections.add("process")
        ps.extend([_p("process.panicked", "eq", False), _p("process.post_probe_healthy", "eq", True)])
        semantic_count += 2
    if semantic_count == 0 and any(t in name for t in ("is_stable", "stable_", "deterministic")):
        sections.add("comparison")
        ps.append(_p("comparison.first", "eq_path", other="comparison.second"))
        semantic_count += 1
    if semantic_count == 0 and "is_scoped" in name:
        sections.add("scope")
        ps.append(_p("scope.cross_scope_effect_observed", "eq", False))
        semantic_count += 1
    if semantic_count == 0 and "is_verified" in name:
        sections.add("verification")
        ps.append(_p("verification.verified", "eq", True))
        semantic_count += 1

    # Remaining domain-specific phrases.  These rules intentionally use raw
    # measurements/comparisons so the external probe cannot merely claim pass/fail.
    if semantic_count == 0 and "covers_" in name:
        sections.add("coverage"); ps.extend([_p("coverage.required_subject_count", "gt", 0), _p("coverage.uncovered_subjects", "eq", [])]); semantic_count += 2
    if semantic_count == 0 and "is_signed_by" in name:
        sections.add("security"); ps.append(_p("security.observed_signing_key_id", "eq_path", other="security.expected_signing_key_id")); semantic_count += 1
    if semantic_count == 0 and "keeps_last_known_good" in name:
        sections.add("comparison"); ps.append(_p("comparison.before", "eq_path", other="comparison.after")); semantic_count += 1
    if semantic_count == 0 and "subject_to_pre_auth_abuse_limit" in name:
        sections.add("rate_limit"); ps.append(_p("rate_limit.applied", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "boundary_allows_exact_capacity" in name:
        sections.add("rate_limit"); ps.append(_p("rate_limit.accepted_count", "eq_path", other="rate_limit.capacity")); semantic_count += 1
    if semantic_count == 0 and "high_cardinality" in name and "memory_growth" in name:
        sections.add("measurement"); ps.append(_p("measurement.memory_growth_bytes", "le_path", other="measurement.configured_maximum_growth_bytes")); semantic_count += 1
    if semantic_count == 0 and "counts_replayed_requests_according_to_documented_policy" in name:
        sections.add("rate_limit"); ps.append(_p("rate_limit.observed_replay_charge", "eq_path", other="rate_limit.documented_replay_charge")); semantic_count += 1
    if semantic_count == 0 and "rebinds_udp_socket_without_leaking_previous_listener" in name:
        sections.add("process"); ps.append(_p("process.listeners_on_target_udp_port", "eq", 1)); semantic_count += 1
    if semantic_count == 0 and "releases_worker_slot" in name:
        sections.add("resource"); ps.append(_p("resource.worker_slots_after", "eq_path", other="resource.worker_slots_before")); semantic_count += 1
    if semantic_count == 0 and "leaves_source_stack_quantity_zero_without_negative_quantity_or_orphaned_escrow" in name:
        sections.add("state"); ps.extend([_p("state.after.source_stack_quantity", "eq", 0), _p("state.minimum_observed_item_quantity", "ge", 0), _p("state.orphaned_escrow_count", "eq", 0)]); semantic_count += 3
    if semantic_count == 0 and "in_progress_idempotency_record_is_not_interpreted_as_success" in name:
        sections.add("state"); ps.append(_p("state.interpreted_as_success", "eq", False)); semantic_count += 1
    if semantic_count == 0 and ("references_existing_" in name or "reference_existing_" in name):
        sections.add("database"); ps.append(_p("database.dangling_reference_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "harmless_to_market_projection" in name:
        sections.add("comparison"); ps.append(_p("comparison.projection_before_duplicate", "eq_path", other="comparison.projection_after_duplicate")); semantic_count += 1
    if semantic_count == 0 and "executes_business_settlement_once" in name:
        sections.add("effects"); ps.append(_p("effects.settlement_execution_count", "eq", 1)); semantic_count += 1
    if semantic_count == 0 and "changes_projection_once" in name:
        sections.add("effects"); ps.append(_p("effects.projection_change_count", "eq", 1)); semantic_count += 1
    if semantic_count == 0 and "is_quarantined_without_mutating" in name:
        sections.update({"messaging","state"}); ps.extend([_p("messaging.quarantined", "eq", True), _p("state.business_state_changed", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "moves_poison_message_to_documented_terminal_handling_path" in name:
        sections.add("messaging"); ps.extend([_p("messaging.retry_limit_reached", "eq", True), _p("messaging.terminal_handling_observed", "eq", True)]); semantic_count += 2
    if semantic_count == 0 and "message_nack_is_sent" in name:
        sections.add("messaging"); ps.append(_p("messaging.nack_sent", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "without_semantic_change" in name:
        sections.add("comparison"); ps.append(_p("comparison.semantic_value_before", "eq_path", other="comparison.semantic_value_after")); semantic_count += 1
    if semantic_count == 0 and "do_not_change_settlement_fingerprint" in name:
        sections.add("identity"); ps.append(_p("identity.fingerprint_before", "eq_path", other="identity.fingerprint_after")); semantic_count += 1
    if semantic_count == 0 and "not_silently_reinterpreted_as_missing" in name:
        sections.add("comparison"); ps.append(_p("comparison.zero_value_semantics", "ne_path", other="comparison.missing_value_semantics")); semantic_count += 1
    if semantic_count == 0 and "wakes_waiting_borrowers" in name:
        sections.add("concurrency"); ps.append(_p("concurrency.waiting_borrowers_woken", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "forwards_authenticated_udp_packet_to_gateway" in name:
        sections.add("transport"); ps.extend([_p("transport.gateway_received_datagram", "eq", True), _p("transport.gateway_received_payload_sha256", "eq_path", other="transport.sent_payload_sha256")]); semantic_count += 2
    if semantic_count == 0 and "multiple_gateway_endpoints_do_not_break_replay_semantics" in name:
        sections.add("effects"); ps.append(_p("effects.business_effect_count", "eq", 1)); semantic_count += 1
    if semantic_count == 0 and "source_address_changes_do_not_change_authenticated_principal_identity" in name:
        sections.add("identity"); ps.append(_p("identity.principal_before", "eq_path", other="identity.principal_after")); semantic_count += 1
    if semantic_count == 0 and "not_used_as_metric_label_values" in name:
        sections.add("metrics"); ps.append(_p("metrics.forbidden_high_cardinality_label_values", "eq", [])); semantic_count += 1
    if semantic_count == 0 and "bounded_documented_error_code_cardinality" in name:
        sections.add("metrics"); ps.append(_p("metrics.observed_error_code_cardinality", "le_path", other="metrics.documented_error_code_cardinality")); semantic_count += 1
    if semantic_count == 0 and "configures_readiness_probe" in name:
        sections.add("configuration"); ps.append(_p("configuration.readiness_probe_present_and_targets_expected_endpoint", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "configures_resource_requests_and_limits" in name:
        sections.add("configuration"); ps.extend([_p("configuration.cpu_request_present", "eq", True), _p("configuration.memory_request_present", "eq", True), _p("configuration.cpu_limit_present", "eq", True), _p("configuration.memory_limit_present", "eq", True)]); semantic_count += 4
    if semantic_count == 0 and "network_policy_allows_worker_to_settlement_and_denies_unrelated_ingress" in name:
        sections.add("security"); ps.extend([_p("security.worker_to_settlement_allowed", "eq", True), _p("security.unrelated_ingress_allowed", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "database_credentials_use_runtime_role_not_migration_role" in name:
        sections.add("security"); ps.extend([_p("security.observed_database_role", "eq_path", other="security.expected_runtime_role"), _p("security.observed_database_role", "ne_path", other="security.migration_role")]); semantic_count += 2
    if semantic_count == 0 and "backward_compatible_with_previous_deployed_binary" in name:
        sections.add("compatibility"); ps.append(_p("compatibility.previous_binary_passed_smoke_after_migration", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "memory_remains_bounded" in name:
        sections.add("measurement"); ps.append(_p("measurement.peak_memory_bytes", "le_path", other="measurement.configured_memory_bound_bytes")); semantic_count += 1
    if semantic_count == 0 and "targets_running_commit_sha" in name:
        sections.add("comparison"); ps.append(_p("comparison.e2e_target_commit_sha", "eq_path", other="comparison.running_commit_sha")); semantic_count += 1
    if semantic_count == 0 and "required_uuid_fields_reject" in name:
        sections.add("boundary"); ps.extend([_p("boundary.input_is_nil_uuid", "eq", True), _p("outcome.accepted", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "uuid_fields_reject_noncanonical_text" in name:
        sections.add("boundary"); ps.extend([_p("boundary.input_is_noncanonical_uuid_text", "eq", True), _p("outcome.accepted", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "retry_deadline_budget_decreases_across_attempts" in name:
        sections.add("retry"); ps.append(_p("retry.deadline_budget_strictly_decreases_each_attempt", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "retry_jitter_delay_stays_between" in name:
        sections.add("retry"); ps.extend([_p("retry.minimum_observed_delay_ms", "ge_path", other="retry.configured_minimum_jitter_ms"), _p("retry.maximum_observed_delay_ms", "le_path", other="retry.configured_maximum_jitter_ms")]); semantic_count += 2
    if semantic_count == 0 and "dns_resolution_failure" in name:
        sections.update({"retry","state"}); ps.extend([_p("retry.classified_retryable", "eq", True), _p("state.work_lost_count", "eq", 0)]); semantic_count += 2
    if semantic_count == 0 and "dns_target_change_is_observed" in name:
        sections.update({"network","process"}); ps.extend([_p("network.new_target_observed", "eq", True), _p("process.restart_count", "eq", 0)]); semantic_count += 2
    if semantic_count == 0 and "connection_is_reused_without_cross_request_state_leakage" in name:
        sections.update({"resource","state"}); ps.extend([_p("resource.connection_reused", "eq", True), _p("state.cross_request_leak_observed", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "do_not_leak_goroutines" in name:
        sections.add("resource"); ps.append(_p("resource.goroutines_after", "le_path", other="resource.goroutines_before_plus_tolerance")); semantic_count += 1
    if semantic_count == 0 and "do_not_leak_database_connections" in name:
        sections.add("resource"); ps.append(_p("resource.database_connections_after", "le_path", other="resource.database_connections_before_plus_tolerance")); semantic_count += 1
    if semantic_count == 0 and "discards_connections_to_demoted_primary" in name:
        sections.add("resource"); ps.append(_p("resource.connections_to_demoted_primary_after_failover", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "converge_to_highest_committed_trade_version" in name:
        sections.add("state"); ps.append(_p("state.projected_trade_version", "eq_path", other="state.highest_committed_trade_version")); semantic_count += 1
    if semantic_count == 0 and "redelivery_count_remains_observable" in name:
        sections.add("messaging"); ps.append(_p("messaging.redelivery_count_after_restart", "ge_path", other="messaging.redelivery_count_before_restart")); semantic_count += 1
    if semantic_count == 0 and "correlation_id_is_preserved" in name:
        sections.add("identity"); ps.append(_p("identity.correlation_id_before", "eq_path", other="identity.correlation_id_after")); semantic_count += 1
    if semantic_count == 0 and "return_errors_that_do_not_leak" in name:
        sections.add("security"); ps.extend([_p("security.unknown_resource_error_class", "eq_path", other="security.forbidden_resource_error_class"), _p("security.resource_existence_leaked", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "identifies_invalid_public_field_without_echoing_secret_fields" in name:
        sections.add("security"); ps.extend([_p("security.invalid_public_field_identified", "eq", True), _p("security.secret_field_echoed", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "do_not_construct_table_or_column_names_from_untrusted_request_fields" in name:
        sections.add("security"); ps.append(_p("security.untrusted_input_used_as_sql_identifier", "eq", False)); semantic_count += 1
    if semantic_count == 0 and "search_path_is_fixed" in name:
        sections.add("database"); ps.append(_p("database.search_path", "eq_path", other="database.expected_search_path")); semantic_count += 1
    if semantic_count == 0 and "invalid_configuration_reload_keeps_last_known_good_configuration" in name:
        sections.add("comparison"); ps.append(_p("comparison.configuration_before", "eq_path", other="comparison.configuration_after_failed_reload")); semantic_count += 1
    if semantic_count == 0 and "liveness_endpoint_stays_successful" in name:
        sections.add("health"); ps.append(_p("health.liveness_success", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "preserve_atomicity" in name:
        sections.add("effects"); ps.append(_p("effects.partial_state_observed", "eq", False)); semantic_count += 1
    if semantic_count == 0 and "ignores_result_with_trade_version_lower" in name:
        sections.add("state"); ps.append(_p("state.projected_trade_version_after", "eq_path", other="state.projected_trade_version_before")); semantic_count += 1
    if semantic_count == 0 and "detects_missing_intermediate_trade_version" in name:
        sections.add("state"); ps.append(_p("state.version_gap_detected", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "schema_version_bump_requires_compatibility_test_fixture" in name:
        sections.add("coverage"); ps.append(_p("coverage.compatibility_fixture_count_for_version_bump", "gt", 0)); semantic_count += 1
    if semantic_count == 0 and "do_not_depend_on_unordered_database_row_return_order" in name:
        sections.add("comparison"); ps.append(_p("comparison.outcome_normal_order", "eq_path", other="comparison.outcome_reversed_row_order")); semantic_count += 1
    if semantic_count == 0 and "do_not_depend_on_goroutine_or_thread_scheduling_fairness" in name:
        sections.add("comparison"); ps.append(_p("comparison.outcome_schedule_a", "eq_path", other="comparison.outcome_schedule_b")); semantic_count += 1
    if semantic_count == 0 and "without_expires_at_persists_null_expiration" in name:
        sections.add("state"); ps.append(_p("state.after.expires_at", "eq", None)); semantic_count += 1
    if semantic_count == 0 and "is_lowercase_hyphenated_uuid" in name:
        sections.add("identity"); ps.append(_p("identity.generated_value_matches_lowercase_hyphenated_uuid_regex", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "_plan_" in name:
        sections.add("plan")
        if "intent_is_" in name: ps.append(_p("plan.intent", "eq_path", other="plan.expected_intent"))
        elif "caused_by_capsuleer_id_is_" in name: ps.append(_p("plan.caused_by_capsuleer_id", "eq_path", other="plan.expected_caused_by_capsuleer_id"))
        elif "created_by_service_is_" in name: ps.append(_p("plan.created_by_service", "eq_path", other="plan.expected_created_by_service"))
        elif "moves_exact" in name or "returns_exact" in name: ps.append(_p("plan.observed_amount", "eq_path", other="plan.expected_amount"))
        elif "sets_new_" in name: ps.append(_p("plan.observed_owner", "eq_path", other="plan.expected_owner"))
        elif "omits_" in name: ps.append(_p("plan.named_operation_count", "eq", 0))
        else: ps.append(_p("plan.matches_expected_operation_sequence", "eq", True))
        semantic_count += 1
    if semantic_count == 0 and "credits_each_buyer_exact_item_quantity" in name:
        sections.add("state"); ps.append(_p("state.all_buyer_item_deltas_match_requested_quantities", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "credits_seller_sum_of_both_payments" in name:
        sections.add("state"); ps.append(_p("state.seller_credit_delta", "eq_path", other="state.expected_total_payment")); semantic_count += 1
    if semantic_count == 0 and "leave_trade_open_with_exact_remainder" in name:
        sections.add("state"); ps.extend([_p("state.after.trade_state", "eq", "OPEN"), _p("state.after.remaining_quantity", "eq_path", other="state.expected_remaining_quantity")]); semantic_count += 2
    if semantic_count == 0 and "transitions_trade_to_completed_with_zero_escrow" in name:
        sections.add("state"); ps.extend([_p("state.after.trade_state", "eq", "COMPLETED"), _p("state.after.item_escrow_quantity", "eq", 0)]); semantic_count += 2
    if semantic_count == 0 and "execute_one_settlement" in name:
        sections.add("effects"); ps.append(_p("effects.settlement_execution_count", "eq", 1)); semantic_count += 1
    if semantic_count == 0 and "preserve_per_trade_event_order" in name:
        sections.add("ordering"); ps.append(_p("ordering.per_subject_order_preserved", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "secret_values_are_referenced_from_secret_objects" in name:
        sections.add("security"); ps.extend([_p("security.plaintext_secret_literal_count", "eq", 0), _p("security.secret_reference_count", "gt", 0)]); semantic_count += 2
    if semantic_count == 0 and "network_policy_denies_unlisted_ingress" in name:
        sections.add("security"); ps.append(_p("security.unlisted_ingress_allowed_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "security_context_is_preserved" in name:
        sections.add("comparison"); ps.append(_p("comparison.base_security_context", "eq_path", other="comparison.overlay_security_context")); semantic_count += 1
    if semantic_count == 0 and "liveness_probes_that_do_not_depend_on_optional_observability_backends" in name:
        sections.add("configuration"); ps.append(_p("configuration.liveness_probe_depends_on_optional_observability_backend", "eq", False)); semantic_count += 1
    if semantic_count == 0 and "define_cpu_and_memory_requests" in name:
        sections.add("configuration"); ps.extend([_p("configuration.all_workloads_have_cpu_request", "eq", True), _p("configuration.all_workloads_have_memory_request", "eq", True)]); semantic_count += 2
    if semantic_count == 0 and "define_cpu_and_memory_limits_or_explicitly_document_limitless_policy" in name:
        sections.add("configuration"); ps.append(_p("configuration.all_workloads_have_limits_or_documented_exception", "eq", True)); semantic_count += 1
    if semantic_count == 0 and any(t in name for t in ("encrypted_root_volumes", "encrypted_persistent_storage", "storage_encryption_is_enabled")):
        sections.add("security"); ps.append(_p("security.storage_encryption_enabled", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "do_not_expose_" in name and "_to_world" in name:
        sections.add("security"); ps.append(_p("security.world_exposure_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "deletion_protection_is_enabled" in name:
        sections.add("configuration"); ps.append(_p("configuration.deletion_protection_enabled", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "backup_retention_meets_configured_production_minimum" in name:
        sections.add("configuration"); ps.append(_p("configuration.backup_retention_days", "ge_path", other="configuration.minimum_backup_retention_days")); semantic_count += 1
    if semantic_count == 0 and "nodes_span_configured_failure_zones" in name:
        sections.add("configuration"); ps.append(_p("configuration.observed_failure_zone_count", "ge_path", other="configuration.required_failure_zone_count")); semantic_count += 1
    if semantic_count == 0 and "requires_nonempty_external_database_url" in name:
        sections.add("configuration"); ps.append(_p("configuration.external_database_url_nonempty", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "requires_nondefault_database_password" in name:
        sections.add("security"); ps.append(_p("security.database_password_is_default", "eq", False)); semantic_count += 1
    if semantic_count == 0 and "creates_persistent_volume_claim_for_postgres" in name:
        sections.add("configuration"); ps.append(_p("configuration.postgres_persistent_volume_claim_count", "gt", 0)); semantic_count += 1
    if semantic_count == 0 and "cluster_credentials_are_not_emitted_in_plaintext_ci_logs" in name:
        sections.add("security"); ps.append(_p("security.plaintext_cluster_credential_occurrence_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "provider_version_upgrade_requires_lockfile_diff" in name:
        sections.add("comparison"); ps.append(_p("comparison.lockfile_diff_after_provider_upgrade", "ne", "")); semantic_count += 1
    if semantic_count == 0 and "is_referenced_by_digest_in_production_manifest" in name:
        sections.add("configuration"); ps.append(_p("configuration.production_image_reference_is_digest_pinned", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "provenance_identifies_repository_commit" in name:
        sections.add("provenance"); ps.append(_p("provenance.commit_sha", "eq_path", other="provenance.expected_commit_sha")); semantic_count += 1
    if semantic_count == 0 and "provenance_identifies_exact_ci_workflow_identity" in name:
        sections.add("provenance"); ps.append(_p("provenance.workflow_identity", "eq_path", other="provenance.expected_workflow_identity")); semantic_count += 1
    if semantic_count == 0 and "github_actions_are_pinned_to_full_commit_sha" in name:
        sections.add("security"); ps.append(_p("security.unpinned_third_party_action_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "downloaded_build_tools_are_verified" in name:
        sections.add("security"); ps.append(_p("security.unverified_downloaded_tool_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "base_image_update_changes_locked_or_reviewable_provenance_input" in name:
        sections.add("comparison"); ps.append(_p("comparison.provenance_input_changed_after_base_image_change", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "do_not_include_private_ssh_keys" in name:
        sections.add("security"); ps.append(_p("security.private_ssh_key_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "entrypoint_exits_nonzero_when_required_configuration_is_missing" in name:
        sections.add("process"); ps.append(_p("process.exit_code", "ne", 0)); semantic_count += 1
    if semantic_count == 0 and "python_dependency_audit_covers_" in name:
        sections.add("coverage"); ps.extend([_p("coverage.required_requirements_file_count", "gt", 0), _p("coverage.unaudited_requirements_files", "eq", [])]); semantic_count += 2
    if semantic_count == 0 and "buf_breaking_detects_" in name:
        sections.add("command"); ps.extend([_p("command.fixture_mutation_applied", "eq", True), _p("command.returncode", "ne", 0)]); semantic_count += 2
    if semantic_count == 0 and "generated_" in name and "proto_sources_match_current_proto_descriptors" in name:
        sections.add("comparison"); ps.append(_p("comparison.diff_after_regeneration", "eq", "")); semantic_count += 1
    if semantic_count == 0 and "reserved_removed_" in name and "are_not_reused" in name:
        sections.add("proto"); ps.append(_p("proto.reused_reserved_number_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "new_optional_field_is_ignored_by_immediately_previous_consumer" in name:
        sections.add("compatibility"); ps.append(_p("compatibility.previous_consumer_semantics_unchanged", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "unknown_fields_round_trip_without_changing_request_fingerprint" in name:
        sections.add("identity"); ps.append(_p("identity.fingerprint_before", "eq_path", other="identity.fingerprint_after_round_trip")); semantic_count += 1
    if semantic_count == 0 and "decodes_in_" in name and "without_field_loss" in name:
        sections.add("comparison"); ps.append(_p("comparison.source_semantic_fields", "eq_path", other="comparison.decoded_semantic_fields")); semantic_count += 1
    if semantic_count == 0 and "message_requeue_delay_is_at_least_configured_minimum_retry_backoff" in name:
        sections.add("messaging"); ps.append(_p("messaging.requeue_delay_ms", "ge_path", other="messaging.configured_minimum_retry_backoff_ms")); semantic_count += 1
    if semantic_count == 0 and "nsq_auth_or_tls_configuration_is_consistent" in name:
        sections.add("configuration"); ps.append(_p("configuration.publisher_consumer_broker_security_settings_consistent", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "acceptance_implies_recomputed_hmac_verification_succeeds" in name:
        sections.add("security"); ps.append(_p("security.all_accepted_cases_recomputed_hmac_valid", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "integer_conversion_failure_returns_invalid_argument_without_wrapping" in name:
        sections.add("outcome"); ps.extend([_p("outcome.error_code", "eq", "INVALID_ARGUMENT"), _p("outcome.integer_wrapped", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "timestamp_conversion_failure_returns_invalid_argument_without_panic" in name:
        sections.update({"outcome","process"}); ps.extend([_p("outcome.error_code", "eq", "INVALID_ARGUMENT"), _p("process.panicked", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "constraint_violation_returns_failure_without_process_abort" in name:
        sections.update({"outcome","process"}); ps.extend([_p("outcome.accepted", "eq", False), _p("process.aborted", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "retained_for_configured_diagnostic_window" in name:
        sections.add("retention"); ps.append(_p("retention.actual_retention_seconds", "ge_path", other="retention.configured_minimum_seconds")); semantic_count += 1
    if semantic_count == 0 and "batches_deletions_without_holding_long_table_lock" in name:
        sections.add("database"); ps.extend([_p("database.cleanup_batch_count", "gt", 1), _p("database.maximum_table_lock_ms", "le_path", other="database.configured_long_lock_threshold_ms")]); semantic_count += 2
    if semantic_count == 0 and "records_external_request_id_from_original_gateway_request" in name:
        sections.add("comparison"); ps.append(_p("comparison.batch_external_request_id", "eq_path", other="comparison.gateway_external_request_id")); semantic_count += 1
    if semantic_count == 0 and "records_idempotency_key_from_original_gateway_request" in name:
        sections.add("comparison"); ps.append(_p("comparison.batch_idempotency_key", "eq_path", other="comparison.gateway_idempotency_key")); semantic_count += 1
    if semantic_count == 0 and "records_created_by_service_as_actual_calling_service" in name:
        sections.add("comparison"); ps.append(_p("comparison.batch_created_by_service", "eq_path", other="comparison.actual_calling_service")); semantic_count += 1
    if semantic_count == 0 and "records_caused_by_capsuleer_id_as_authenticated_business_actor" in name:
        sections.add("comparison"); ps.append(_p("comparison.batch_caused_by_capsuleer_id", "eq_path", other="comparison.authenticated_business_actor_id")); semantic_count += 1
    if semantic_count == 0 and "references_exact_" in name:
        sections.add("database"); ps.append(_p("database.reference_matches_exact_expected_parent", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "retry_returns_original_audit_identity_without_creating_second_batch" in name:
        sections.update({"comparison","effects"}); ps.extend([_p("comparison.audit_identity_first", "eq_path", other="comparison.audit_identity_retry"), _p("effects.settlement_batch_count", "eq", 1)]); semantic_count += 2
    if semantic_count == 0 and "contribute_zero_to_ledger_reconciliation" in name:
        sections.add("effects"); ps.append(_p("effects.reconciliation_delta", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "differences_are_limited_to_declared_platform_allowlist" in name:
        sections.add("configuration"); ps.append(_p("configuration.unallowlisted_difference_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "secret_reference_overrides_development_fixture_secret" in name:
        sections.add("comparison"); ps.append(_p("comparison.effective_secret_source", "eq", "explicit_secret_reference")); semantic_count += 1
    if semantic_count == 0 and "configuration_change_requires_restart_when_hot_reload_is_not_supported" in name:
        sections.add("configuration"); ps.extend([_p("configuration.hot_reload_supported", "eq", False), _p("configuration.change_applied_without_restart", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and "resolved_by_documented_precedence_without_nondeterminism" in name:
        sections.add("comparison"); ps.extend([_p("comparison.observed_effective_value", "eq_path", other="comparison.documented_precedence_value"), _p("comparison.repeat_values_all_equal", "eq", True)]); semantic_count += 2
    if semantic_count == 0 and "reaches_gateway_and_returns_single_response_datagram" in name:
        sections.add("transport"); ps.extend([_p("transport.gateway_received_datagram", "eq", True), _p("transport.response_datagram_count", "eq", 1)]); semantic_count += 2
    if semantic_count == 0 and "processed_as_independent_requests" in name:
        sections.add("effects"); ps.extend([_p("effects.success_count", "eq", 2), _p("effects.business_effect_count", "eq", 2)]); semantic_count += 2
    if semantic_count == 0 and "success_rate_for_unrelated_principals_stays_above_slo" in name:
        sections.add("measurement"); ps.append(_p("measurement.success_rate", "ge_path", other="measurement.slo_success_rate")); semantic_count += 1
    if semantic_count == 0 and "load_shedding_prefers_explicit_backpressure_over_unbounded_queue_growth" in name:
        sections.add("measurement"); ps.extend([_p("measurement.explicit_backpressure_observed", "eq", True), _p("measurement.peak_queue_depth", "le_path", other="measurement.configured_queue_bound")]); semantic_count += 2
    if semantic_count == 0 and "records_one_observation_per_terminal_gateway_request" in name:
        sections.add("metrics"); ps.append(_p("metrics.histogram_observation_count", "eq_path", other="metrics.terminal_gateway_request_count")); semantic_count += 1
    if semantic_count == 0 and "alert_fires_when" in name:
        sections.add("metrics"); ps.extend([_p("metrics.trigger_preconditions_satisfied", "eq", True), _p("metrics.alert_fired", "eq", True)]); semantic_count += 2
    if semantic_count == 0 and "created_without_blocking_required_production_writes" in name:
        sections.add("database"); ps.extend([_p("database.index_created", "eq", True), _p("database.required_write_blocked", "eq", False)]); semantic_count += 2
    if semantic_count == 0 and name.startswith("test_proposed_test_names_"):
        sections.add("naming"); ps.append(_p("naming.violation_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "fuzz_corpus_seed_names_or_metadata_identify_regression_issue" in name:
        sections.add("coverage"); ps.append(_p("coverage.unattributed_bug_regression_seed_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "fuzz_regression_seed_suite_runs_in_ci_when_rust_fuzz_targets_exist" in name:
        sections.add("coverage"); ps.append(_p("coverage.rust_fuzz_targets_without_ci_regression_seed_job", "eq", [])); semantic_count += 1
    if semantic_count == 0 and "reports_remaining_quantity_equal_to_item_escrow_quantity" in name:
        sections.add("comparison"); ps.append(_p("comparison.read_model_remaining_quantity", "eq_path", other="comparison.item_escrow_quantity")); semantic_count += 1
    if semantic_count == 0 and "returns_exact_remaining_quantity_to_original_seller_stack" in name:
        sections.add("state"); ps.append(_p("state.seller_stack_quantity_delta", "eq_path", other="state.expected_remaining_item_refund")); semantic_count += 1
    if semantic_count == 0 and "returns_exact_remaining_isk_to_declared_refund_wallet" in name:
        sections.add("state"); ps.append(_p("state.refund_wallet_isk_delta", "eq_path", other="state.expected_remaining_isk_refund")); semantic_count += 1
    if semantic_count == 0 and "creates_exactly_one_request_attempt_row" in name:
        sections.add("effects"); ps.append(_p("effects.request_attempt_row_delta", "eq", 1)); semantic_count += 1
    if semantic_count == 0 and "history_survives_worker_restart" in name:
        sections.add("comparison"); ps.append(_p("comparison.history_before_restart", "eq_path", other="comparison.history_after_restart")); semantic_count += 1
    if semantic_count == 0 and "do_not_interpolate_it_directly_into_executable_shell" in name:
        sections.add("security"); ps.append(_p("security.direct_untrusted_shell_interpolation_count", "eq", 0)); semantic_count += 1
    if semantic_count == 0 and "security_scan_job_runs_even_when_earlier_nondependent_verification_job_fails" in name:
        sections.add("ci"); ps.extend([_p("ci.earlier_job_forced_failed", "eq", True), _p("ci.security_scan_job_executed", "eq", True)]); semantic_count += 2
    if semantic_count == 0 and "cargo_audit_scans_locked_dependencies_used_by_trade_settlement_build" in name:
        sections.add("coverage"); ps.extend([_p("coverage.trade_settlement_lockfile_audited", "eq", True), _p("coverage.runtime_dependency_graph_audited", "eq", True)]); semantic_count += 2
    if semantic_count == 0 and "secret_scan_regression_fixture_proves_known_fake_secret_pattern_is_detected" in name:
        sections.add("security"); ps.append(_p("security.known_fake_secret_fixture_detected", "eq", True)); semantic_count += 1
    if semantic_count == 0 and "verifies_at_least_one_request_crossed_targeted_failure_window" in name:
        sections.add("fault"); ps.append(_p("fault.requests_crossing_target_window", "ge", 1)); semantic_count += 1

    if semantic_count == 0:
        raise ValueError(f"no semantic evidence rule for contract: category={category} name={name}")

    return EvidenceSpec(name=name, category=category, predicates=tuple(ps), required_sections=tuple(sorted(sections)))


def _get_path(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise AssertionError(f"evidence missing required path {path!r}; stopped at {part!r}")
        cur = cur[part]
    return cur


def evaluate_predicate(data: dict[str, Any], predicate: Predicate) -> None:
    actual = _get_path(data, predicate.path)
    other = _get_path(data, predicate.other_path) if predicate.other_path else None
    op = predicate.op
    if op == "eq":
        assert actual == predicate.expected, (predicate.path, actual, predicate.expected)
    elif op == "ne":
        assert actual != predicate.expected, (predicate.path, actual, predicate.expected)
    elif op == "gt":
        assert actual > predicate.expected, (predicate.path, actual, predicate.expected)
    elif op == "ge":
        assert actual >= predicate.expected, (predicate.path, actual, predicate.expected)
    elif op == "lt":
        assert actual < predicate.expected, (predicate.path, actual, predicate.expected)
    elif op == "le":
        assert actual <= predicate.expected, (predicate.path, actual, predicate.expected)
    elif op == "eq_path":
        assert actual == other, (predicate.path, actual, predicate.other_path, other)
    elif op == "ne_path":
        assert actual != other, (predicate.path, actual, predicate.other_path, other)
    elif op == "gt_path":
        assert actual > other, (predicate.path, actual, predicate.other_path, other)
    elif op == "ge_path":
        assert actual >= other, (predicate.path, actual, predicate.other_path, other)
    elif op == "lt_path":
        assert actual < other, (predicate.path, actual, predicate.other_path, other)
    elif op == "le_path":
        assert actual <= other, (predicate.path, actual, predicate.other_path, other)
    else:
        raise AssertionError(f"unknown evidence predicate op {op!r}")


def validate_evidence(spec: EvidenceSpec, result: dict[str, Any]) -> None:
    for section in spec.required_sections:
        assert isinstance(result.get(section), dict), f"{spec.name}: missing evidence section {section!r}"
    for predicate in spec.predicates:
        evaluate_predicate(result, predicate)


def spec_to_dict(spec: EvidenceSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "category": spec.category,
        "required_sections": list(spec.required_sections),
        "predicates": [
            {"path": p.path, "op": p.op, "expected": p.expected, "other_path": p.other_path}
            for p in spec.predicates
        ],
    }
