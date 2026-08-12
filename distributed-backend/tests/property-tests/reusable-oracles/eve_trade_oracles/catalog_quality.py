"""Oracles over the immutable proposed-business-test catalog.

The caller owns repository loading.  These functions accept plain records and
express only the pass/fail relations named by the business tests.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping


PLACEHOLDER = re.compile(r"(?:^|_)(?:todo|fixme|tbd)(?:_|$)")
VAGUE_SUCCESS = re.compile(r"(?:^|_)(?:works|handles|behaves|correctly|properly)(?:_|$)")
ALTERNATIVE_ACCEPT_REJECT = re.compile(r"_(?:rejected|accepted)_or_(?:rejected|accepted)_")


def _names(records: Iterable[Mapping[str, object]]) -> list[str]:
    names = [str(record["name"]) for record in records]
    if not names:
        raise AssertionError("business catalog is empty")
    if any(not re.fullmatch(r"test_[a-z0-9_]+", name) for name in names):
        raise AssertionError("business catalog contains a malformed pytest identity")
    return names


def assert_catalog_names_unique(records: Iterable[Mapping[str, object]]) -> None:
    names = _names(records)
    duplicates = sorted(name for name, count in Counter(names).items() if count != 1)
    assert not duplicates, f"business names must occur exactly once: {duplicates[:20]}"


def assert_no_placeholder_words(records: Iterable[Mapping[str, object]]) -> None:
    subject = "test_proposed_test_names_contain_no_placeholder_words_todo_fixme_or_tbd"
    bad = sorted(name for name in _names(records) if name != subject and PLACEHOLDER.search(name))
    assert not bad, f"placeholder words occur in proposed names: {bad[:20]}"


def assert_no_vague_success_verbs(records: Iterable[Mapping[str, object]]) -> None:
    bad = sorted(name for name in _names(records) if VAGUE_SUCCESS.search(name))
    assert not bad, f"vague success verbs occur in proposed names: {bad[:20]}"


def assert_no_alternative_accept_reject_outcomes(records: Iterable[Mapping[str, object]]) -> None:
    subject = "test_proposed_test_names_do_not_encode_two_alternative_expected_outcomes_with_rejected_or_accepted_wording"
    bad = sorted(
        name for name in _names(records) if name != subject and ALTERNATIVE_ACCEPT_REJECT.search(name)
    )
    assert not bad, f"alternative accepted/rejected expected outcomes are ambiguous: {bad[:20]}"


def assert_reject_names_identify_rejected_condition(records: Iterable[Mapping[str, object]]) -> None:
    bad: list[str] = []
    generic_subjects = {"input", "request", "command", "value", "payload", "message", "operation"}
    for name in _names(records):
        if not re.search(r"(?:^|_)(?:reject|rejects|rejected)(?:_|$)", name):
            continue
        if "_rejects_" in name:
            condition = name.split("_rejects_", 1)[1]
        elif name.endswith(("_is_rejected", "_are_rejected")):
            condition = re.sub(r"_(?:is|are)_rejected$", "", name).removeprefix("test_")
        else:
            # Names about rejection classes, counters, mappings, or naming
            # policy do not use `reject(s) CONDITION` as their predicate.
            continue
        descriptive = [token for token in condition.split("_") if token not in generic_subjects]
        if len(descriptive) < 2:
            bad.append(name)
    assert not bad, f"reject names omit the specific rejected condition: {sorted(bad)[:20]}"


def assert_retry_names_identify_business_effect_repeatability(records: Iterable[Mapping[str, object]]) -> None:
    business_tokens = (
        "ledger", "escrow", "transfer", "refund", "business_effect", "state_change",
        "resurrect", "trade_version", "second_market_operation", "second_business_effect",
        "duplicate_trade", "duplicate_item", "duplicate_settlement", "terminal_business_result",
        "command_is_observationally", "operation_exactly_once",
    )
    repeatability_tokens = (
        "duplicate", "idempot", "twice", "second_", "exactly_once", "exactly_one",
        "without_reexecution", "same_", "repeat", "original_", "resurrect", "overwritten",
        "observationally", "does_not_refund",
    )
    bad = []
    for name in _names(records):
        active_retry = re.search(r"(?:^|_)retry(?:ing|_of|_does|_returns|_after)?(?:_|$)", name)
        if active_retry and any(token in name for token in business_tokens):
            if not any(token in name for token in repeatability_tokens):
                bad.append(name)
    assert not bad, f"business retry names omit whether the business effect may repeat: {sorted(bad)[:20]}"


def assert_concurrent_names_identify_shared_resource_or_boundary(records: Iterable[Mapping[str, object]]) -> None:
    boundary_tokens = (
        "same_", "shared_", "trade", "wallet", "stack", "idempotency_key", "outbox",
        "rate_limit", "socket", "shutdown", "supported_schema", "settlement", "commit",
        "global", "serial", "destination", "source", "capacity", "request",
        "available_quantity", "winner", "first_handler_execution",
    )
    bad = [
        name for name in _names(records)
        if "concurrent" in name and not any(token in name for token in boundary_tokens)
    ]
    assert not bad, f"concurrent names omit the shared resource or race boundary: {sorted(bad)[:20]}"


def assert_crash_names_identify_window_and_recovery(records: Iterable[Mapping[str, object]]) -> None:
    window_tokens = ("before", "after", "during", "between", "at_")
    invariant_tokens = (
        "restart", "recover", "redeliver", "resume", "rollback", "reconcile", "transfer",
        "cannot_lose", "leaves_no", "leaves_message", "single_terminal", "idempot",
    )
    bad = []
    for name in _names(records):
        if "crash" not in name:
            continue
        if any(
            token in name
            for token in (
                "without_process_crash", "fuzz_crash", "crash_test", "crash_failpoint",
                "crash_injection", "production_gate_fails_if_all_crash",
                "proposed_test_names_using_crash", "every_crash_recovery_test",
            )
        ):
            continue
        has_window = any(token in name for token in window_tokens) or any(
            token in name for token in ("crash_and_restart_sequence", "replica_crash")
        )
        has_invariant = any(token in name for token in invariant_tokens) or any(
            token in name for token in ("without_", "exactly_one", "converges_to")
        )
        if not has_window or not has_invariant:
            bad.append(name)
    assert not bad, f"crash names omit the crash window or post-restart invariant: {sorted(bad)[:20]}"


def assert_timeout_names_identify_boundary_and_persistence_invariant(records: Iterable[Mapping[str, object]]) -> None:
    boundary_tokens = (
        "downstream", "checkout", "request", "configuration", "lock_wait", "transaction",
        "publish", "statement", "nsqd_message", "worker", "udp", "deadline",
    )
    invariant_tokens = (
        "releases", "exceeds", "has_timeout", "does_not", "returns", "keeps", "is_rejected",
        "before", "after", "leak", "preserves", "unknown_commit", "partial", "recovers",
        "idempotently", "at_startup",
    )
    bad = []
    for name in _names(records):
        if "timeout" not in name or "timeout_test_proves" in name or "using_timeout_identify" in name:
            continue
        has_boundary = any(token in name for token in boundary_tokens) or "configured_timeout" in name
        if not has_boundary or not any(token in name for token in invariant_tokens):
            bad.append(name)
    assert not bad, f"timeout names omit the timeout boundary or persistence invariant: {sorted(bad)[:20]}"


def assert_invalid_names_identify_invalid_property(records: Iterable[Mapping[str, object]]) -> None:
    generic_endings = ("_invalid", "_invalid_input", "_invalid_value", "_invalid_request")
    bad = [name for name in _names(records) if "invalid" in name and name.endswith(generic_endings)]
    assert not bad, f"invalid names omit the exact invalid property: {sorted(bad)[:20]}"


def assert_catalog_names_equal_classification(
    source_records: Iterable[Mapping[str, object]],
    classified_records: Iterable[Mapping[str, object]],
    *,
    expected_count: int,
) -> None:
    source_names = _names(source_records)
    classified_names = _names(classified_records)
    assert len(source_names) == expected_count, f"source catalog count changed: {len(source_names)}"
    assert len(classified_names) == expected_count, f"classified catalog count changed: {len(classified_names)}"
    assert set(source_names) == set(classified_names), "classification omits or invents a required business contract"


def assert_catalog_digest(catalog_root: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    paths = sorted(path for path in catalog_root.rglob("*.md") if path.name != "test-rules.md")
    assert len(paths) == 46, f"expected 46 catalog leaves, found {len(paths)}"
    for path in paths:
        digest.update(path.relative_to(catalog_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    actual = digest.hexdigest()
    assert actual == expected_sha256, f"canonical catalog changed without explicit digest update: {actual}"
