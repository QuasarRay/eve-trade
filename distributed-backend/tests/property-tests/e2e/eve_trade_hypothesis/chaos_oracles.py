from __future__ import annotations

from statistics import median
from typing import Any

from .evidence_integrity import EvidenceIntegrityError


IMPLEMENTED_CHAOS_ORACLES = frozenset(
    {
        "test_litmus_pod_delete_experiment_targets_only_selected_eve_trade_workload_labels",
        "test_litmus_network_loss_experiment_proves_impairment_is_active_before_workload_assertions_begin",
        "test_litmus_network_delay_experiment_records_injected_latency_range_in_test_evidence",
        "test_litmus_experiment_cleanup_restores_all_affected_network_and_pod_resources",
    }
)


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceIntegrityError(f"{path} must be an object")
    return value


def _network_signal(result: dict[str, Any]) -> dict[str, Any]:
    signals = result["fault"]["effect_signals"]
    matches = [item for item in signals if item.get("kind") == "network_probe"]
    if len(matches) != 1:
        raise EvidenceIntegrityError("the chaos oracle requires exactly one raw network_probe")
    return _mapping(matches[0].get("raw"), "fault.effect_signals.network_probe.raw")


def _loss(samples: list[dict[str, Any]]) -> float:
    if not samples:
        raise EvidenceIntegrityError("network probe sample set is empty")
    return 100.0 * sum(item.get("success") is not True for item in samples) / len(samples)


def _selector_pairs(selector: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for expression in selector.split(","):
        if "=" not in expression:
            raise EvidenceIntegrityError(f"unsupported target selector expression: {expression!r}")
        key, value = expression.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def _validate_target_selection(result: dict[str, Any], litmus_contract: dict[str, Any]) -> None:
    wanted = _selector_pairs(litmus_contract["target"]["selector"])
    resources = result["fault"]["target"]["selected_resources"]
    selected_uids: set[str] = set()
    for index, resource in enumerate(resources):
        metadata = _mapping(
            _mapping(resource, f"selected_resources[{index}]").get("metadata"),
            f"selected_resources[{index}].metadata",
        )
        labels = _mapping(
            metadata.get("labels"), f"selected_resources[{index}].metadata.labels"
        )
        mismatches = {key: value for key, value in wanted.items() if labels.get(key) != value}
        if mismatches:
            raise EvidenceIntegrityError(
                f"selected target {index} violates the exact contract selector: {mismatches}"
            )
        uid = metadata.get("uid")
        if not isinstance(uid, str) or not uid:
            raise EvidenceIntegrityError(f"selected target {index} has no UID")
        selected_uids.add(uid)
    if result["fault"]["target"].get("resource_identity") not in selected_uids:
        raise EvidenceIntegrityError("fault target identity is not one of the selected resources")
    transitions = [
        signal
        for signal in result["fault"]["effect_signals"]
        if signal.get("kind") == "pod_uid_transition"
    ]
    if len(transitions) != 1:
        raise EvidenceIntegrityError("target-selection oracle requires one pod UID transition")
    transition_raw = _mapping(transitions[0].get("raw"), "pod_uid_transition.raw")
    before_uids = set(transition_raw.get("before_uids") or [])
    if before_uids != selected_uids:
        raise EvidenceIntegrityError(
            "physical pod transition is not bound to the exact selected target resources"
        )


def _validate_network_loss(request: dict[str, Any], result: dict[str, Any]) -> None:
    raw = _network_signal(result)
    baseline = raw.get("baseline")
    active = raw.get("active")
    if not isinstance(baseline, list) or not isinstance(active, list):
        raise EvidenceIntegrityError("network loss oracle lacks raw baseline/active samples")
    measured = _loss(active)
    requested = float(request["case"]["network_loss_percent"])
    minimum_effect = max(2.0, requested * 0.25)
    if _loss(baseline) != 0:
        raise EvidenceIntegrityError("network-loss control baseline was impaired")
    if measured < minimum_effect:
        raise EvidenceIntegrityError(
            f"measured active loss {measured}% is below the {minimum_effect}% "
            f"physical-effect threshold for generated loss {requested}%"
        )


def _validate_network_latency(request: dict[str, Any], result: dict[str, Any]) -> None:
    raw = _network_signal(result)
    baseline = [
        float(item["latency_ms"])
        for item in raw.get("baseline", [])
        if item.get("success") is True and isinstance(item.get("latency_ms"), (int, float))
    ]
    active = [
        float(item["latency_ms"])
        for item in raw.get("active", [])
        if item.get("success") is True and isinstance(item.get("latency_ms"), (int, float))
    ]
    if not baseline or not active:
        raise EvidenceIntegrityError("latency oracle lacks successful raw baseline/active samples")
    requested = float(request["case"]["network_latency_ms"])
    measured_delta = median(active) - median(baseline)
    if measured_delta < requested * 0.5:
        raise EvidenceIntegrityError(
            f"measured median latency delta {measured_delta}ms does not evidence generated {requested}ms"
        )


def _validate_cleanup(result: dict[str, Any]) -> None:
    probes = result["recovery"]["raw_probes"]
    absent = [probe for probe in probes if probe.get("kind") == "fault_absent"]
    if len(absent) != 1:
        raise EvidenceIntegrityError("cleanup oracle requires exactly one fault_absent probe")
    raw = _mapping(absent[0].get("raw"), "recovery.fault_absent.raw")
    if raw.get("remaining_chaos_resources") != []:
        raise EvidenceIntegrityError("execution-scoped Litmus resources remain after cleanup")
    comparison = _mapping(result.get("comparison"), "comparison")
    if comparison.get("before") != comparison.get("after"):
        raise EvidenceIntegrityError("target resource identity/readiness was not restored after cleanup")


def validate_chaos_oracle(
    contract: str,
    request: dict[str, Any],
    result: dict[str, Any],
    litmus_contract: dict[str, Any],
) -> None:
    if contract not in IMPLEMENTED_CHAOS_ORACLES:
        raise EvidenceIntegrityError(
            f"no repository-owned independent chaos oracle is implemented for {contract}"
        )
    if contract == "test_litmus_pod_delete_experiment_targets_only_selected_eve_trade_workload_labels":
        _validate_target_selection(result, litmus_contract)
    elif contract == "test_litmus_network_loss_experiment_proves_impairment_is_active_before_workload_assertions_begin":
        _validate_network_loss(request, result)
    elif contract == "test_litmus_network_delay_experiment_records_injected_latency_range_in_test_evidence":
        _validate_network_latency(request, result)
    elif contract == "test_litmus_experiment_cleanup_restores_all_affected_network_and_pod_resources":
        _validate_cleanup(result)
