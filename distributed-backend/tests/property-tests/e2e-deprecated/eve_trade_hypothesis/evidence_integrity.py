from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable


PROTOCOL_VERSION = 3
EVIDENCE_SCHEMA = "eve-trade.external-evidence/v3"
CHAOS_EVIDENCE_SCHEMA = "eve-trade.chaos-evidence/v1"


class EvidenceIntegrityError(AssertionError):
    """Evidence is stale, unbound, self-asserted, or missing physical proof."""


def parse_timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise EvidenceIntegrityError(f"{path} must be an RFC3339 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EvidenceIntegrityError(f"{path} is not an RFC3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise EvidenceIntegrityError(f"{path} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def isoformat_utc(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceIntegrityError(f"{path} must be an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceIntegrityError(f"{path} must be a list")
    return value


def _exact(value: Any, expected: Any, path: str) -> None:
    if value != expected:
        raise EvidenceIntegrityError(f"{path} mismatch: expected {expected!r}, got {value!r}")


def _true(value: Any, path: str) -> None:
    if value is not True:
        raise EvidenceIntegrityError(f"{path} must be true")


def _walk_forbidden_assertions(value: Any, path: str = "observations") -> None:
    forbidden = {"expected", "assertion_passed", "predicate", "oracle_result", "should_pass"}
    if isinstance(value, dict):
        overlap = forbidden.intersection(value)
        if overlap:
            raise EvidenceIntegrityError(
                f"{path} contains collector-authored oracle fields: {sorted(overlap)}"
            )
        for key, child in value.items():
            _walk_forbidden_assertions(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden_assertions(child, f"{path}[{index}]")


def validate_observation_records(
    observations: Any,
    *,
    issued_at: datetime,
    collected_at: datetime,
) -> None:
    records = _list(observations, "observations")
    if not records:
        raise EvidenceIntegrityError("observations must contain raw records")
    _walk_forbidden_assertions(records)
    for index, raw_record in enumerate(records):
        path = f"observations[{index}]"
        record = _mapping(raw_record, path)
        source = record.get("source")
        if not isinstance(source, str) or not source or source in {"collector", "expected", "oracle"}:
            raise EvidenceIntegrityError(f"{path}.source must identify a physical source")
        kind = record.get("kind")
        if not isinstance(kind, str) or not kind:
            raise EvidenceIntegrityError(f"{path}.kind must identify the raw observation type")
        observed_at = parse_timestamp(record.get("observed_at"), f"{path}.observed_at")
        if observed_at < issued_at - timedelta(seconds=2) or observed_at > collected_at + timedelta(seconds=2):
            raise EvidenceIntegrityError(f"{path} was observed outside this execution window")
        if not any(key in record for key in ("raw", "value", "resource", "samples")):
            raise EvidenceIntegrityError(f"{path} contains no raw value/resource/samples")


def validate_execution_identity(
    request: dict[str, Any],
    result: dict[str, Any],
    *,
    seen_evidence_ids: set[str],
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    if result.get("ok") is not None:
        raise EvidenceIntegrityError("generic ok responses are forbidden")
    _exact(result.get("protocol_version"), PROTOCOL_VERSION, "protocol_version")
    _exact(result.get("evidence_schema"), EVIDENCE_SCHEMA, "evidence_schema")
    _exact(result.get("contract"), request["contract"], "contract")
    _exact(result.get("case_sha256"), request["case_sha256"], "case_sha256")
    request_execution = _mapping(request.get("execution"), "request.execution")
    result_execution = _mapping(result.get("execution"), "execution")
    for key in ("run_id", "invocation_id", "nonce"):
        _exact(result_execution.get(key), request_execution.get(key), f"execution.{key}")
    evidence_id = result.get("evidence_id")
    if not isinstance(evidence_id, str) or len(evidence_id) < 16:
        raise EvidenceIntegrityError("evidence_id must be a fresh opaque identifier")
    if evidence_id in seen_evidence_ids:
        raise EvidenceIntegrityError(f"replayed evidence_id: {evidence_id}")
    seen_evidence_ids.add(evidence_id)

    issued_at = parse_timestamp(request_execution.get("issued_at"), "request.execution.issued_at")
    collected_at = parse_timestamp(result.get("collected_at"), "collected_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if collected_at < issued_at:
        raise EvidenceIntegrityError("evidence predates the current invocation")
    if collected_at > current + timedelta(seconds=5):
        raise EvidenceIntegrityError("evidence collection timestamp is in the future")
    max_age_seconds = int(request_execution.get("max_age_seconds", 600))
    if current - collected_at > timedelta(seconds=max_age_seconds):
        raise EvidenceIntegrityError("evidence is stale for the current invocation")

    scenario = _mapping(result.get("scenario"), "scenario")
    _true(scenario.get("action_executed"), "scenario.action_executed")
    _true(scenario.get("preconditions_satisfied"), "scenario.preconditions_satisfied")
    _true(scenario.get("generated_case_applied"), "scenario.generated_case_applied")
    _exact(
        scenario.get("applied_case_sha256"),
        request["case_sha256"],
        "scenario.applied_case_sha256",
    )
    validate_observation_records(
        result.get("observations"), issued_at=issued_at, collected_at=collected_at
    )
    return issued_at, collected_at


def _probe_loss_percent(samples: Any, path: str) -> float:
    records = _list(samples, path)
    if not records:
        raise EvidenceIntegrityError(f"{path} must not be empty")
    successes = 0
    for index, sample in enumerate(records):
        item = _mapping(sample, f"{path}[{index}]")
        if not isinstance(item.get("success"), bool):
            raise EvidenceIntegrityError(f"{path}[{index}].success must be boolean")
        successes += int(item["success"])
    return 100.0 * (len(records) - successes) / len(records)


def _validate_signal_raw(
    signal: dict[str, Any],
    *,
    family: str,
    case: dict[str, Any],
    path: str,
) -> None:
    kind = signal["kind"]
    raw = _mapping(signal.get("raw"), f"{path}.raw")
    if kind == "pod_uid_transition":
        before = set(_list(raw.get("before_uids"), f"{path}.raw.before_uids"))
        active = set(_list(raw.get("active_uids"), f"{path}.raw.active_uids"))
        if not before or before == active:
            raise EvidenceIntegrityError(f"{path} does not prove a pod UID transition")
    elif kind == "container_restart_transition":
        before = _mapping(raw.get("before"), f"{path}.raw.before")
        active = _mapping(raw.get("active"), f"{path}.raw.active")
        if not before or not any(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > int(before.get(key, -1))
            for key, value in active.items()
        ):
            raise EvidenceIntegrityError(f"{path} does not prove a container restart increment")
    elif kind == "network_probe":
        baseline = _list(raw.get("baseline"), f"{path}.raw.baseline")
        active = _list(raw.get("active"), f"{path}.raw.active")
        baseline_loss = _probe_loss_percent(baseline, f"{path}.raw.baseline")
        active_loss = _probe_loss_percent(active, f"{path}.raw.active")
        if baseline_loss >= 100:
            raise EvidenceIntegrityError(f"{path} baseline never reached the target")
        if family == "network_partition" and active_loss <= baseline_loss:
            raise EvidenceIntegrityError(f"{path} does not prove partition impairment")
        if family == "packet_loss":
            requested = float(case.get("network_loss_percent", 1))
            minimum_effect = max(2.0, requested * 0.25)
            if baseline_loss != 0 or active_loss < minimum_effect:
                raise EvidenceIntegrityError(
                    f"{path} measured {active_loss}% loss without a clean baseline or below "
                    f"the {minimum_effect}% physical-effect threshold for generated {requested}%"
                )
        if family == "network_latency":
            baseline_latency = [
                float(item["latency_ms"])
                for item in baseline
                if item.get("success") is True and isinstance(item.get("latency_ms"), (int, float))
            ]
            active_latency = [
                float(item["latency_ms"])
                for item in active
                if item.get("success") is True and isinstance(item.get("latency_ms"), (int, float))
            ]
            if not baseline_latency or not active_latency:
                raise EvidenceIntegrityError(f"{path} lacks successful latency samples")
            requested = float(case.get("network_latency_ms", 1))
            if median(active_latency) - median(baseline_latency) < requested * 0.5:
                raise EvidenceIntegrityError(f"{path} does not prove the generated latency range")
        if family == "packet_duplication":
            baseline_duplicates = int(raw.get("baseline_duplicates", 0))
            active_duplicates = int(raw.get("active_duplicates", 0))
            if active_duplicates <= baseline_duplicates:
                raise EvidenceIntegrityError(f"{path} does not prove packet duplication")
    elif kind == "dns_probe":
        baseline = _mapping(raw.get("baseline"), f"{path}.raw.baseline")
        active = _mapping(raw.get("active"), f"{path}.raw.active")
        if baseline.get("success") is not True or active.get("success") is not False:
            raise EvidenceIntegrityError(f"{path} does not prove a DNS regression from baseline")
        if not isinstance(active.get("error"), str) or not active["error"]:
            raise EvidenceIntegrityError(f"{path} lacks the observed DNS error")
    elif kind == "resource_pressure_probe":
        baseline = raw.get("baseline")
        active = raw.get("active")
        if not isinstance(baseline, (int, float)) or not isinstance(active, (int, float)):
            raise EvidenceIntegrityError(f"{path} pressure samples must be numeric")
        if active <= baseline:
            raise EvidenceIntegrityError(f"{path} does not prove increased resource pressure")
    elif kind == "node_transition":
        if raw.get("unschedulable") is not True:
            raise EvidenceIntegrityError(f"{path} does not prove the node became unschedulable")
        if not _list(raw.get("moved_pod_uids"), f"{path}.raw.moved_pod_uids"):
            raise EvidenceIntegrityError(f"{path} does not prove target pod movement")
    elif kind == "kubernetes_event":
        if not isinstance(raw.get("reason"), str) or not raw["reason"]:
            raise EvidenceIntegrityError(f"{path} event reason is required")
        if not isinstance(raw.get("involved_object_uid"), str) or not raw["involved_object_uid"]:
            raise EvidenceIntegrityError(f"{path} event target UID is required")
    elif kind == "network_policy_transition":
        resource = _mapping(raw.get("resource"), f"{path}.raw.resource")
        if resource.get("kind") != "NetworkPolicy" or not resource.get("metadata", {}).get("uid"):
            raise EvidenceIntegrityError(f"{path} lacks a created NetworkPolicy resource")
    elif kind == "experiment_log":
        lines = _list(raw.get("lines"), f"{path}.raw.lines")
        if not any(isinstance(line, str) and ("inject" in line.lower() or "chaos" in line.lower()) for line in lines):
            raise EvidenceIntegrityError(f"{path} has no injection log line")
    else:
        raise EvidenceIntegrityError(f"{path}.kind is not a recognized physical effect signal: {kind!r}")


def _distinct_signal_sources(
    signals: Iterable[Any],
    *,
    family: str,
    case: dict[str, Any],
) -> tuple[set[str], list[datetime], set[str]]:
    sources: set[str] = set()
    timestamps: list[datetime] = []
    kinds: set[str] = set()
    for index, raw_signal in enumerate(signals):
        signal = _mapping(raw_signal, f"fault.effect_signals[{index}]")
        source = signal.get("source")
        if not isinstance(source, str) or not source:
            raise EvidenceIntegrityError(f"fault.effect_signals[{index}].source is required")
        if not isinstance(signal.get("kind"), str) or not signal["kind"]:
            raise EvidenceIntegrityError(f"fault.effect_signals[{index}].kind is required")
        observed_at = parse_timestamp(
            signal.get("observed_at"), f"fault.effect_signals[{index}].observed_at"
        )
        _validate_signal_raw(
            signal,
            family=family,
            case=case,
            path=f"fault.effect_signals[{index}]",
        )
        sources.add(source)
        timestamps.append(observed_at)
        kinds.add(signal["kind"])
    return sources, timestamps, kinds


def validate_chaos_evidence(
    request: dict[str, Any],
    result: dict[str, Any],
    litmus_contract: dict[str, Any],
) -> None:
    if result.get("chaos_evidence_schema") != CHAOS_EVIDENCE_SCHEMA:
        raise EvidenceIntegrityError("chaos_evidence_schema is missing or unsupported")
    fault = _mapping(result.get("fault"), "fault")
    case = _mapping(request.get("case"), "request.case")
    _true(fault.get("injected"), "fault.injected")
    _exact(fault.get("family"), litmus_contract["fault_family"], "fault.family")
    _true(fault.get("active_during_target_window"), "fault.active_during_target_window")

    litmus = _mapping(fault.get("litmus"), "fault.litmus")
    _exact(
        litmus.get("experiment"),
        litmus_contract["fault_injection"]["experiment"],
        "fault.litmus.experiment",
    )
    if litmus.get("phase") != "Completed":
        raise EvidenceIntegrityError("Litmus experiment evidence must be Completed")
    if litmus.get("verdict") not in {"Pass", "Passed"}:
        raise EvidenceIntegrityError("Litmus ChaosResult did not pass")
    for key in ("engine_uid", "result_uid", "target_resource_version"):
        if not isinstance(litmus.get(key), str) or not litmus[key]:
            raise EvidenceIntegrityError(f"fault.litmus.{key} is required")

    engine_resource = _mapping(litmus.get("engine_resource"), "fault.litmus.engine_resource")
    result_resource = _mapping(litmus.get("result_resource"), "fault.litmus.result_resource")
    if engine_resource.get("kind") != "ChaosEngine":
        raise EvidenceIntegrityError("fault.litmus.engine_resource is not a ChaosEngine")
    if result_resource.get("kind") != "ChaosResult":
        raise EvidenceIntegrityError("fault.litmus.result_resource is not a ChaosResult")
    _exact(
        engine_resource.get("metadata", {}).get("uid"),
        litmus["engine_uid"],
        "fault.litmus.engine_resource.metadata.uid",
    )
    engine_metadata = _mapping(
        engine_resource.get("metadata"), "fault.litmus.engine_resource.metadata"
    )
    engine_labels = _mapping(
        engine_metadata.get("labels"), "fault.litmus.engine_resource.metadata.labels"
    )
    _exact(
        engine_labels.get("eve-trade.io/run-id"),
        request["execution"]["run_id"],
        "fault.litmus.engine_resource.metadata.labels.eve-trade.io/run-id",
    )
    _exact(
        engine_labels.get("eve-trade.io/contract-hash"),
        request["case_sha256"][:16],
        "fault.litmus.engine_resource.metadata.labels.eve-trade.io/contract-hash",
    )
    _exact(
        engine_labels.get("eve-trade.io/execution-id"),
        engine_metadata.get("name"),
        "fault.litmus.engine_resource.metadata.labels.eve-trade.io/execution-id",
    )
    engine_spec = _mapping(engine_resource.get("spec"), "fault.litmus.engine_resource.spec")
    _exact(engine_spec.get("engineState"), "active", "raw ChaosEngine engineState")
    _exact(engine_spec.get("annotationCheck"), "false", "raw ChaosEngine annotationCheck")
    _exact(
        engine_spec.get("chaosServiceAccount"),
        litmus_contract["fault_injection"]["service_account"],
        "raw ChaosEngine chaosServiceAccount",
    )
    appinfo = _mapping(engine_spec.get("appinfo"), "fault.litmus.engine_resource.spec.appinfo")
    _exact(
        appinfo.get("applabel"),
        litmus_contract["target"]["selector"],
        "raw ChaosEngine appinfo.applabel",
    )
    _exact(
        appinfo.get("appkind"),
        litmus_contract["target"]["kind"],
        "raw ChaosEngine appinfo.appkind",
    )
    if not isinstance(appinfo.get("appns"), str) or not appinfo["appns"]:
        raise EvidenceIntegrityError("raw ChaosEngine appinfo.appns is required")
    raw_experiments = _list(
        engine_spec.get("experiments"), "fault.litmus.engine_resource.spec.experiments"
    )
    if len(raw_experiments) != 1:
        raise EvidenceIntegrityError("raw ChaosEngine must contain exactly one experiment")
    raw_engine_experiment = _mapping(raw_experiments[0], "raw ChaosEngine experiment")
    _exact(
        raw_engine_experiment.get("name"),
        litmus_contract["fault_injection"]["experiment"],
        "raw ChaosEngine experiment name",
    )
    experiment_spec = _mapping(
        raw_engine_experiment.get("spec"), "raw ChaosEngine experiment spec"
    )
    components = _mapping(
        experiment_spec.get("components"), "raw ChaosEngine experiment components"
    )
    raw_environment = _list(
        components.get("env"), "raw ChaosEngine experiment components.env"
    )
    environment: dict[str, str] = {}
    for index, raw_variable in enumerate(raw_environment):
        variable = _mapping(
            raw_variable, f"raw ChaosEngine experiment components.env[{index}]"
        )
        name = variable.get("name")
        value = variable.get("value")
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise EvidenceIntegrityError("raw ChaosEngine environment requires string name/value")
        if name in environment:
            raise EvidenceIntegrityError(f"raw ChaosEngine environment duplicates {name}")
        environment[name] = value
    for env_name, raw_expected in litmus_contract["fault_injection"].get(
        "static_environment", {}
    ).items():
        expected = str(raw_expected).replace("${APP_NAMESPACE}", appinfo["appns"])
        _exact(environment.get(env_name), expected, f"raw ChaosEngine environment {env_name}")
    for parameter, definition in litmus_contract["generated_parameters"].items():
        litmus_env = definition.get("litmus_env")
        if litmus_env:
            if parameter not in case:
                raise EvidenceIntegrityError(
                    f"request case omits generated Litmus parameter {parameter}"
                )
            _exact(
                environment.get(litmus_env),
                str(case[parameter]),
                f"raw ChaosEngine generated environment {litmus_env}",
            )
    _exact(
        result_resource.get("metadata", {}).get("uid"),
        litmus["result_uid"],
        "fault.litmus.result_resource.metadata.uid",
    )
    result_labels = _mapping(
        result_resource.get("metadata", {}).get("labels"),
        "fault.litmus.result_resource.metadata.labels",
    )
    _exact(
        result_labels.get("chaosUID"),
        litmus["engine_uid"],
        "fault.litmus.result_resource.metadata.labels.chaosUID",
    )
    raw_status = _mapping(result_resource.get("status"), "fault.litmus.result_resource.status")
    raw_experiment = _mapping(
        raw_status.get("experimentStatus"),
        "fault.litmus.result_resource.status.experimentStatus",
    )
    _exact(raw_experiment.get("phase"), litmus["phase"], "raw Litmus experiment phase")
    _exact(raw_experiment.get("verdict"), litmus["verdict"], "raw Litmus verdict")

    target = _mapping(fault.get("target"), "fault.target")
    _exact(target.get("selector"), litmus_contract["target"]["selector"], "fault.target.selector")
    if not isinstance(target.get("resource_identity"), str) or not target["resource_identity"]:
        raise EvidenceIntegrityError("fault.target.resource_identity is required")
    selected_resources = _list(target.get("selected_resources"), "fault.target.selected_resources")
    if not selected_resources:
        raise EvidenceIntegrityError("fault.target.selected_resources must contain the selected target")
    selected_versions: set[str] = set()
    selected_namespaces: set[str] = set()
    for index, selected in enumerate(selected_resources):
        resource = _mapping(selected, f"fault.target.selected_resources[{index}]")
        metadata = _mapping(resource.get("metadata"), f"fault.target.selected_resources[{index}].metadata")
        for key in ("uid", "resourceVersion"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise EvidenceIntegrityError(
                    f"fault.target.selected_resources[{index}].metadata.{key} is required"
                )
        selected_versions.add(metadata["resourceVersion"])
        namespace = metadata.get("namespace")
        if not isinstance(namespace, str) or not namespace:
            raise EvidenceIntegrityError(
                f"fault.target.selected_resources[{index}].metadata.namespace is required"
            )
        selected_namespaces.add(namespace)
    if litmus["target_resource_version"] not in selected_versions:
        raise EvidenceIntegrityError("target_resource_version is not bound to a selected raw resource")
    if selected_namespaces != {appinfo["appns"]}:
        raise EvidenceIntegrityError("raw ChaosEngine namespace differs from selected target resources")

    signals = _list(fault.get("effect_signals"), "fault.effect_signals")
    required_sources = int(litmus_contract["fault_effect_proof"]["required_distinct_sources"])
    sources, signal_times, signal_kinds = _distinct_signal_sources(
        signals,
        family=litmus_contract["fault_family"],
        case=case,
    )
    if len(sources) < required_sources:
        raise EvidenceIntegrityError(
            f"fault effect needs {required_sources} distinct sources, got {sorted(sources)}"
        )
    if sources <= {"litmus", "chaosresult"}:
        raise EvidenceIntegrityError("ChaosResult alone cannot prove the target was affected")
    required_physical_kinds = {
        "pod_delete": {"pod_uid_transition"},
        "container_kill": {"container_restart_transition"},
        "network_partition": {"network_probe", "network_policy_transition"},
        "packet_loss": {"network_probe"},
        "packet_duplication": {"network_probe"},
        "network_latency": {"network_probe"},
        "dns_error": {"dns_probe"},
        "cpu_pressure": {"resource_pressure_probe"},
        "memory_pressure": {"resource_pressure_probe"},
        "disk_pressure": {"resource_pressure_probe"},
        "node_drain": {"node_transition"},
    }[litmus_contract["fault_family"]]
    if signal_kinds.isdisjoint(required_physical_kinds):
        raise EvidenceIntegrityError(
            "fault evidence lacks a family-specific physical effect signal: "
            f"expected one of {sorted(required_physical_kinds)}"
        )

    window = _mapping(fault.get("window"), "fault.window")
    active_at = parse_timestamp(window.get("active_at"), "fault.window.active_at")
    cleared_at = parse_timestamp(window.get("cleared_at"), "fault.window.cleared_at")
    if active_at >= cleared_at:
        raise EvidenceIntegrityError("fault activation must precede fault clearance")
    issued_at = parse_timestamp(request["execution"].get("issued_at"), "request.execution.issued_at")
    collected_at = parse_timestamp(result.get("collected_at"), "collected_at")
    if active_at < issued_at - timedelta(seconds=2) or cleared_at > collected_at + timedelta(seconds=2):
        raise EvidenceIntegrityError("fault window lies outside the current evidence invocation")
    engine_observations = [
        observation
        for observation in _list(result.get("observations"), "observations")
        if isinstance(observation, dict)
        and observation.get("kind") == "chaosengine-created"
    ]
    if len(engine_observations) != 1:
        raise EvidenceIntegrityError("chaos evidence requires one raw ChaosEngine creation observation")
    engine_observation = engine_observations[0]
    observed_engine = _mapping(
        engine_observation.get("resource"), "chaosengine-created.resource"
    )
    _exact(
        observed_engine.get("metadata", {}).get("uid"),
        litmus["engine_uid"],
        "chaosengine-created.resource.metadata.uid",
    )
    engine_created_at = parse_timestamp(
        engine_observation.get("observed_at"), "chaosengine-created.observed_at"
    )
    generated_offset = int(case.get("start_offset_ms", 0)) / 1000.0
    if active_at < engine_created_at + timedelta(seconds=generated_offset - 0.05):
        raise EvidenceIntegrityError(
            "fault activation observation does not respect generated start_offset_ms"
        )
    first_physical_signal = min(signal_times)
    if abs((active_at - first_physical_signal).total_seconds()) > 2:
        raise EvidenceIntegrityError(
            "fault.window.active_at is not derived from the first physical effect observation"
        )

    workload = _mapping(result.get("workload"), "workload")
    requests = _list(workload.get("requests"), "workload.requests")
    if "request_count" in case and len(requests) != int(case["request_count"]):
        raise EvidenceIntegrityError(
            "workload.requests count does not match the exact generated request_count"
        )
    crossing = 0
    for index, raw_request in enumerate(requests):
        item = _mapping(raw_request, f"workload.requests[{index}]")
        if not isinstance(item.get("request_id"), str) or not item["request_id"]:
            raise EvidenceIntegrityError(f"workload.requests[{index}].request_id is required")
        started = parse_timestamp(item.get("started_at"), f"workload.requests[{index}].started_at")
        finished = parse_timestamp(item.get("finished_at"), f"workload.requests[{index}].finished_at")
        if started > finished:
            raise EvidenceIntegrityError(f"workload.requests[{index}] has an inverted interval")
        if started < issued_at - timedelta(seconds=2) or finished > collected_at + timedelta(seconds=2):
            raise EvidenceIntegrityError(
                f"workload.requests[{index}] lies outside the current evidence invocation"
            )
        if started <= cleared_at and finished >= active_at:
            crossing += 1
    if crossing < 1:
        raise EvidenceIntegrityError("no workload request crossed the independently observed fault window")
    if fault.get("requests_crossing_target_window") != crossing:
        raise EvidenceIntegrityError("driver-supplied overlap count does not match raw request intervals")

    family = litmus_contract["fault_family"]
    if family in {"packet_loss", "network_latency"}:
        network_signals = [
            signal for signal in signals if signal.get("kind") == "network_probe"
        ]
        active_samples = _list(
            _mapping(network_signals[0].get("raw"), "network_probe.raw").get("active"),
            "network_probe.raw.active",
        )
        active_intervals = {
            (sample.get("started_at"), sample.get("finished_at"))
            for sample in active_samples
            if isinstance(sample, dict)
        }
        workload_intervals = {
            (item.get("started_at"), item.get("finished_at"))
            for item in requests
            if isinstance(item, dict)
        }
        if active_intervals.isdisjoint(workload_intervals):
            raise EvidenceIntegrityError(
                "generated workload is not bound to the raw effect-bearing network probe batch"
            )
    elif family == "pod_delete":
        baselines = [
            observation
            for observation in _list(result.get("observations"), "observations")
            if isinstance(observation, dict)
            and observation.get("kind") == "control-baseline"
        ]
        if len(baselines) != 1:
            raise EvidenceIntegrityError("pod-delete evidence requires one clean control baseline")
        baseline_samples = _list(
            baselines[0].get("samples"), "control-baseline.samples"
        )
        if not baseline_samples or not all(
            isinstance(sample, dict) and sample.get("success") is True
            for sample in baseline_samples
        ):
            raise EvidenceIntegrityError("pod-delete control baseline was already impaired")
        if not any(
            isinstance(item, dict)
            and isinstance(item.get("raw"), dict)
            and item["raw"].get("success") is False
            for item in requests
        ):
            raise EvidenceIntegrityError(
                "pod deletion was not observed by any generated workload request"
            )

    scenario = _mapping(result.get("scenario"), "scenario")
    applied = _mapping(scenario.get("applied_parameters"), "scenario.applied_parameters")
    for parameter in litmus_contract["generated_parameters"]:
        if parameter in case:
            _exact(applied.get(parameter), case[parameter], f"scenario.applied_parameters.{parameter}")

    recovery = _mapping(result.get("recovery"), "recovery")
    _true(recovery.get("completed"), "recovery.completed")
    recovery_started = parse_timestamp(recovery.get("started_at"), "recovery.started_at")
    completed_at = parse_timestamp(recovery.get("completed_at"), "recovery.completed_at")
    deadline_at = parse_timestamp(recovery.get("deadline_at"), "recovery.deadline_at")
    if recovery_started > completed_at:
        raise EvidenceIntegrityError("recovery.started_at is after recovery.completed_at")
    generated_deadline = int(case["recovery_deadline_seconds"])
    if abs(
        (
            deadline_at
            - (recovery_started + timedelta(seconds=generated_deadline))
        ).total_seconds()
    ) > 0.001:
        raise EvidenceIntegrityError(
            "recovery.deadline_at is not derived from generated recovery_deadline_seconds"
        )
    if completed_at > deadline_at:
        raise EvidenceIntegrityError("recovery completed after the generated deadline")
    if not isinstance(recovery.get("raw_probes"), list) or not recovery["raw_probes"]:
        raise EvidenceIntegrityError("recovery requires raw readiness/revert probes")
    recovery_probes = _list(recovery["raw_probes"], "recovery.raw_probes")
    probe_kinds: set[str] = set()
    probe_times: list[datetime] = []
    for index, raw_probe in enumerate(recovery_probes):
        probe = _mapping(raw_probe, f"recovery.raw_probes[{index}]")
        kind = probe.get("kind")
        if not isinstance(kind, str) or not kind:
            raise EvidenceIntegrityError(f"recovery.raw_probes[{index}].kind is required")
        observed_at = parse_timestamp(
            probe.get("observed_at"), f"recovery.raw_probes[{index}].observed_at"
        )
        raw = _mapping(probe.get("raw"), f"recovery.raw_probes[{index}].raw")
        if kind == "target_ready":
            desired = raw.get("desired")
            ready = raw.get("ready")
            if not isinstance(desired, int) or desired < 1 or not isinstance(ready, int) or ready < desired:
                raise EvidenceIntegrityError("target_ready probe does not prove desired readiness")
            readiness_samples = _list(
                raw.get("probe_samples"),
                f"recovery.raw_probes[{index}].raw.probe_samples",
            )
            if not readiness_samples or not all(
                isinstance(sample, dict) and sample.get("success") is True
                for sample in readiness_samples
            ):
                raise EvidenceIntegrityError("target_ready probe has an unsuccessful application sample")
        elif kind == "fault_absent":
            remaining = _list(
                raw.get("remaining_chaos_resources"),
                f"recovery.raw_probes[{index}].raw.remaining_chaos_resources",
            )
            if remaining or raw.get("target_effect_active") is not False:
                raise EvidenceIntegrityError("fault_absent probe still observes chaos resources/effect")
            post_cleanup = _list(
                raw.get("post_cleanup_probe_samples"),
                f"recovery.raw_probes[{index}].raw.post_cleanup_probe_samples",
            )
            minimum_samples = 64 if family == "packet_loss" else 8
            if len(post_cleanup) < minimum_samples:
                raise EvidenceIntegrityError(
                    f"fault_absent probe needs at least {minimum_samples} post-cleanup samples"
                )
            recovered_latencies: list[float] = []
            for sample_index, raw_sample in enumerate(post_cleanup):
                sample_path = (
                    f"recovery.raw_probes[{index}].raw.post_cleanup_probe_samples[{sample_index}]"
                )
                sample = _mapping(raw_sample, sample_path)
                if sample.get("success") is not True:
                    raise EvidenceIntegrityError(
                        "fault_absent post-cleanup application probe was unsuccessful"
                    )
                latency = sample.get("latency_ms")
                if not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0:
                    raise EvidenceIntegrityError(f"{sample_path}.latency_ms must be nonnegative")
                recovered_latencies.append(float(latency))
                sample_started = parse_timestamp(sample.get("started_at"), f"{sample_path}.started_at")
                sample_finished = parse_timestamp(sample.get("finished_at"), f"{sample_path}.finished_at")
                if sample_started > sample_finished:
                    raise EvidenceIntegrityError(f"{sample_path} has an inverted interval")
                if sample_started < issued_at - timedelta(seconds=2) or sample_finished > observed_at + timedelta(seconds=2):
                    raise EvidenceIntegrityError(
                        f"{sample_path} lies outside the post-cleanup observation window"
                    )
            target_state = _mapping(
                raw.get("target_state"),
                f"recovery.raw_probes[{index}].raw.target_state",
            )
            desired = target_state.get("desired")
            ready = target_state.get("ready")
            if not isinstance(desired, int) or desired < 1 or not isinstance(ready, int) or ready < desired:
                raise EvidenceIntegrityError("fault_absent target state is not fully ready")
            if family == "network_latency":
                network_signal = next(
                    signal for signal in signals if signal.get("kind") == "network_probe"
                )
                baseline_samples = _list(
                    _mapping(network_signal.get("raw"), "network_probe.raw").get("baseline"),
                    "network_probe.raw.baseline",
                )
                baseline_latencies = [
                    float(sample["latency_ms"])
                    for sample in baseline_samples
                    if isinstance(sample, dict)
                    and sample.get("success") is True
                    and isinstance(sample.get("latency_ms"), (int, float))
                    and not isinstance(sample.get("latency_ms"), bool)
                ]
                if not baseline_latencies:
                    raise EvidenceIntegrityError("latency cleanup proof has no control baseline")
                tolerated_delta = max(
                    50.0,
                    float(case.get("network_latency_ms", 1)) * 0.25,
                )
                if median(recovered_latencies) - median(baseline_latencies) > tolerated_delta:
                    raise EvidenceIntegrityError(
                        "fault_absent probes show residual injected network latency"
                    )
        else:
            raise EvidenceIntegrityError(f"unknown recovery probe kind: {kind!r}")
        probe_kinds.add(kind)
        probe_times.append(observed_at)
    if not {"target_ready", "fault_absent"}.issubset(probe_kinds):
        raise EvidenceIntegrityError("recovery requires target_ready and fault_absent raw probes")
    if abs((cleared_at - max(probe_times)).total_seconds()) > 2:
        raise EvidenceIntegrityError("fault.window.cleared_at is not bound to recovery probes")
    if abs((completed_at - max(probe_times)).total_seconds()) > 2:
        raise EvidenceIntegrityError("recovery.completed_at is not bound to recovery probes")
