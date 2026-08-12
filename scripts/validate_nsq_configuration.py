#!/usr/bin/env python3
from __future__ import annotations

"""Validate the repository's NSQ delivery contract from structured sources.

The Go reader below is a deliberately small lexer/parser for the declarations
used by Encore Pub/Sub.  It follows balanced Go syntax and composite-literal
fields; it does not infer semantics from repository-wide keyword presence.
"""

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BINDINGS = {
    "WorkTopic": ("settlement-work", "trade-settlement-executor"),
    "ResultTopic": ("settlement-results", "market-settlement-result-projection"),
}
NSQ_IMAGE = "nsqio/nsq@sha256:1ceea517773270f5dd75f92777d4f7a49d8728b3884e28c749524ad6b031ca18"


class NSQConfigurationError(AssertionError):
    pass


@dataclass(frozen=True)
class GoToken:
    kind: str
    value: str


@dataclass(frozen=True)
class Subscription:
    topic_symbol: str
    channel: str
    max_concurrency: int
    ack_deadline_ns: int
    min_backoff_ns: int
    max_backoff_ns: int
    max_retries: int


@dataclass(frozen=True)
class NSQRepositoryState:
    topics: Mapping[str, str]
    topic_references: frozenset[str]
    subscriptions: Mapping[str, Subscription]
    production_backend: Mapping[str, Any]
    local_backend: Mapping[str, Any]
    base_nsqd: Mapping[str, Any]
    local_nsqd_patch: Mapping[str, Any]
    base_flags: Mapping[str, str]
    local_flags: Mapping[str, str]
    database_max_connections: int
    worker_connection_reserve: int
    worker_request_timeout_ns: int


def _go_tokens(source: str) -> list[GoToken]:
    tokens: list[GoToken] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            index = length if end < 0 else end + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise NSQConfigurationError("unterminated Go block comment")
            index = end + 2
            continue
        if char == '"':
            end = index + 1
            escaped = False
            while end < length:
                current = source[end]
                if current == '"' and not escaped:
                    break
                if current == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                end += 1
            if end >= length:
                raise NSQConfigurationError("unterminated Go string")
            raw = source[index : end + 1]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise NSQConfigurationError(f"unsupported Go string literal {raw!r}") from exc
            tokens.append(GoToken("string", value))
            index = end + 1
            continue
        if char == "`":
            end = source.find("`", index + 1)
            if end < 0:
                raise NSQConfigurationError("unterminated raw Go string")
            tokens.append(GoToken("string", source[index + 1 : end]))
            index = end + 1
            continue
        if char.isalpha() or char == "_":
            end = index + 1
            while end < length and (source[end].isalnum() or source[end] == "_"):
                end += 1
            tokens.append(GoToken("ident", source[index:end]))
            index = end
            continue
        if char.isdigit():
            end = index + 1
            while end < length and (source[end].isalnum() or source[end] == "_"):
                end += 1
            tokens.append(GoToken("number", source[index:end].replace("_", "")))
            index = end
            continue
        tokens.append(GoToken("punct", char))
        index += 1
    return tokens


def _values(tokens: Sequence[GoToken]) -> list[str]:
    return [token.value for token in tokens]


def _matching_index(tokens: Sequence[GoToken], start: int) -> int:
    pairs = {"(": ")", "[": "]", "{": "}"}
    opening = tokens[start].value
    if opening not in pairs:
        raise NSQConfigurationError(f"expected balanced delimiter, got {opening!r}")
    stack = [pairs[opening]]
    for index in range(start + 1, len(tokens)):
        value = tokens[index].value
        if value in pairs:
            stack.append(pairs[value])
        elif stack and value == stack[-1]:
            stack.pop()
            if not stack:
                return index
    raise NSQConfigurationError(f"unterminated {opening!r} group")


def _split_top_level(tokens: Sequence[GoToken], separator: str = ",") -> list[list[GoToken]]:
    groups: list[list[GoToken]] = []
    current: list[GoToken] = []
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for token in tokens:
        value = token.value
        if value in pairs:
            stack.append(pairs[value])
        elif stack and value == stack[-1]:
            stack.pop()
        if value == separator and not stack:
            if current:
                groups.append(current)
            current = []
        else:
            current.append(token)
    if current:
        groups.append(current)
    return groups


