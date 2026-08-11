from __future__ import annotations

"""Stdlib-only, non-vacuous helpers for behavioral property laws."""

from copy import deepcopy
from dataclasses import dataclass
import math
import random
from typing import Any, Callable, Iterable


class LawFailure(AssertionError):
    pass


@dataclass(frozen=True)
class LawObservation:
    law: str
    cases: int
    detail: str = ""
    seed: int | None = None
    counterexample: Any = None


def require(law: str, condition: bool, detail: str = "") -> None:
    if not condition:
        suffix = f": {detail}" if detail else ""
        raise LawFailure(f"law {law!r} violated{suffix}")


def _materialize(law: str, cases: Iterable[Any]) -> list[Any]:
    values = list(cases)
    if not values:
        raise ValueError(f"{law} law requires at least one case")
    return values


def _call(law: str, fn: Callable, case_id: int, *args):
    try:
        return fn(*deepcopy(args))
    except LawFailure:
        raise
    except BaseException as exc:
        raise LawFailure(f"law {law!r} raised for case #{case_id} {args!r}: {type(exc).__name__}: {exc}") from exc


def _equal(left: Any, right: Any, *, nan_equal: bool = False) -> bool:
    if isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right):
        return nan_equal
    return left == right


def deterministic(
    fn: Callable[[Any], Any],
    cases: Iterable[Any],
    *,
    runs: int = 3,
    normalize=lambda value: value,
    nan_equal: bool = False,
) -> LawObservation:
    if runs < 2:
        raise ValueError("determinism law requires at least two runs")
    values = _materialize("deterministic", cases)
    for index, case in enumerate(values, 1):
        first = normalize(_call("deterministic", fn, index, case))
        for _ in range(runs - 1):
            observed = normalize(_call("deterministic", fn, index, case))
            require("deterministic", _equal(observed, first, nan_equal=nan_equal), f"case #{index}={case!r}")
    return LawObservation("deterministic", len(values), f"runs={runs}")


def idempotent(fn: Callable[[Any], Any], cases: Iterable[Any], *, normalize=lambda value: value) -> LawObservation:
    values = _materialize("idempotent", cases)
    for index, case in enumerate(values, 1):
        once = _call("idempotent", fn, index, case)
        twice = _call("idempotent", fn, index, once)
        require("idempotent", normalize(twice) == normalize(once), f"case #{index}={case!r}")
    return LawObservation("idempotent", len(values))


def roundtrip(
    encode: Callable[[Any], Any],
    decode: Callable[[Any], Any],
    cases: Iterable[Any],
    *,
    normalize=lambda value: value,
) -> LawObservation:
    values = _materialize("roundtrip", cases)
    for index, case in enumerate(values, 1):
        encoded = _call("roundtrip.encode", encode, index, case)
        decoded = _call("roundtrip.decode", decode, index, encoded)
        require("roundtrip", normalize(decoded) == normalize(case), f"case #{index}={case!r}")
    return LawObservation("roundtrip", len(values))


def commutative(op: Callable[[Any, Any], Any], pairs: Iterable[tuple[Any, Any]], *, normalize=lambda value: value) -> LawObservation:
    values = _materialize("commutative", pairs)
    for index, (left, right) in enumerate(values, 1):
        forward = _call("commutative", op, index, left, right)
        reverse = _call("commutative", op, index, right, left)
        require("commutative", normalize(forward) == normalize(reverse), f"case #{index}: {left!r}, {right!r}")
    return LawObservation("commutative", len(values))


def associative(op: Callable[[Any, Any], Any], triples: Iterable[tuple[Any, Any, Any]], *, normalize=lambda value: value) -> LawObservation:
    values = _materialize("associative", triples)
    for index, (a, b, c) in enumerate(values, 1):
        ab = _call("associative", op, index, a, b)
        left = _call("associative", op, index, ab, c)
        bc = _call("associative", op, index, b, c)
        right = _call("associative", op, index, a, bc)
        require("associative", normalize(left) == normalize(right), f"case #{index}: {a!r}, {b!r}, {c!r}")
    return LawObservation("associative", len(values))


