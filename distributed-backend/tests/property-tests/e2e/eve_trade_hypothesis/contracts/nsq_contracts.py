from __future__ import annotations

"""Exact NSQ topic, delivery, durability, timeout, and transport bindings."""

import ast
import copy
import importlib.util
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class NSQContractSpec:
    oracle: str
    topic_symbol: str | None = None
    capabilities: tuple[str, ...] = ("structured_go", "structured_json", "structured_yaml")


def _spec(oracle: str, topic_symbol: str | None = None, *capabilities: str) -> NSQContractSpec:
    return NSQContractSpec(
        oracle=oracle,
        topic_symbol=topic_symbol,
        capabilities=capabilities or ("structured_go", "structured_json", "structured_yaml"),
    )


NSQ_CONTRACTS: dict[str, NSQContractSpec] = {
    "test_settlement_work_topic_name_matches_market_publisher_and_worker_subscriber_configuration": _spec(
        "topic_binding", "WorkTopic"
    ),
    "test_settlement_result_topic_name_matches_worker_publisher_and_market_subscriber_configuration": _spec(
        "topic_binding", "ResultTopic"
    ),
    "test_settlement_worker_uses_non_ephemeral_channel_for_correctness_critical_work": _spec(
        "durable_channel", "WorkTopic"
    ),
    "test_settlement_result_consumer_uses_non_ephemeral_channel_for_correctness_critical_results": _spec(
        "durable_channel", "ResultTopic"
    ),
    "test_nsqd_restart_preserves_unacknowledged_correctness_critical_messages_under_configured_storage_mode": _spec(
        "restart_delivery_canary",
        None,
        "disk_only_queue",
        "persistent_volume",
        "real_nsqd_restart",
        "raw_protocol_observation",
        "dagger_execution",
    ),
    "test_worker_max_in_flight_does_not_exceed_processing_capacity_that_preserves_database_lock_slo": _spec(
        "worker_capacity", "WorkTopic", "structured_go", "structured_toml", "startup_guard"
    ),
    "test_message_requeue_delay_is_at_least_configured_minimum_retry_backoff": _spec(
        "minimum_requeue", None, "structured_go", "retry_policy"
    ),
    "test_message_requeue_delay_does_not_exceed_configured_maximum_retry_backoff": _spec(
        "maximum_requeue", None, "structured_go", "structured_yaml", "retry_policy"
    ),
    "test_nsqd_message_timeout_exceeds_normal_worker_processing_deadline": _spec(
        "message_timeout", "WorkTopic", "structured_go", "structured_yaml", "timeout_relation"
    ),
    "test_worker_extends_or_finishes_message_before_nsqd_timeout_for_long_running_settlement": _spec(
        "bounded_processing", "WorkTopic", "structured_go", "grpc_deadline", "timeout_relation"
    ),
    "test_nsqd_configuration_rejects_ephemeral_channel_name_for_settlement_work": _spec(
        "ephemeral_rejection", "WorkTopic", "structured_json", "hostile_mutation"
    ),
    "test_nsqd_configuration_rejects_ephemeral_channel_name_for_settlement_results": _spec(
        "ephemeral_rejection", "ResultTopic", "structured_json", "hostile_mutation"
    ),
    "test_nsq_auth_or_tls_configuration_is_consistent_between_publishers_consumers_and_broker_when_enabled": _spec(
        "transport_security", None, "structured_json", "structured_yaml", "mtls_hop_model"
    ),
}


def binding_metadata(spec: NSQContractSpec) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "family": "nsq_delivery",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }
    if spec.topic_symbol:
        metadata["parameters"] = {"topic_symbol": spec.topic_symbol}
    return metadata