def _find_top_level(tokens: Sequence[GoToken], value: str) -> int:
    stack: list[str] = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    for index, token in enumerate(tokens):
        current = token.value
        if current == value and not stack:
            return index
        if current in pairs:
            stack.append(pairs[current])
        elif stack and current == stack[-1]:
            stack.pop()
    return -1


def _call_arguments(tokens: Sequence[GoToken], index: int, selector: Sequence[str]) -> tuple[list[list[GoToken]], int] | None:
    values = _values(tokens)
    if values[index : index + len(selector)] != list(selector):
        return None
    cursor = index + len(selector)
    if cursor < len(tokens) and tokens[cursor].value == "[":
        cursor = _matching_index(tokens, cursor) + 1
    if cursor >= len(tokens) or tokens[cursor].value != "(":
        return None
    end = _matching_index(tokens, cursor)
    return _split_top_level(tokens[cursor + 1 : end]), end


def _constant_expressions(source: str) -> dict[str, list[GoToken]]:
    constants: dict[str, list[GoToken]] = {}
    in_group = False
    for raw_line in source.splitlines():
        tokens = _go_tokens(raw_line)
        values = _values(tokens)
        if not values:
            continue
        if values[:2] == ["const", "("]:
            in_group = True
            continue
        if in_group and values == [")"]:
            in_group = False
            continue
        declaration = tokens
        if values[0] == "const":
            declaration = tokens[1:]
        elif not in_group:
            continue
        equals = _find_top_level(declaration, "=")
        if equals <= 0 or declaration[0].kind != "ident":
            continue
        constants[declaration[0].value] = list(declaration[equals + 1 :])
    return constants


_TIME_UNITS_NS = {
    "Nanosecond": 1,
    "Microsecond": 1_000,
    "Millisecond": 1_000_000,
    "Second": 1_000_000_000,
    "Minute": 60_000_000_000,
    "Hour": 3_600_000_000_000,
}


def _strip_parentheses(tokens: Sequence[GoToken]) -> list[GoToken]:
    result = list(tokens)
    while result and result[0].value == "(" and _matching_index(result, 0) == len(result) - 1:
        result = result[1:-1]
    return result


def _evaluate_go_number(
    tokens: Sequence[GoToken],
    constants: Mapping[str, Sequence[GoToken]],
    resolving: frozenset[str] = frozenset(),
) -> int:
    expression = _strip_parentheses(tokens)
    if not expression:
        raise NSQConfigurationError("empty Go numeric expression")
    for operators in (("+", "-"), ("*", "/")):
        for index in range(len(expression) - 1, -1, -1):
            if expression[index].value not in operators:
                continue
            if _find_top_level(expression[: index + 1], expression[index].value) != index:
                continue
            left = _evaluate_go_number(expression[:index], constants, resolving)
            right = _evaluate_go_number(expression[index + 1 :], constants, resolving)
            if expression[index].value == "+":
                return left + right
            if expression[index].value == "-":
                return left - right
            if expression[index].value == "*":
                return left * right
            if right == 0 or left % right:
                raise NSQConfigurationError("non-integral Go duration division")
            return left // right
    values = _values(expression)
    if len(expression) == 1 and expression[0].kind == "number":
        return int(expression[0].value, 0)
    if len(expression) == 1 and expression[0].kind == "ident":
        name = expression[0].value
        if name not in constants or name in resolving:
            raise NSQConfigurationError(f"unresolved Go constant {name!r}")
        return _evaluate_go_number(constants[name], constants, resolving | {name})
    if len(values) == 3 and values[:2] == ["time", "."] and values[2] in _TIME_UNITS_NS:
        return _TIME_UNITS_NS[values[2]]
    raise NSQConfigurationError(f"unsupported Go numeric expression: {' '.join(values)}")