def monotonic(
    fn: Callable[[Any], Any],
    ordered_pairs: Iterable[tuple[Any, Any]],
    *,
    le=lambda left, right: left <= right,
    direction: str = "increasing",
) -> LawObservation:
    if direction not in {"increasing", "decreasing"}:
        raise ValueError("monotonic direction must be increasing or decreasing")
    values = _materialize("monotonic", ordered_pairs)
    for index, (low, high) in enumerate(values, 1):
        require("monotonic.precondition", le(low, high), f"unordered case #{index}: {low!r}, {high!r}")
        low_value = _call("monotonic", fn, index, low)
        high_value = _call("monotonic", fn, index, high)
        condition = le(low_value, high_value) if direction == "increasing" else le(high_value, low_value)
        require("monotonic", condition, f"case #{index}: low={low!r}, high={high!r}, direction={direction}")
    return LawObservation("monotonic", len(values), f"direction={direction}")


def conservation(
    transform: Callable[[Any], Any],
    cases: Iterable[Any],
    measure: Callable[[Any], Any],
    *,
    tolerance: float | None = None,
) -> LawObservation:
    if tolerance is not None and (not math.isfinite(tolerance) or tolerance < 0):
        raise ValueError("conservation tolerance must be finite and non-negative")
    values = _materialize("conservation", cases)
    for index, case in enumerate(values, 1):
        before = _call("conservation.measure", measure, index, case)
        transformed = _call("conservation.transform", transform, index, case)
        after = _call("conservation.measure", measure, index, transformed)
        if isinstance(before, float) or isinstance(after, float):
            equal = before == after if tolerance is None else math.isclose(before, after, rel_tol=tolerance, abs_tol=tolerance)
        else:
            equal = before == after
        require("conservation", equal, f"case #{index}: before={before!r}, after={after!r}, tolerance={tolerance!r}")
    return LawObservation("conservation", len(values), f"tolerance={tolerance!r}")


def differential(
    left: Callable[[Any], Any],
    right: Callable[[Any], Any],
    cases: Iterable[Any],
    *,
    normalize=lambda value: value,
) -> LawObservation:
    values = _materialize("differential", cases)
    for index, case in enumerate(values, 1):
        left_value = normalize(_call("differential.left", left, index, case))
        right_value = normalize(_call("differential.right", right, index, case))
        require("differential", left_value == right_value, f"case #{index}={case!r}, left={left_value!r}, right={right_value!r}")
    return LawObservation("differential", len(values))


def invariant_sequence(
    initial: Any,
    actions: Iterable[Any],
    step: Callable[[Any, Any], Any],
    invariant: Callable[[Any], bool],
    *,
    require_nonempty: bool = True,
) -> LawObservation:
    values = list(actions)
    if require_nonempty and not values:
        raise ValueError("state-sequence law requires at least one action")
    state = deepcopy(initial)
    require("state.invariant", bool(_call("state.invariant", invariant, 0, state)), "initial state")
    for index, action in enumerate(values, 1):
        state = _call("state.step", step, index, state, action)
        require("state.invariant", bool(_call("state.invariant", invariant, index, state)), f"after action #{index}: {action!r}")
    return LawObservation("state.invariant", len(values))


def generated_cases(generator: Callable[[random.Random], Any], count: int, *, seed: int) -> tuple[list[Any], int]:
    if count <= 0:
        raise ValueError("generated property suite requires a positive case count")
    rng = random.Random(seed)
    return [generator(rng) for _ in range(count)], seed


def minimize_counterexample(value: Any, fails: Callable[[Any], bool]) -> Any:
    """Deterministically shrink common sequence/string/integer counterexamples."""
    current = deepcopy(value)
    candidates = []
    if isinstance(current, int):
        candidates = [0, 1, -1, current // 2]
    elif isinstance(current, str):
        candidates = ["", current[: len(current) // 2], current[:1]]
    elif isinstance(current, (list, tuple)):
        candidates = [type(current)(), current[: len(current) // 2], current[:1]]
    for candidate in candidates:
        try:
            if fails(deepcopy(candidate)):
                current = candidate
        except Exception:
            current = candidate
    return current