@lru_cache(maxsize=4)
def _validator(path_text: str) -> ModuleType:
    path = Path(path_text)
    name = "eve_trade_validate_nsq_configuration"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot import {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contains_token_sequence(module: ModuleType, source: str, expected: Sequence[str]) -> bool:
    values = [token.value for token in module._go_tokens(source)]
    width = len(expected)
    return any(values[index : index + width] == list(expected) for index in range(len(values) - width + 1))


def _binding(state: Any, module: ModuleType, symbol: str) -> tuple[str, str, Any]:
    topic_name, channel = module.EXPECTED_BINDINGS[symbol]
    assert state.topics.get(symbol) == topic_name
    assert f"settlement.{symbol}" in state.topic_references
    subscription = state.subscriptions.get(channel)
    assert subscription is not None
    assert subscription.topic_symbol == f"settlement.{symbol}"
    assert subscription.channel == channel
    for backend in (state.production_backend, state.local_backend):
        module.validate_backend_bindings(backend)
        topic = backend["topics"][topic_name]
        assert topic["name"] == topic_name
        assert topic["subscriptions"][channel]["name"] == channel
    return topic_name, channel, subscription


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1, f"expected exactly one function {name}"
    return matches[0]


def _call_attribute(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _assert_restart_canary_plan(root: Path, module: ModuleType) -> None:
    dagger_path = root / ".github/dagger/kubernetes.py"
    dagger_tree = ast.parse(dagger_path.read_text(encoding="utf-8"), filename=str(dagger_path))
    imported_images = {
        alias.name
        for node in dagger_tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "_images"
        for alias in node.names
    }
    image_path = root / ".github/dagger/_images.py"
    image_tree = ast.parse(image_path.read_text(encoding="utf-8"), filename=str(image_path))
    assignments = {
        target.id: node.value.value
        for node in ast.walk(image_tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    assert "NSQ_IMAGE" in imported_images
    assert assignments.get("NSQ_IMAGE") == module.NSQ_IMAGE
    run = _function(dagger_tree, "run")
    literals = {
        node.value
        for node in ast.walk(run)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "/nsqd" in literals
    assert "/usr/local/bin/nsqd" in literals
    assert "python scripts/validate_nsq_configuration.py" in literals
    assert "python scripts/verify_nsq_restart_delivery.py --nsqd /usr/local/bin/nsqd" in literals
    attributes = {_call_attribute(node) for node in ast.walk(run)}
    assert {"from_", "file", "with_file", "sync"} <= attributes

    canary_path = root / "scripts/verify_nsq_restart_delivery.py"
    canary_tree = ast.parse(canary_path.read_text(encoding="utf-8"), filename=str(canary_path))
    verify = _function(canary_tree, "verify_restart_delivery")
    calls = [
        node.func.id
        for node in ast.walk(verify)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("_start_nsqd") == 2
    assert calls.count("receive_one") == 2
    assert calls.count("_stop_nsqd") == 2
    assert calls.count("publish") == 1
    assert calls.count("finish") == 1
    compared_attributes = {
        node.attr
        for node in ast.walk(verify)
        if isinstance(node, ast.Attribute)
        and node.attr in {"body", "message_id", "attempts"}
    }
    assert compared_attributes == {"body", "message_id", "attempts"}


def _ephemeral_backend(state: Any, module: ModuleType, symbol: str) -> Mapping[str, Any]:
    topic_name, channel = module.EXPECTED_BINDINGS[symbol]
    backend = copy.deepcopy(state.production_backend)
    subscriptions = backend["topics"][topic_name]["subscriptions"]
    subscription = subscriptions.pop(channel)
    ephemeral = channel + "#ephemeral"
    subscription["name"] = ephemeral
    subscriptions[ephemeral] = subscription
    return backend


def validate_nsq_contract(name: str, root: Path) -> None:
    assert name in NSQ_CONTRACTS, f"unknown NSQ semantic contract: {name}"
    spec = NSQ_CONTRACTS[name]
    module = _validator(str(root / "scripts/validate_nsq_configuration.py"))
    state = module.load_repository_state(root)

    if spec.oracle in {"topic_binding", "durable_channel"}:
        assert spec.topic_symbol is not None
        topic_name, channel, _ = _binding(state, module, spec.topic_symbol)
        if spec.oracle == "durable_channel":
            assert "#ephemeral" not in topic_name.lower()
            assert "#ephemeral" not in channel.lower()
        return

    if spec.oracle == "restart_delivery_canary":
        required = {
            "data-path": "/data",
            "mem-queue-size": "0",
            "sync-every": "1",
            "sync-timeout": "1s",
        }
        assert all(state.base_flags.get(key) == value for key, value in required.items())
        container = module._nsqd_container(state.base_nsqd)
        mounts = {item["name"]: item["mountPath"] for item in container["volumeMounts"]}
        claims = {
            item["metadata"]["name"]
            for item in state.base_nsqd["spec"]["volumeClaimTemplates"]
        }
        assert mounts.get("data") == "/data" and "data" in claims
        _assert_restart_canary_plan(root, module)
        return

    if spec.oracle == "worker_capacity":
        _, _, worker = _binding(state, module, "WorkTopic")
        assert worker.max_concurrency > 0
        assert worker.max_concurrency + state.worker_connection_reserve <= state.database_max_connections
        service_source = (root / "distributed-backend/src/settlementworker/service.go").read_text(encoding="utf-8")
        assert _contains_token_sequence(
            module,
            service_source,
            (
                "if",
                "settlementWorkerMaxInFlight",
                "+",
                "settlementWorkerDBConnectionReserve",
                ">",
                "cfg",
                ".",
                "TradeSettlementMaxConnections",
                "{",
            ),
        )
        regression = (root / "distributed-backend/src/settlementworker/regression_lifecycle_test.go").read_text(encoding="utf-8")
        assert _contains_token_sequence(
            module,
            regression,
            ("t", ".", "Run", "(", "test_worker_startup_rejects_max_in_flight_above_database_capacity_reserve"),
        )
        return

    if spec.oracle == "minimum_requeue":
        assert state.subscriptions
        for subscription in state.subscriptions.values():
            assert 0 < subscription.min_backoff_ns <= subscription.max_backoff_ns
        return

    if spec.oracle == "maximum_requeue":
        broker_maximum = module.parse_duration_ns(state.base_flags["max-req-timeout"])
        assert state.local_flags["max-req-timeout"] == state.base_flags["max-req-timeout"]
        for subscription in state.subscriptions.values():
            assert subscription.min_backoff_ns <= subscription.max_backoff_ns <= broker_maximum
        return

    if spec.oracle == "message_timeout":
        _, _, worker = _binding(state, module, "WorkTopic")
        broker_timeout = module.parse_duration_ns(state.base_flags["msg-timeout"])
        assert state.local_flags["msg-timeout"] == state.base_flags["msg-timeout"]
        assert broker_timeout > worker.ack_deadline_ns > state.worker_request_timeout_ns > 0
        return

    if spec.oracle == "bounded_processing":
        _, _, worker = _binding(state, module, "WorkTopic")
        broker_timeout = module.parse_duration_ns(state.base_flags["msg-timeout"])
        assert state.worker_request_timeout_ns < worker.ack_deadline_ns < broker_timeout
        service_source = (root / "distributed-backend/src/settlementworker/service.go").read_text(encoding="utf-8")
        client_source = (root / "distributed-backend/src/settlementworker/client.go").read_text(encoding="utf-8")
        assert _contains_token_sequence(
            module,
            service_source,
            ("NewGRPCSettlementExecutor", "(", "cfg", ".", "TradeSettlementTarget", ",", "cfg", ".", "RequestTimeout", ")"),
        )
        assert _contains_token_sequence(
            module,
            client_source,
            ("return", "context", ".", "WithTimeout", "(", "parent", ",", "e", ".", "timeout", ")"),
        )
        return

    if spec.oracle == "ephemeral_rejection":
        assert spec.topic_symbol is not None
        _binding(state, module, spec.topic_symbol)
        hostile = _ephemeral_backend(state, module, spec.topic_symbol)
        try:
            module.validate_backend_bindings(hostile)
        except module.NSQConfigurationError:
            return
        raise AssertionError(f"NSQ validator accepted ephemeral {spec.topic_symbol} channel")

    if spec.oracle == "transport_security":
        module.validate_backend_bindings(state.production_backend)
        module.validate_backend_bindings(state.local_backend)
        assert state.production_backend["hosts"] == "127.0.0.1:4150"
        assert state.production_backend["tls"]["required"] is False
        assert state.production_backend["authentication"]["required"] is False
        module.validate_production_proxy(root)
        assert state.base_flags["tls-required"] == "true"
        assert state.base_flags["tls-client-auth-policy"] == "require-verify"
        assert state.local_backend["tls"]["required"] is False
        assert state.local_backend["authentication"]["required"] is False
        assert not any(key.startswith("tls-") for key in state.local_flags)
        return

    raise AssertionError(f"unhandled NSQ oracle: {spec.oracle}")