def _composite_fields(tokens: Sequence[GoToken]) -> dict[str, list[GoToken]]:
    opening = next((index for index, token in enumerate(tokens) if token.value == "{"), -1)
    if opening < 0:
        raise NSQConfigurationError("expected Go composite literal")
    closing = _matching_index(tokens, opening)
    fields: dict[str, list[GoToken]] = {}
    for group in _split_top_level(tokens[opening + 1 : closing]):
        colon = _find_top_level(group, ":")
        if colon != 1 or group[0].kind != "ident":
            raise NSQConfigurationError(f"unsupported Go composite field: {' '.join(_values(group))}")
        fields[group[0].value] = list(group[colon + 1 :])
    return fields


def parse_go_topics(source: str) -> dict[str, str]:
    tokens = _go_tokens(source)
    topics: dict[str, str] = {}
    for index, token in enumerate(tokens):
        if token.value != "var" or index + 3 >= len(tokens):
            continue
        symbol = tokens[index + 1]
        if symbol.kind != "ident" or tokens[index + 2].value != "=":
            continue
        call = _call_arguments(tokens, index + 3, ("pubsub", ".", "NewTopic"))
        if call is None:
            continue
        arguments, _ = call
        if len(arguments) != 2 or len(arguments[0]) != 1 or arguments[0][0].kind != "string":
            raise NSQConfigurationError(f"topic {symbol.value} must use one literal name")
        fields = _composite_fields(arguments[1])
        if _values(fields.get("DeliveryGuarantee", [])) != ["pubsub", ".", "AtLeastOnce"]:
            raise NSQConfigurationError(f"topic {symbol.value} is not at-least-once")
        ordering = fields.get("OrderingAttribute", [])
        if len(ordering) != 1 or ordering[0].kind != "string" or ordering[0].value != "idempotency-key":
            raise NSQConfigurationError(f"topic {symbol.value} lacks the idempotency ordering attribute")
        topics[symbol.value] = arguments[0][0].value
    return topics


def _selector_name(tokens: Sequence[GoToken]) -> str:
    values = _values(tokens)
    if not values or any(value not in {"."} and token.kind != "ident" for value, token in zip(values, tokens)):
        raise NSQConfigurationError(f"expected Go selector, got {' '.join(values)}")
    return "".join(values)


def parse_go_topic_references(source: str) -> frozenset[str]:
    tokens = _go_tokens(source)
    references: set[str] = set()
    for index in range(len(tokens)):
        call = _call_arguments(tokens, index, ("pubsub", ".", "TopicRef"))
        if call is None:
            continue
        arguments, _ = call
        if len(arguments) != 1:
            raise NSQConfigurationError("TopicRef must have exactly one topic argument")
        references.add(_selector_name(arguments[0]))
    return frozenset(references)


def parse_go_subscriptions(source: str) -> dict[str, Subscription]:
    tokens = _go_tokens(source)
    constants = _constant_expressions(source)
    subscriptions: dict[str, Subscription] = {}
    for index in range(len(tokens)):
        call = _call_arguments(tokens, index, ("pubsub", ".", "NewSubscription"))
        if call is None:
            continue
        arguments, _ = call
        if len(arguments) != 3 or len(arguments[1]) != 1 or arguments[1][0].kind != "string":
            raise NSQConfigurationError("subscription must use a literal channel and inline configuration")
        fields = _composite_fields(arguments[2])
        retry_fields = _composite_fields(fields.get("RetryPolicy", []))
        channel = arguments[1][0].value
        subscriptions[channel] = Subscription(
            topic_symbol=_selector_name(arguments[0]),
            channel=channel,
            max_concurrency=_evaluate_go_number(fields["MaxConcurrency"], constants),
            ack_deadline_ns=_evaluate_go_number(fields["AckDeadline"], constants),
            min_backoff_ns=_evaluate_go_number(retry_fields["MinBackoff"], constants),
            max_backoff_ns=_evaluate_go_number(retry_fields["MaxBackoff"], constants),
            max_retries=_evaluate_go_number(retry_fields["MaxRetries"], constants),
        )
    return subscriptions


def parse_go_constant(source: str, name: str) -> int:
    constants = _constant_expressions(source)
    if name not in constants:
        raise NSQConfigurationError(f"missing Go constant {name}")
    return _evaluate_go_number(constants[name], constants)


