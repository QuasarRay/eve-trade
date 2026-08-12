"""Real-deployment action adapters used by the Dagger scenario runner.

Adapters return factual observations only. They do not evaluate business
correctness and never emit expected/should/pass fields.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class ActionFailure(RuntimeError):
    pass


SENSITIVE_KEY = re.compile(r"(?i)(secret|password|token|private[_-]?key|credential|hmac|database_url)")
ALLOWED_PROVENANCE = {
    "SCENARIO_INPUT",
    "ANYSYSTEM_CONTROLLER",
    "DAGGER_ORCHESTRATOR",
    "LITMUS_CONTROL_PLANE",
    "KUBERNETES_CONTROL_PLANE",
    "REAL_APPLICATION",
    "REAL_DATABASE",
    "REAL_BROKER",
    "INDEPENDENT_NETWORK_PROBE",
    "INDEPENDENT_RESOURCE_PROBE",
    "PHYSICAL_CLOCK",
    "INJECTED_APPLICATION_CLOCK",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact(value: Any, key: str = "") -> Any:
    if key and SENSITIVE_KEY.search(key):
        return "<redacted:present>" if value not in (None, "") else "<redacted:empty>"
    if isinstance(value, dict):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, str):
        value = re.sub(
            r"(?i)\b(postgres(?:ql)?://[^:\s/@]+:)[^@\s/]+(@)",
            r"\1<redacted>\2",
            value,
        )
        value = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1<redacted>", value)
        return value
    return value


def fact(
    name: str,
    value: Any,
    provenance: str,
    source_ref: str,
    entity_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    if provenance not in ALLOWED_PROVENANCE:
        raise ActionFailure(f"unknown observation provenance class {provenance!r}")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ActionFailure(f"fact name has invalid syntax: {name!r}")
    if not source_ref:
        raise ActionFailure("fact source reference cannot be empty")
    if name.startswith(("expected_", "should_")) or name in {
        "business_valid",
        "invariant_satisfied",
        "test_passed",
        "correct_trade_state",
    }:
        raise ActionFailure(f"scenario adapter attempted to emit forbidden business-answer field {name}")
    return {
        "name": name,
        "value": redact(value),
        "provenance": provenance,
        "source_ref": source_ref,
        "observed_at": now(),
        "entity_ids": entity_ids or {},
    }


def run(
    argv: list[str],
    *,
    timeout: int = 120,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        argv,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    if check and process.returncode != 0:
        raise ActionFailure(
            f"bounded command failed ({process.returncode}): {argv[0]}\n"
            f"stdout={redact(process.stdout[-4096:])}\n"
            f"stderr={redact(process.stderr[-4096:])}"
        )
    return process


@dataclass(frozen=True)
class ExecutionContext:
    scenario_id: str
    scenario_revision: str
    run_id: str
    action_id: str
    namespace: str
    artifact_root: Path

    @property
    def labels(self) -> dict[str, str]:
        return {
            "eve-trade.io/run-id": self.run_id,
            "eve-trade.io/scenario-id": self.scenario_id[:63],
            "eve-trade.io/action-id": self.action_id[:63],
            "eve-trade.io/managed-by": "deterministic-emulation",
        }


class ActionExecutor:
    def __init__(self, context: ExecutionContext):
        self.context = context
        self.kubectl = os.environ.get("KUBECTL", "kubectl")

    def execute(self, specification: dict[str, Any]) -> dict[str, Any]:
        adapter = specification.get("adapter")
        request = specification.get("request")
        if not isinstance(request, dict):
            raise ActionFailure("action adapter request must be an object")
        handlers = {
            "ACTION_SEQUENCE": self.action_sequence,
            "KUBERNETES": self.kubernetes,
            "LITMUS": self.litmus,
            "HTTP_JSON": self.http_json,
            "UDP_JSON": self.udp_json,
            "POSTGRES_SQL": self.postgres_sql,
            "NSQ_HTTP": self.nsq_http,
            "OBSERVE_ONLY": self.observe_only,
        }
        try:
            handler = handlers[str(adapter)]
        except KeyError as exc:
            raise ActionFailure(f"unsupported real-deployment action adapter: {adapter!r}") from exc
        started_at = now()
        result = handler(request)
        if not isinstance(result, dict) or not isinstance(result.get("facts"), list):
            raise ActionFailure(f"{adapter} adapter returned malformed factual evidence")
        return {
            "adapter": adapter,
            "started_at": started_at,
            "finished_at": now(),
            "facts": result["facts"],
            "control_plane_evidence_refs": list(result.get("control_plane_evidence_refs", [])),
            "independent_effect_evidence_refs": list(result.get("independent_effect_evidence_refs", [])),
            "established_conditions": list(result.get("established_conditions", [])),
            "relevant_entity_ids": dict(result.get("relevant_entity_ids", {})),
        }

    def _kubectl_json(self, *args: str, timeout: int = 90) -> dict[str, Any]:
        process = run(
            [self.kubectl, "-n", self.context.namespace, *args, "-o", "json"],
            timeout=timeout,
        )
        value = json.loads(process.stdout)
        if not isinstance(value, dict):
            raise ActionFailure("kubectl JSON result is not an object")
        return value

    @staticmethod
    def _json_pointer(value: Any, pointer: str) -> Any:
        if pointer == "":
            return value
        if not pointer.startswith("/"):
            raise ActionFailure("JSON pointer must be empty or begin with '/'")
        current = value
        for raw_token in pointer[1:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and token in current:
                current = current[token]
            elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
                current = current[int(token)]
            else:
                raise KeyError(pointer)
        return current

    def _get_resource(self, resource: str, selector: str | None = None) -> dict[str, Any]:
        resource_type = resource.split("/", 1)[0].lower()
        safe_resource_types = {
            "all", "deployment", "deployments", "statefulset", "statefulsets",
            "daemonset", "daemonsets", "replicaset", "replicasets", "pod", "pods",
            "service", "services", "endpoint", "endpoints", "job", "jobs",
            "networkpolicy", "networkpolicies", "chaosengine", "chaosengines",
            "chaosresult", "chaosresults", "event", "events",
        }
        if resource_type not in safe_resource_types:
            raise ActionFailure(f"Kubernetes observation resource type is not allowlisted: {resource_type}")
        args = ["get", resource]
        if selector:
            if len(selector) > 512 or any(character in selector for character in ("\n", "\r", "\x00")):
                raise ActionFailure("Kubernetes selector is malformed")
            args.extend(["-l", selector])
        return self._kubectl_json(*args)

    def _verify_namespace_safety(self) -> None:
        context = run([self.kubectl, "config", "current-context"]).stdout.strip()
        if not context or any(token in context.lower() for token in ("prod", "production", "stage", "staging", "live")):
            raise ActionFailure(f"refusing emulation action against protected/empty context {context!r}")
        namespace = json.loads(
            run([self.kubectl, "get", "namespace", self.context.namespace, "-o", "json"]).stdout
        )
        labels = namespace.get("metadata", {}).get("labels", {})
        if labels.get("eve-trade.io/chaos-safe") != "true":
            raise ActionFailure("namespace lacks eve-trade.io/chaos-safe=true")
        if labels.get("eve-trade.io/run-id") != self.context.run_id:
            raise ActionFailure("namespace run ID differs from scenario invocation")
        marker = self._kubectl_json("get", "configmap", "eve-trade-chaos-safety")
        data = marker.get("data", {})
        if data.get("run_id") != self.context.run_id or data.get("scenario_id") != self.context.scenario_id:
            raise ActionFailure("chaos safety marker does not authorize this exact run/scenario")

    def kubernetes(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request.get("operation")
        resource = str(request.get("resource") or "")
        if not resource:
            raise ActionFailure("KUBERNETES action requires exact resource")
        if operation == "GET_JSON":
            value = self._get_resource(resource, str(request.get("selector") or "") or None)
        elif operation == "ROLLOUT_RESTART":
            self._verify_namespace_safety()
            run([self.kubectl, "-n", self.context.namespace, "rollout", "restart", resource])
            run(
                [self.kubectl, "-n", self.context.namespace, "rollout", "status", resource, "--timeout=300s"],
                timeout=320,
            )
            value = self._kubectl_json("get", resource)
        elif operation == "SCALE":
            self._verify_namespace_safety()
            replicas = request.get("replicas")
            if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0 or replicas > 20:
                raise ActionFailure("KUBERNETES SCALE replicas must be an integer in [0, 20]")
            run(
                [
                    self.kubectl,
                    "-n",
                    self.context.namespace,
                    "scale",
                    resource,
                    f"--replicas={replicas}",
                ]
            )
            value = self._kubectl_json("get", resource)
        elif operation == "WAIT_ROLLOUT":
            run(
                [self.kubectl, "-n", self.context.namespace, "rollout", "status", resource, "--timeout=300s"],
                timeout=320,
            )
            value = self._kubectl_json("get", resource)
        elif operation == "WAIT_JSON_CONDITION":
            selector = str(request.get("selector") or "") or None
            pointer = str(request.get("json_pointer") or "")
            comparison = str(request.get("comparison") or "EQUALS")
            if comparison not in {"EQUALS", "NOT_EQUALS", "EXISTS", "NOT_EXISTS"}:
                raise ActionFailure("unsupported Kubernetes JSON condition comparison")
            expected = request.get("expected")
            timeout_seconds = int(request.get("timeout_seconds", 120))
            if not 1 <= timeout_seconds <= 600:
                raise ActionFailure("Kubernetes condition timeout must be in [1, 600]")
            deadline = time.monotonic() + timeout_seconds
            last: dict[str, Any] | None = None
            while time.monotonic() < deadline:
                last = self._get_resource(resource, selector)
                exists = True
                try:
                    observed = self._json_pointer(last, pointer)
                except KeyError:
                    exists = False
                    observed = None
                satisfied = {
                    "EQUALS": exists and observed == expected,
                    "NOT_EQUALS": exists and observed != expected,
                    "EXISTS": exists,
                    "NOT_EXISTS": not exists,
                }[comparison]
                if satisfied:
                    value = last
                    break
                time.sleep(min(float(request.get("poll_interval_seconds", 1)), 5.0))
            else:
                raise ActionFailure(f"bounded Kubernetes condition wait expired; last={redact(last)}")
        elif operation == "WAIT_POD_UID_CHANGE":
            selector = str(request.get("selector") or "")
            previous_uid = str(request.get("previous_uid") or "")
            if not selector or not previous_uid:
                raise ActionFailure("WAIT_POD_UID_CHANGE requires selector and previous_uid")
            timeout_seconds = int(request.get("timeout_seconds", 180))
            if not 1 <= timeout_seconds <= 600:
                raise ActionFailure("pod UID wait timeout must be in [1, 600]")
            deadline = time.monotonic() + timeout_seconds
            value = {}
            while time.monotonic() < deadline:
                value = self._get_resource("pods", selector)
                items = value.get("items", [])
                replacements = [
                    item
                    for item in items
                    if item.get("metadata", {}).get("uid") != previous_uid
                    and any(
                        condition.get("type") == "Ready" and condition.get("status") == "True"
                        for condition in item.get("status", {}).get("conditions", [])
                    )
                ]
                old_present = any(item.get("metadata", {}).get("uid") == previous_uid for item in items)
                if replacements and not old_present:
                    break
                time.sleep(1)
            else:
                raise ActionFailure("bounded wait expired before a ready replacement pod had a new UID")
        elif operation == "DELETE_LABELLED":
            self._verify_namespace_safety()
            allowed_resources = {
                "configmap", "job", "pod", "networkpolicy", "chaosengine", "chaosresult",
                "configmap,job,pod,networkpolicy,chaosengine,chaosresult",
            }
            selector = str(request.get("selector") or "")
            if resource not in allowed_resources or selector != f"eve-trade.io/run-id={self.context.run_id}":
                raise ActionFailure("scoped delete requires the exact run label and an allowlisted resource set")
            run(
                [
                    self.kubectl, "-n", self.context.namespace, "delete", resource,
                    "-l", selector, "--ignore-not-found=true", "--wait=true", "--timeout=120s",
                ],
                timeout=140,
            )
            value = self._get_resource("all", selector)
        else:
            raise ActionFailure(f"unsupported KUBERNETES operation {operation!r}")
        metadata = value.get("metadata", {})
        ref = f"kubernetes://{self.context.namespace}/{resource}/{metadata.get('uid', 'unknown')}"
        return {
            "facts": [
                fact(
                    "kubernetes_resource_observation",
                    value,
                    "KUBERNETES_CONTROL_PLANE",
                    ref,
                    {"resource": resource, "uid": str(metadata.get("uid") or "")},
                )
            ],
            "control_plane_evidence_refs": [ref],
            "independent_effect_evidence_refs": [ref] if operation in {"GET_JSON", "WAIT_ROLLOUT", "WAIT_JSON_CONDITION", "WAIT_POD_UID_CHANGE"} else [],
            "established_conditions": [f"kubernetes_{operation.lower()}_completed"],
            "relevant_entity_ids": {"resource": resource, "uid": str(metadata.get("uid") or "")},
        }

    def _http_request(self, request: dict[str, Any]) -> tuple[int, bytes, dict[str, str]]:
        url = str(request.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ActionFailure("HTTP adapter requires an absolute HTTP(S) URL")
        method = str(request.get("method") or "GET").upper()
        timeout = float(request.get("timeout_seconds", 15))
        if not 0 < timeout <= 120:
            raise ActionFailure("HTTP timeout must be in (0, 120]")
        headers = {str(key): str(value) for key, value in dict(request.get("headers", {})).items()}
        body = request.get("body")
        data = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        if data is not None:
            headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return int(response.status), response.read(1024 * 1024), dict(response.headers.items())
        except urllib.error.HTTPError as error:
            return int(error.code), error.read(1024 * 1024), dict(error.headers.items())

    def http_json(self, request: dict[str, Any]) -> dict[str, Any]:
        status, body, headers = self._http_request(request)
        try:
            payload: Any = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"body_base64": base64.b64encode(body).decode("ascii")}
        ref = f"application-http://{self.context.action_id}/{int(time.time_ns())}"
        return {
            "facts": [
                fact("http_status", status, "REAL_APPLICATION", ref),
                fact("http_response", payload, "REAL_APPLICATION", ref),
                fact(
                    "http_response_metadata",
                    {"content_type": headers.get("Content-Type"), "content_length": len(body)},
                    "REAL_APPLICATION",
                    ref,
                ),
            ],
            "independent_effect_evidence_refs": [ref],
            "established_conditions": ["real_http_exchange_observed"],
            "relevant_entity_ids": dict(request.get("entity_ids", {})),
        }

    def udp_json(self, request: dict[str, Any]) -> dict[str, Any]:
        host = str(request.get("host") or "")
        port = request.get("port")
        timeout = float(request.get("timeout_seconds", 5))
        if not host or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ActionFailure("UDP adapter requires host and port in [1, 65535]")
        payload = request.get("payload")
        if isinstance(payload, dict):
            sent = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        elif isinstance(payload, str):
            sent = base64.b64decode(payload, validate=True)
        else:
            raise ActionFailure("UDP payload must be JSON object or base64 string")
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(sent, (host, port))
            received, peer = sock.recvfrom(int(request.get("maximum_response_bytes", 65507)))
        try:
            response: Any = json.loads(received)
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {"body_base64": base64.b64encode(received).decode("ascii")}
        ref = f"application-udp://{host}:{port}/{int(time.time_ns())}"
        return {
            "facts": [
                fact("udp_response", response, "REAL_APPLICATION", ref),
                fact("udp_exchange_sizes", {"sent_bytes": len(sent), "received_bytes": len(received)}, "REAL_APPLICATION", ref),
                fact("udp_response_peer", list(peer), "REAL_APPLICATION", ref),
            ],
            "independent_effect_evidence_refs": [ref],
            "established_conditions": ["real_udp_exchange_observed"],
            "relevant_entity_ids": dict(request.get("entity_ids", {})),
        }

    def postgres_sql(self, request: dict[str, Any]) -> dict[str, Any]:
        query = str(request.get("query") or "")
        if not query or "\\" in query:
            raise ActionFailure("POSTGRES_SQL requires a non-psql-meta query")
        if re.search(r"(?i)\b(pg_auth|pg_shadow|password|secret|credential|private_key|hmac)\b", query):
            raise ActionFailure("POSTGRES_SQL refuses queries over credential-bearing fields or catalogs")
        if re.search(r"(?i)\b(drop|truncate|alter\s+system|copy\s+.+\s+to\s+program)\b", query):
            raise ActionFailure("POSTGRES_SQL refuses destructive or host-program statements")
        url_env = str(request.get("database_url_env") or "EVE_TRADE_RUNTIME_DATABASE_URL")
        database_url = os.environ.get(url_env)
        if not database_url:
            raise ActionFailure(f"required database URL environment variable is absent: {url_env}")
        environment = os.environ.copy()
        environment["PGDATABASE"] = database_url
        process = run(
            ["psql", "--no-password", "--csv", "--tuples-only", "--no-align", "--command", query],
            timeout=int(request.get("timeout_seconds", 60)),
            env=environment,
        )
        ref = f"postgres://{self.context.action_id}/{int(time.time_ns())}"
        return {
            "facts": [fact("postgres_query_rows", process.stdout.splitlines(), "REAL_DATABASE", ref)],
            "independent_effect_evidence_refs": [ref],
            "established_conditions": ["real_postgres_query_completed"],
            "relevant_entity_ids": dict(request.get("entity_ids", {})),
        }

    def nsq_http(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request.get("operation")
        base_url = str(request.get("base_url") or os.environ.get("EVE_TRADE_NSQ_HTTP") or "")
        if not base_url.startswith(("http://", "https://")):
            raise ActionFailure("NSQ_HTTP requires an absolute base URL")
        if operation == "PUBLISH":
            topic = str(request.get("topic") or "")
            body = request.get("body")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", topic):
                raise ActionFailure("NSQ topic has invalid syntax")
            if isinstance(body, dict):
                data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            elif isinstance(body, str):
                data = base64.b64decode(body, validate=True)
            else:
                raise ActionFailure("NSQ publish body must be object or base64 string")
            url = base_url.rstrip("/") + "/pub?" + urllib.parse.urlencode({"topic": topic})
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=15) as response:
                status = int(response.status)
                response_body = response.read(4096).decode("utf-8", "replace")
            value: Any = {"status": status, "body": response_body, "topic": topic, "published_bytes": len(data)}
        elif operation == "STATS":
            url = base_url.rstrip("/") + "/stats?format=json"
            with urllib.request.urlopen(url, timeout=15) as response:
                value = json.load(response)
        else:
            raise ActionFailure(f"unsupported NSQ_HTTP operation {operation!r}")
        ref = f"nsq://{self.context.action_id}/{int(time.time_ns())}"
        return {
            "facts": [fact("nsq_observation", value, "REAL_BROKER", ref)],
            "independent_effect_evidence_refs": [ref],
            "established_conditions": [f"real_nsq_{str(operation).lower()}_observed"],
            "relevant_entity_ids": dict(request.get("entity_ids", {})),
        }

    def observe_only(self, request: dict[str, Any]) -> dict[str, Any]:
        observations = request.get("observations")
        if not isinstance(observations, list) or not observations:
            raise ActionFailure("OBSERVE_ONLY requires a nonempty observations list")
        facts: list[dict[str, Any]] = []
        refs: list[str] = []
        conditions: list[str] = []
        entities: dict[str, str] = {}
        for index, observation in enumerate(observations):
            if not isinstance(observation, dict) or observation.get("adapter") == "OBSERVE_ONLY":
                raise ActionFailure(f"invalid nested observation at index {index}")
            result = self.execute(observation)
            facts.extend(result["facts"])
            refs.extend(result["independent_effect_evidence_refs"])
            conditions.extend(result["established_conditions"])
            entities.update(result["relevant_entity_ids"])
        return {
            "facts": facts,
            "independent_effect_evidence_refs": refs,
            "established_conditions": conditions,
            "relevant_entity_ids": entities,
        }

    def action_sequence(self, request: dict[str, Any]) -> dict[str, Any]:
        actions = request.get("actions")
        if set(request) != {"actions"} or not isinstance(actions, list) or not actions:
            raise ActionFailure("ACTION_SEQUENCE requires exactly one nonempty actions list")
        facts: list[dict[str, Any]] = []
        control_refs: list[str] = []
        independent_refs: list[str] = []
        conditions: list[str] = []
        entities: dict[str, str] = {}
        for index, action in enumerate(actions):
            if not isinstance(action, dict) or set(action) != {"adapter", "request"}:
                raise ActionFailure(f"malformed sequence action at index {index}")
            if action.get("adapter") in {"ACTION_SEQUENCE", "OBSERVE_ONLY", "LITMUS"}:
                raise ActionFailure(f"nested sequence/control adapter is forbidden at index {index}")
            result = self.execute(action)
            facts.extend(result["facts"])
            control_refs.extend(result["control_plane_evidence_refs"])
            independent_refs.extend(result["independent_effect_evidence_refs"])
            conditions.extend(result["established_conditions"])
            entities.update(result["relevant_entity_ids"])
        return {
            "facts": facts,
            "control_plane_evidence_refs": control_refs,
            "independent_effect_evidence_refs": independent_refs,
            "established_conditions": conditions,
            "relevant_entity_ids": entities,
        }

    def litmus(self, request: dict[str, Any]) -> dict[str, Any]:
        self._verify_namespace_safety()
        operation = request.get("operation")
        engine_action_id = self.context.action_id
        if operation == "RELEASE":
            engine_action_id = str(request.get("activation_action_id") or "")
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", engine_action_id):
                raise ActionFailure("Litmus release requires the exact DNS-safe activation action ID")
        scenario_slug = self.context.scenario_id.replace("_", "-")
        engine_name = (
            f"emu-{scenario_slug[:20]}-{engine_action_id[:14]}-{self.context.run_id[-12:]}"
        )[:63].rstrip("-")
        if operation == "RELEASE":
            run(
                [
                    self.kubectl,
                    "-n",
                    self.context.namespace,
                    "delete",
                    "chaosengine,chaosresult,job,pod,networkpolicy",
                    "-l",
                    f"eve-trade.io/run-id={self.context.run_id},eve-trade.io/action-id={engine_action_id[:63]}",
                    "--ignore-not-found=true",
                    "--wait=true",
                    "--timeout=120s",
                ],
                timeout=140,
            )
            ref = f"litmus://{self.context.namespace}/{engine_name}/released"
            independent = request.get("independent_witness")
            if not isinstance(independent, dict):
                raise ActionFailure("Litmus release requires an independent recovery witness action")
            witness_result = self.execute(independent)
            if not witness_result["independent_effect_evidence_refs"]:
                raise ActionFailure("independent recovery witness produced no evidence reference")
            return {
                "facts": [
                    fact("litmus_release", {"engine_name": engine_name}, "LITMUS_CONTROL_PLANE", ref),
                    *witness_result["facts"],
                ],
                "control_plane_evidence_refs": [ref],
                "independent_effect_evidence_refs": witness_result["independent_effect_evidence_refs"],
                "established_conditions": [
                    "scenario_litmus_resources_released",
                    *witness_result["established_conditions"],
                ],
                "relevant_entity_ids": {
                    "chaos_engine": engine_name,
                    **witness_result["relevant_entity_ids"],
                },
            }
        if operation != "ACTIVATE":
            raise ActionFailure("LITMUS operation must be ACTIVATE or RELEASE")
        experiment = str(request.get("experiment") or "")
        target = request.get("target")
        if not experiment or not isinstance(target, dict):
            raise ActionFailure("LITMUS activation requires exact experiment and target")
        selector = str(target.get("selector") or "")
        kind = str(target.get("kind") or "")
        if not selector or not kind:
            raise ActionFailure("LITMUS target requires exact kind and selector")
        environment = [
            {"name": str(key), "value": str(value)}
            for key, value in sorted(dict(request.get("environment", {})).items())
        ]
        document = {
            "apiVersion": "litmuschaos.io/v1alpha1",
            "kind": "ChaosEngine",
            "metadata": {
                "name": engine_name,
                "namespace": self.context.namespace,
                "labels": self.context.labels,
            },
            "spec": {
                "engineState": "active",
                "annotationCheck": "false",
                "jobCleanUpPolicy": "retain",
                "appinfo": {
                    "appns": self.context.namespace,
                    "applabel": selector,
                    "appkind": kind,
                },
                "chaosServiceAccount": "eve-trade-chaos-runner",
                "experiments": [{"name": experiment, "spec": {"components": {"env": environment}}}],
            },
        }
        applied = run(
            [self.kubectl, "-n", self.context.namespace, "apply", "-f", "-"],
            input_text=yaml.safe_dump(document, sort_keys=False),
        )
        engine = self._kubectl_json("get", "chaosengine", engine_name)
        uid = str(engine.get("metadata", {}).get("uid") or "")
        if not uid:
            raise ActionFailure("created ChaosEngine has no UID")
        acknowledgement_timeout = int(request.get("acknowledgement_timeout_seconds", 120))
        if not 1 <= acknowledgement_timeout <= 600:
            raise ActionFailure("Litmus acknowledgement timeout must be in [1, 600]")
        deadline = time.monotonic() + acknowledgement_timeout
        result_resource: dict[str, Any] | None = None
        result_status: dict[str, Any] = {}
        while time.monotonic() < deadline:
            candidates = self._get_resource("chaosresults")
            for candidate in candidates.get("items", []):
                labels = candidate.get("metadata", {}).get("labels", {})
                annotations = candidate.get("metadata", {}).get("annotations", {})
                owner_uids = {
                    str(owner.get("uid") or "")
                    for owner in candidate.get("metadata", {}).get("ownerReferences", [])
                }
                identity_values = {str(value) for value in [*labels.values(), *annotations.values()]}
                result_name = str(candidate.get("metadata", {}).get("name") or "")
                if (
                    uid not in owner_uids
                    and uid not in identity_values
                    and engine_name not in identity_values
                    and not result_name.startswith(engine_name + "-")
                ):
                    continue
                status = candidate.get("status", {}).get("experimentStatus", {})
                if status.get("phase") == "Error" or status.get("verdict") in {"Fail", "Failed"}:
                    raise ActionFailure(f"Litmus experiment entered a failed control-plane state: {redact(status)}")
                if status.get("phase"):
                    result_resource = candidate
                    result_status = status
                    break
            if result_resource is not None:
                break
            time.sleep(1)
        if result_resource is None:
            raise ActionFailure("bounded wait expired before a scenario-bound ChaosResult acknowledgement")
        ref = f"litmus://{self.context.namespace}/{engine_name}/{uid}"
        result_uid = str(result_resource.get("metadata", {}).get("uid") or "")
        result_ref = f"litmus-result://{self.context.namespace}/{result_uid}"
        facts = [
            fact(
                "litmus_control_plane_activation",
                {
                    "experiment": experiment,
                    "engine_name": engine_name,
                    "engine_uid": uid,
                    "result_uid": result_uid,
                    "result_status": result_status,
                    "apply_output": applied.stdout.strip(),
                },
                "LITMUS_CONTROL_PLANE",
                ref,
                {"chaos_engine_uid": uid, "chaos_result_uid": result_uid, "target_selector": selector},
            )
        ]
        independent = request.get("independent_witness")
        if not isinstance(independent, dict):
            raise ActionFailure("Litmus activation requires an independent witness action")
        witness_result = self.execute(independent)
        if not witness_result["independent_effect_evidence_refs"]:
            raise ActionFailure("independent witness produced no independent evidence reference")
        facts.extend(witness_result["facts"])
        return {
            "facts": facts,
            "control_plane_evidence_refs": [ref, result_ref],
            "independent_effect_evidence_refs": witness_result["independent_effect_evidence_refs"],
            "established_conditions": ["litmus_control_plane_activated", *witness_result["established_conditions"]],
            "relevant_entity_ids": {
                "chaos_engine_uid": uid,
                "chaos_result_uid": result_uid,
                "target_selector": selector,
                **witness_result["relevant_entity_ids"],
            },
        }
