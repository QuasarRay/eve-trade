from __future__ import annotations

import string
from typing import Any

from hypothesis import strategies as st


SMALL_QUANTITY = st.integers(min_value=1, max_value=20)
SMALL_PRICE = st.integers(min_value=1, max_value=10_000)
NONCE = st.text(alphabet=string.ascii_lowercase + string.digits, min_size=1, max_size=12)
UUID_TEXT = st.uuids().map(str)
HASH_SEED = st.integers(min_value=0, max_value=2**32 - 1)


def _base(**extra):
    values = {"nonce": NONCE}
    values.update(extra)
    return st.fixed_dictionaries(values)


def contract_strategy(category: int, name: str):
    """Return input generation that can actually reach the named boundary.

    Category-specific state-machine/fault strategies deliberately precede broad
    token matching; the previous ordering accidentally starved several sequence
    properties of their ``ops`` input.
    """
    if category in {32, 33, 83}:
        operation = st.sampled_from(["issue", "accept", "cancel", "retry", "invalid"])
        valid_operation = st.sampled_from(["issue", "accept", "cancel", "retry"])
        if category == 32 and "invalid_settlement_operation_sequence" in name:
            # Every generated example contains an invalid operation; the previous
            # strategy only made invalid operations possible, which allowed vacuous
            # examples that never exercised the named boundary.
            ops = st.tuples(
                st.lists(valid_operation, min_size=0, max_size=12),
                st.just("invalid"),
                st.lists(valid_operation, min_size=0, max_size=12),
            ).map(lambda parts: [*parts[0], parts[1], *parts[2]])
        elif category == 32 and "random_retry_sequence" in name:
            ops = st.tuples(
                st.lists(valid_operation, min_size=0, max_size=12),
                st.just("retry"),
                st.lists(valid_operation, min_size=0, max_size=12),
            ).map(lambda parts: [*parts[0], parts[1], *parts[2]])
        else:
            ops = st.lists(operation, min_size=1, max_size=40)
        extra = {}
        if category == 83 and "thousand_random_trades" in name:
            extra["sequence_length"] = st.just(1000)
        return _base(
            ops=ops,
            quantity=SMALL_QUANTITY,
            price=SMALL_PRICE,
            parts=st.lists(st.integers(min_value=1, max_value=8), min_size=1, max_size=10),
            **extra,
        )

    if category in {24, 36, 44, 46, 54, 64, 66, 101}:
        return _base(
            failure_iteration=st.integers(min_value=0, max_value=20),
            delay_ms=st.integers(min_value=0, max_value=2_000),
            workers=st.integers(min_value=2, max_value=8),
            operations=st.integers(min_value=2, max_value=32),
        )

    if category == 3:
        return _base(
            quantity=SMALL_QUANTITY,
            price=SMALL_PRICE,
            garbage=st.binary(min_size=0, max_size=1024),
            text=st.text(min_size=0, max_size=160),
            bit=st.integers(min_value=0, max_value=7),
            exponent_token=st.sampled_from(["1e3", "1E3", "1e+3", "10e2"]),
            integral_fraction_token=st.sampled_from(["0.0", "1.0", "2.000", "10.0000"]),
            nesting_depth=st.integers(min_value=2, max_value=256),
            field_count=st.integers(min_value=2, max_value=2048),
        )

    if category in {4, 5, 6, 7, 28, 29, 30, 31, 48, 61, 76, 78, 79, 86}:
        return _base(
            quantity=SMALL_QUANTITY,
            price=SMALL_PRICE,
            garbage=st.binary(min_size=0, max_size=512),
            text=st.text(min_size=0, max_size=120),
            uuid=UUID_TEXT,
            other_uuid=UUID_TEXT,
            workers=st.integers(min_value=2, max_value=8),
            operations=st.integers(min_value=2, max_value=24),
        )

    if any(token in name for token in ("concurrent", "race", "load", "burst", "throughput", "hot_", "fairness")):
        return _base(
            quantity=SMALL_QUANTITY,
            price=SMALL_PRICE,
            workers=st.integers(min_value=2, max_value=8),
            operations=st.integers(min_value=2, max_value=32),
        )

    if any(token in name for token in ("retry", "backoff", "redelivery", "attempt", "lease", "timeout", "deadline")):
        return _base(
            attempts=st.integers(min_value=1, max_value=8),
            delay_ms=st.integers(min_value=0, max_value=5_000),
            quantity=SMALL_QUANTITY,
            price=SMALL_PRICE,
        )

    if any(token in name for token in ("quantity", "isk", "price", "balance", "overflow", "numeric", "conservation", "reconciliation")):
        return _base(
            quantity=SMALL_QUANTITY,
            price=SMALL_PRICE,
            seller_isk=st.integers(min_value=0, max_value=100_000),
            buyer_isk=st.integers(min_value=0, max_value=100_000),
            parts=st.lists(st.integers(min_value=1, max_value=8), min_size=1, max_size=8),
        )

    if any(token in name for token in ("uuid", "identifier", "idempotency", "fingerprint", "message_id", "correlation_id", "trade_id")):
        return _base(
            uuid=UUID_TEXT,
            other_uuid=UUID_TEXT,
            text=st.text(min_size=0, max_size=80),
            quantity=SMALL_QUANTITY,
            price=SMALL_PRICE,
        )

    # Repository/configuration properties are deterministic invariants.  Hypothesis
    # still controls traversal perturbations that handlers may opt into, but the
    # suite intentionally does not pretend that arbitrary text is meaningful data.
    return _base(reverse=st.booleans(), sample=st.integers(min_value=0, max_value=32))