def _load_exact_backend(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    backends = document.get("pubsub") if isinstance(document, dict) else None
    if not isinstance(backends, list) or len(backends) != 1 or backends[0].get("type") != "nsq":
        raise NSQConfigurationError(f"{path} must contain exactly one NSQ backend")
    return backends[0]


def validate_backend_bindings(backend: Mapping[str, Any]) -> None:
    topics = backend.get("topics")
    expected_names = {topic for topic, _ in EXPECTED_BINDINGS.values()}
    if not isinstance(topics, dict) or set(topics) != expected_names:
        raise NSQConfigurationError(f"NSQ topics must be exactly {sorted(expected_names)}")
    for topic_name, channel_name in EXPECTED_BINDINGS.values():
        topic = topics[topic_name]
        if not isinstance(topic, dict) or topic.get("name") != topic_name:
            raise NSQConfigurationError(f"topic key/name mismatch for {topic_name}")
        subscriptions = topic.get("subscriptions")
        if not isinstance(subscriptions, dict) or set(subscriptions) != {channel_name}:
            raise NSQConfigurationError(f"unexpected subscriptions for {topic_name}")
        subscription = subscriptions[channel_name]
        if not isinstance(subscription, dict) or subscription.get("name") != channel_name:
            raise NSQConfigurationError(f"subscription key/name mismatch for {channel_name}")
        for kind, value in (("topic", topic_name), ("channel", channel_name)):
            if "#ephemeral" in value.lower():
                raise NSQConfigurationError(f"correctness-critical {kind} cannot be ephemeral: {value}")


def _load_yaml_documents(path: Path) -> list[Mapping[str, Any]]:
    return [document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(document, dict)]


def _nsqd_workload(documents: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    matches = [
        document
        for document in documents
        if document.get("kind") == "StatefulSet"
        and (document.get("metadata") or {}).get("name") == "nsqd"
    ]
    if len(matches) != 1:
        raise NSQConfigurationError("expected exactly one nsqd StatefulSet")
    return matches[0]


def _nsqd_container(workload: Mapping[str, Any]) -> Mapping[str, Any]:
    containers = (((workload.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers")
    matches = [container for container in containers or [] if container.get("name") == "nsqd"]
    if len(matches) != 1:
        raise NSQConfigurationError("expected exactly one nsqd container")
    return matches[0]


def _flags(container: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for argument in container.get("args") or []:
        if not isinstance(argument, str) or not argument.startswith("--") or "=" not in argument:
            raise NSQConfigurationError(f"nsqd argument must be an explicit --name=value flag: {argument!r}")
        key, value = argument[2:].split("=", 1)
        if key in result:
            raise NSQConfigurationError(f"duplicate nsqd flag: {key}")
        result[key] = value
    return result


def parse_duration_ns(value: str) -> int:
    units = (("ms", 1_000_000), ("s", 1_000_000_000), ("m", 60_000_000_000), ("h", 3_600_000_000_000))
    for suffix, multiplier in units:
        if value.endswith(suffix):
            number = value[: -len(suffix)]
            if number.isdigit() and int(number) > 0:
                return int(number) * multiplier
    raise NSQConfigurationError(f"unsupported positive NSQ duration: {value!r}")


def load_production_proxy(root: Path = ROOT) -> Mapping[str, Any]:
    documents = _load_yaml_documents(
        root / "distributed-backend/orchestration/kubernetes/overlay/prod/nsq-client-proxy.yaml"
    )
    matches = [
        document
        for document in documents
        if document.get("kind") == "ConfigMap"
        and (document.get("metadata") or {}).get("name") == "nsq-client-proxy"
    ]
    if len(matches) != 1:
        raise NSQConfigurationError("expected exactly one production NSQ client-proxy ConfigMap")
    encoded = (matches[0].get("data") or {}).get("envoy.yaml")
    proxy = yaml.safe_load(encoded) if isinstance(encoded, str) else None
    if not isinstance(proxy, dict):
        raise NSQConfigurationError("production NSQ proxy Envoy configuration is missing")
    return proxy


def validate_production_proxy(root: Path = ROOT) -> None:
    proxy = load_production_proxy(root)
    resources = proxy.get("static_resources") or {}
    listeners = resources.get("listeners") or []
    clusters = resources.get("clusters") or []
    if len(listeners) != 1 or len(clusters) != 1:
        raise NSQConfigurationError("NSQ proxy must have exactly one listener and one upstream cluster")
    listener_socket = (listeners[0].get("address") or {}).get("socket_address") or {}
    if listener_socket.get("address") != "127.0.0.1" or listener_socket.get("port_value") != 4150:
        raise NSQConfigurationError("NSQ proxy listener must be restricted to loopback port 4150")
    cluster = clusters[0]
    endpoint = (
        (((cluster.get("load_assignment") or {}).get("endpoints") or [{}])[0].get("lb_endpoints") or [{}])[0]
        .get("endpoint", {})
        .get("address", {})
        .get("socket_address", {})
    )
    if endpoint.get("address") != "nsqd.eve-trade.svc.cluster.local" or endpoint.get("port_value") != 4150:
        raise NSQConfigurationError("NSQ proxy upstream must be the in-cluster nsqd service")
    transport = cluster.get("transport_socket") or {}
    if transport.get("name") != "envoy.transport_sockets.tls":
        raise NSQConfigurationError("NSQ proxy upstream transport must be TLS")
    common = ((transport.get("typed_config") or {}).get("common_tls_context") or {})
    if (common.get("tls_params") or {}).get("tls_minimum_protocol_version") != "TLSv1_3":
        raise NSQConfigurationError("NSQ proxy upstream must require TLS 1.3")
    certificates = common.get("tls_certificates") or []
    if len(certificates) != 1:
        raise NSQConfigurationError("NSQ proxy must present exactly one client certificate")
    certificate = certificates[0]
    if not (certificate.get("certificate_chain") or {}).get("filename"):
        raise NSQConfigurationError("NSQ proxy client certificate chain is missing")
    if not (certificate.get("private_key") or {}).get("filename"):
        raise NSQConfigurationError("NSQ proxy client private key is missing")
    validation = common.get("validation_context") or {}
    if not (validation.get("trusted_ca") or {}).get("filename"):
        raise NSQConfigurationError("NSQ proxy does not verify the broker certificate chain")
    sans = validation.get("match_typed_subject_alt_names") or []
    expected_san = "nsqd.eve-trade.svc.cluster.local"
    if not any(((item.get("matcher") or {}).get("exact") == expected_san) for item in sans):
        raise NSQConfigurationError("NSQ proxy does not verify the broker DNS identity")


def load_repository_state(root: Path = ROOT) -> NSQRepositoryState:
    topic_source = (root / "distributed-backend/src/settlement/work.go").read_text(encoding="utf-8")
    worker_source = (root / "distributed-backend/src/settlementworker/service.go").read_text(encoding="utf-8")
    worker_config_source = (root / "distributed-backend/src/settlementworker/config.go").read_text(encoding="utf-8")
    result_source = (root / "distributed-backend/src/market/settlement_result.go").read_text(encoding="utf-8")
    base_documents = _load_yaml_documents(root / "distributed-backend/orchestration/kubernetes/base/nsq.yaml")
    local_documents = _load_yaml_documents(root / "distributed-backend/orchestration/kubernetes/overlay/local/nsq-local.yaml")
    base_nsqd = _nsqd_workload(base_documents)
    local_nsqd = _nsqd_workload(local_documents)
    subscriptions = {
        **parse_go_subscriptions(worker_source),
        **parse_go_subscriptions(result_source),
    }
    rust_config = tomllib.loads(
        (root / "distributed-backend/src/trade-settlement/config/app.toml").read_text(encoding="utf-8")
    )
    return NSQRepositoryState(
        topics=parse_go_topics(topic_source),
        topic_references=parse_go_topic_references(worker_source),
        subscriptions=subscriptions,
        production_backend=_load_exact_backend(root / "infra/encore/self-host.nsq.json"),
        local_backend=_load_exact_backend(root / "infra/encore/self-host.local.nsq.json"),
        base_nsqd=base_nsqd,
        local_nsqd_patch=local_nsqd,
        base_flags=_flags(_nsqd_container(base_nsqd)),
        local_flags=_flags(_nsqd_container(local_nsqd)),
        database_max_connections=int(rust_config["sqlx"]["max_connections"]),
        worker_connection_reserve=parse_go_constant(worker_source, "settlementWorkerDBConnectionReserve"),
        worker_request_timeout_ns=parse_go_constant(worker_config_source, "settlementWorkerDefaultRequestTimeout"),
    )


def validate_repository(root: Path = ROOT) -> NSQRepositoryState:
    state = load_repository_state(root)
    if state.topics != {symbol: binding[0] for symbol, binding in EXPECTED_BINDINGS.items()}:
        raise NSQConfigurationError(f"Go topic declarations do not match reviewed bindings: {state.topics}")
    expected_refs = {f"settlement.{symbol}" for symbol in EXPECTED_BINDINGS}
    if not expected_refs <= state.topic_references:
        raise NSQConfigurationError(f"publisher TopicRef bindings missing: {expected_refs - state.topic_references}")
    expected_channels = {channel for _, channel in EXPECTED_BINDINGS.values()}
    if set(state.subscriptions) != expected_channels:
        raise NSQConfigurationError(f"Go subscriptions do not match reviewed channels: {state.subscriptions}")
    for symbol, (topic_name, channel) in EXPECTED_BINDINGS.items():
        subscription = state.subscriptions[channel]
        if subscription.topic_symbol != f"settlement.{symbol}":
            raise NSQConfigurationError(f"{channel} subscribes to {subscription.topic_symbol}, expected settlement.{symbol}")
        if subscription.min_backoff_ns <= 0 or subscription.min_backoff_ns > subscription.max_backoff_ns:
            raise NSQConfigurationError(f"invalid retry interval for {channel}")
        if subscription.max_retries <= 0:
            raise NSQConfigurationError(f"{channel} has no bounded retry budget")
    for backend in (state.production_backend, state.local_backend):
        validate_backend_bindings(backend)
    for environment, backend in (("production loopback", state.production_backend), ("local", state.local_backend)):
        if (backend.get("tls") or {}).get("required") is not False:
            raise NSQConfigurationError(f"{environment} NSQ application hop must explicitly disable TLS")
        if (backend.get("authentication") or {}).get("required") is not False:
            raise NSQConfigurationError(f"{environment} NSQ application hop must explicitly disable authentication")
    if state.production_backend.get("hosts") != "127.0.0.1:4150":
        raise NSQConfigurationError("production application must reach NSQ only through the loopback mTLS proxy")
    validate_production_proxy(root)

    required_durable_flags = {
        "data-path": "/data",
        "mem-queue-size": "0",
        "sync-every": "1",
        "sync-timeout": "1s",
        "msg-timeout": "60s",
        "max-msg-timeout": "15m",
        "max-req-timeout": "2m",
    }
    for environment, flags in (("base", state.base_flags), ("local", state.local_flags)):
        for key, expected in required_durable_flags.items():
            if flags.get(key) != expected:
                raise NSQConfigurationError(f"{environment} nsqd {key} must be {expected}")
    if state.base_flags.get("tls-required") != "true" or state.base_flags.get("tls-client-auth-policy") != "require-verify":
        raise NSQConfigurationError("production broker must require and verify client TLS certificates")
    if any(key.startswith("tls-") for key in state.local_flags):
        raise NSQConfigurationError("local nsqd patch must not retain production TLS flags")
    container = _nsqd_container(state.base_nsqd)
    if container.get("image") != NSQ_IMAGE:
        raise NSQConfigurationError("nsqd image must remain pinned to the reviewed digest")
    mounts = {item.get("name"): item.get("mountPath") for item in container.get("volumeMounts") or []}
    claims = {item.get("metadata", {}).get("name") for item in (state.base_nsqd.get("spec") or {}).get("volumeClaimTemplates") or []}
    if mounts.get("data") != "/data" or "data" not in claims:
        raise NSQConfigurationError("disk-only NSQ queues require the data PVC mounted at /data")
    return state


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        state = validate_repository(args.root.resolve())
    except (NSQConfigurationError, KeyError, TypeError, ValueError) as exc:
        print(f"NSQ_CONFIGURATION_INVALID: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "NSQ_CONFIGURATION_VALID",
                "topics": dict(state.topics),
                "channels": sorted(state.subscriptions),
                "storage_mode": "disk-only-pvc",
                "message_timeout_ns": parse_duration_ns(state.base_flags["msg-timeout"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
