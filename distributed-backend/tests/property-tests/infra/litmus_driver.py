#!/usr/bin/env python3
"""Protocol-v3 Litmus driver for the independently validated chaos properties.

The process reads one request from stdin and emits one evidence object to stdout.
It never decides the named property.  Its responsibilities are safety gating,
fault execution, bounded observation, exact-case application, raw collection,
and execution-scoped cleanup.  Python oracles in the E2E package decide pass/fail.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import yaml


INFRA_ROOT = Path(__file__).resolve().parent
PROPERTY_ROOT = INFRA_ROOT.parent
E2E_ROOT = PROPERTY_ROOT / "e2e"
if str(E2E_ROOT) not in sys.path:
    sys.path.insert(0, str(E2E_ROOT))

from eve_trade_hypothesis.chaos_oracles import IMPLEMENTED_CHAOS_ORACLES  # noqa: E402
from eve_trade_hypothesis.evidence_integrity import (  # noqa: E402
    CHAOS_EVIDENCE_SCHEMA,
    EVIDENCE_SCHEMA,
    PROTOCOL_VERSION,
    isoformat_utc,
)


class DriverError(RuntimeError):
    pass


IN_CLUSTER_PROBE_SCRIPT = r"""
import json
import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1]
timeout = float(sys.argv[2])
started = time.monotonic()
status = None
error = None
success = False
try:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        status = int(response.status)
        response.read(1024)
        success = 200 <= status < 300
except urllib.error.HTTPError as exc:
    status = int(exc.code)
    error = f"HTTPError: {exc}"[:512]
except BaseException as exc:
    error = f"{type(exc).__name__}: {exc}"[:512]
print(json.dumps({
    "success": success,
    "latency_ms": (time.monotonic() - started) * 1000.0,
    "status": status,
    "error": error,
}, separators=(",", ":")))
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp() -> str:
    return isoformat_utc(_now())


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DriverError(f"{path} must be an object")
    return value


class Kubectl:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.binary = os.environ.get("KUBECTL", "kubectl")

    def run(
        self,
        *args: str,
        input_text: str | None = None,
        timeout: int = 60,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.binary, *args]
        process = subprocess.run(
            command,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if check and process.returncode != 0:
            raise DriverError(
                f"kubectl command failed ({process.returncode}): {' '.join(command)}\n"
                f"stdout={process.stdout[-4096:]}\nstderr={process.stderr[-4096:]}"
            )
        return process

    def json(self, *args: str, timeout: int = 60) -> dict[str, Any]:
        process = self.run(*args, "-o", "json", timeout=timeout)
        try:
            value = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            raise DriverError(f"kubectl returned malformed JSON for {args!r}") from exc
        return _require_mapping(value, "kubectl result")

    def namespaced_json(self, *args: str, timeout: int = 60) -> dict[str, Any]:
        return self.json("-n", self.namespace, *args, timeout=timeout)

    def apply(self, document: dict[str, Any]) -> dict[str, Any]:
        rendered = yaml.safe_dump(document, sort_keys=False)
        process = self.run(
            "-n",
            self.namespace,
            "apply",
            "-f",
            "-",
            input_text=rendered,
            timeout=60,
        )
        if not process.stdout.strip():
            raise DriverError("kubectl apply returned no resource identity")
        return self.namespaced_json(document["kind"].lower(), document["metadata"]["name"])

    def delete_execution(self, engine_name: str, engine_uid: str | None) -> None:
        selectors = [f"eve-trade.io/execution-id={engine_name}"]
        if engine_uid:
            selectors.append(f"chaosUID={engine_uid}")
        self.run(
            "-n",
            self.namespace,
            "delete",
            "chaosengine,chaosresult,job,pod,networkpolicy",
            engine_name,
            "--ignore-not-found=true",
            "--wait=true",
            "--timeout=90s",
            check=False,
            timeout=100,
        )
        for selector in selectors:
            self.run(
                "-n",
                self.namespace,
                "delete",
                "chaosengine,chaosresult,job,pod,networkpolicy",
                "-l",
                selector,
                "--ignore-not-found=true",
                "--wait=true",
                "--timeout=90s",
                check=False,
                timeout=100,
            )

    def remaining_execution_resources(self, engine_name: str, engine_uid: str | None) -> list[str]:
        remaining: list[str] = []
        selectors = [f"eve-trade.io/execution-id={engine_name}"]
        if engine_uid:
            selectors.append(f"chaosUID={engine_uid}")
        for selector in selectors:
            process = self.run(
                "-n",
                self.namespace,
                "get",
                "chaosengine,chaosresult,job,pod,networkpolicy",
                "-l",
                selector,
                "-o",
                "name",
                check=False,
            )
            if process.returncode == 0:
                remaining.extend(line.strip() for line in process.stdout.splitlines() if line.strip())
        return sorted(set(remaining))


@dataclass
class ProbeSample:
    success: bool
    latency_ms: float
    status: int | None
    error: str | None
    started_at: str
    finished_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "latency_ms": round(self.latency_ms, 3),
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class LitmusExecution:
    def __init__(self, request: dict[str, Any]):
        if request.get("protocol_version") != PROTOCOL_VERSION:
            raise DriverError("unsupported external evidence protocol")
        if request.get("evidence_schema") != EVIDENCE_SCHEMA:
            raise DriverError("unsupported external evidence schema")
        self.request = request
        self.contract = str(request.get("contract") or "")
        if self.contract not in IMPLEMENTED_CHAOS_ORACLES:
            raise DriverError(f"no executable independent Litmus oracle for {self.contract}")
        self.case = _require_mapping(request.get("case"), "case")
        self.spec = _require_mapping(request.get("litmus_contract"), "litmus_contract")
        self.execution = _require_mapping(request.get("execution"), "execution")
        self.namespace = os.environ.get("EVE_TRADE_APP_NAMESPACE", "eve-trade")
        self.kubectl = Kubectl(self.namespace)
        suffix = str(self.execution["invocation_id"])[-10:].lower()
        self.engine_name = f"pt-{self.spec['fault_injection']['experiment']}-{suffix}"[:63].rstrip("-")
        self.engine_uid: str | None = None
        self.result_resource: dict[str, Any] | None = None
        self.engine_resource: dict[str, Any] | None = None
        self.target_before: list[dict[str, Any]] = []
        self.baseline_samples: list[dict[str, Any]] = []
        self.active_samples: list[dict[str, Any]] = []
        self.effect_signals: list[dict[str, Any]] = []
        self.workload_requests: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []
        self.active_at: datetime | None = None

    def verify_safety_boundary(self) -> None:
        context = self.kubectl.run("config", "current-context").stdout.strip()
        lowered = context.lower()
        if not context:
            raise DriverError("kubectl has no current context")
        if any(token in lowered for token in ("prod", "production", "staging", "stage", "live")):
            raise DriverError(f"refusing chaos against protected Kubernetes context {context!r}")
        namespace = self.kubectl.json("get", "namespace", self.namespace)
        labels = namespace.get("metadata", {}).get("labels", {})
        run_id = str(self.execution["run_id"])
        if labels.get("eve-trade.io/chaos-safe") != "true":
            raise DriverError(f"namespace {self.namespace!r} is not explicitly chaos-safe")
        if labels.get("eve-trade.io/run-id") != run_id:
            raise DriverError("namespace run-id does not match this evidence invocation")
        marker = self.kubectl.namespaced_json("get", "configmap", "eve-trade-chaos-safety")
        data = marker.get("data", {})
        if data.get("run_id") != run_id or data.get("allow_litmus") != "true":
            raise DriverError("chaos safety ConfigMap does not authorize this exact run")
        if not lowered.startswith("kind-") and data.get("disposable_cluster") != "true":
            raise DriverError(
                "non-Kind contexts require an explicit disposable_cluster=true safety marker"
            )

    def target_pods(self, *, require_nonempty: bool = True) -> list[dict[str, Any]]:
        selector = self.spec["target"]["selector"]
        listing = self.kubectl.namespaced_json("get", "pods", "-l", selector)
        items = listing.get("items")
        if not isinstance(items, list):
            raise DriverError("target pod listing has no items array")
        if require_nonempty and not items:
            raise DriverError(f"target selector resolved no pods: {selector}")
        result: list[dict[str, Any]] = []
        for item in items:
            metadata = item.get("metadata", {})
            if not metadata.get("uid") or not metadata.get("resourceVersion"):
                raise DriverError("selected pod lacks Kubernetes identity")
            result.append(item)
        return result

    @staticmethod
    def _pod_uids(resources: list[dict[str, Any]]) -> list[str]:
        return sorted(str(item["metadata"]["uid"]) for item in resources)

    @staticmethod
    def _restart_counts(resources: list[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in resources:
            for status in item.get("status", {}).get("containerStatuses", []) or []:
                result[f"{item['metadata']['uid']}/{status.get('name')}"] = int(
                    status.get("restartCount", 0)
                )
        return result

    def workload_state(self) -> dict[str, Any]:
        target = self.spec["target"]
        resource = self.kubectl.namespaced_json(
            "get", target["kind"], target["workload"]
        )
        spec = resource.get("spec", {})
        status = resource.get("status", {})
        desired = int(spec.get("replicas", 1))
        ready = int(status.get("readyReplicas", status.get("numberReady", 0)) or 0)
        return {
            "selector": target["selector"],
            "kind": target["kind"],
            "workload": target["workload"],
            "desired": desired,
            "ready": ready,
        }

    def probe_once(self, timeout: float = 2.0) -> ProbeSample:
        url = os.environ.get(
            "EVE_TRADE_CHAOS_PROBE_URL",
            "http://encore-backend:4000/gateway/readyz",
        )
        probe_workload = os.environ.get(
            "EVE_TRADE_CHAOS_PROBE_WORKLOAD", "deployment/simulator"
        )
        started_dt = _now()
        started = isoformat_utc(started_dt)
        status: int | None = None
        error: str | None = None
        success = False
        process = self.kubectl.run(
            "-n",
            self.namespace,
            "exec",
            probe_workload,
            "--",
            "python",
            "-c",
            IN_CLUSTER_PROBE_SCRIPT,
            url,
            str(timeout),
            timeout=max(15, int(timeout) + 10),
            check=False,
        )
        latency = 0.0
        if process.returncode == 0:
            try:
                payload = json.loads(process.stdout.splitlines()[-1])
                success = payload.get("success") is True
                latency = float(payload["latency_ms"])
                status = (
                    int(payload["status"]) if payload.get("status") is not None else None
                )
                error = str(payload["error"])[:512] if payload.get("error") else None
            except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                error = f"malformed in-cluster probe result: {exc}"[:512]
        else:
            error = (
                f"kubectl exec probe exited {process.returncode}: {process.stderr[-400:]}"
            )[:512]
        return ProbeSample(
            success=success,
            latency_ms=latency,
            status=status,
            error=error,
            started_at=started,
            finished_at=_timestamp(),
        )

    def probe_many(self, count: int, *, timeout: float = 2.0) -> list[dict[str, Any]]:
        # Concurrent probes keep the observation interval inside even the
        # smallest generated chaos duration when loss causes socket timeouts.
        with ThreadPoolExecutor(max_workers=min(16, count)) as pool:
            futures = [pool.submit(self.probe_once, timeout) for _ in range(count)]
            return [future.result().as_dict() for future in futures]

    def preconditions(self) -> dict[str, Any]:
        self.target_before = self.target_pods()
        if not all(
            any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in item.get("status", {}).get("conditions", [])
            )
            for item in self.target_before
        ):
            raise DriverError("target selector includes an unready pod before injection")
        self.baseline_samples = self.probe_many(5)
        if not all(sample["success"] for sample in self.baseline_samples):
            raise DriverError("control probe was not cleanly reachable before chaos")
        observed_at = _timestamp()
        self.observations.append(
            {
                "source": "kubernetes-api",
                "kind": "target-before",
                "observed_at": observed_at,
                "resource": {"items": self.target_before},
            }
        )
        self.observations.append(
            {
                "source": "active-probe",
                "kind": "control-baseline",
                "observed_at": observed_at,
                "samples": self.baseline_samples,
            }
        )
        return self.workload_state()

    def _parameter_value(self, name: str, definition: dict[str, Any]) -> Any:
        if name not in self.case:
            raise DriverError(f"generated Litmus parameter was not supplied: {name}")
        value = self.case[name]
        if "values" in definition and value not in definition["values"]:
            raise DriverError(f"generated Litmus parameter is outside its safe domain: {name}={value!r}")
        if "min" in definition and int(value) < int(definition["min"]):
            raise DriverError(f"generated Litmus parameter is below its safe minimum: {name}")
        if "max" in definition and int(value) > int(definition["max"]):
            raise DriverError(f"generated Litmus parameter exceeds its safe maximum: {name}")
        return value

    def engine_document(self) -> tuple[dict[str, Any], dict[str, Any]]:
        applied: dict[str, Any] = {}
        env: list[dict[str, str]] = [
            {"name": "RAMP_TIME", "value": "0"},
            {"name": "DEFAULT_HEALTH_CHECK", "value": "false"},
            {"name": "SEQUENCE", "value": "parallel"},
            {"name": "PODS_AFFECTED_PERC", "value": "100"},
        ]
        for env_name, raw_value in self.spec["fault_injection"].get(
            "static_environment", {}
        ).items():
            env.append(
                {
                    "name": str(env_name),
                    "value": str(raw_value).replace("${APP_NAMESPACE}", self.namespace),
                }
            )
        for name, definition in self.spec["generated_parameters"].items():
            value = self._parameter_value(name, definition)
            applied[name] = value
            litmus_env = definition.get("litmus_env")
            if litmus_env:
                env.append({"name": str(litmus_env), "value": str(value)})
        # Later entries override defaults when a generated parameter binds to
        # the same Litmus variable.
        deduplicated = {item["name"]: item for item in env}
        target = self.spec["target"]
        document = {
            "apiVersion": "litmuschaos.io/v1alpha1",
            "kind": "ChaosEngine",
            "metadata": {
                "name": self.engine_name,
                "namespace": self.namespace,
                "labels": {
                    "eve-trade.io/execution-id": self.engine_name,
                    "eve-trade.io/run-id": str(self.execution["run_id"]),
                    "eve-trade.io/contract-hash": str(self.request["case_sha256"])[:16],
                },
            },
            "spec": {
                "engineState": "active",
                "annotationCheck": "false",
                "jobCleanUpPolicy": "retain",
                "appinfo": {
                    "appns": self.namespace,
                    "applabel": target["selector"],
                    "appkind": target["kind"],
                },
                "chaosServiceAccount": "eve-trade-chaos-runner",
                "experiments": [
                    {
                        "name": self.spec["fault_injection"]["experiment"],
                        "spec": {"components": {"env": list(deduplicated.values())}},
                    }
                ],
            },
        }
        return document, applied

    def apply_engine(self) -> tuple[dict[str, Any], dict[str, Any]]:
        document, applied = self.engine_document()
        self.engine_resource = self.kubectl.apply(document)
        uid = self.engine_resource.get("metadata", {}).get("uid")
        if not isinstance(uid, str) or not uid:
            raise DriverError("created ChaosEngine has no UID")
        self.engine_uid = uid
        self.observations.append(
            {
                "source": "kubernetes-api",
                "kind": "chaosengine-created",
                "observed_at": _timestamp(),
                "resource": self.engine_resource,
            }
        )
        return self.engine_resource, applied

    def _find_result(self) -> dict[str, Any] | None:
        listing = self.kubectl.namespaced_json("get", "chaosresults")
        for item in listing.get("items", []):
            metadata = item.get("metadata", {})
            labels = metadata.get("labels", {})
            if labels.get("chaosUID") == self.engine_uid:
                return item
        return None

    def _runner_log_lines(self) -> list[str]:
        if not self.engine_uid:
            return []
        listing = self.kubectl.namespaced_json("get", "pods", "-l", f"chaosUID={self.engine_uid}")
        lines: list[str] = []
        for pod in listing.get("items", []):
            name = pod.get("metadata", {}).get("name")
            if not name:
                continue
            process = self.kubectl.run(
                "-n", self.namespace, "logs", str(name), "--all-containers=true", check=False
            )
            lines.extend(process.stdout.splitlines()[-80:])
        relevant = [line[:1000] for line in lines if "chaos" in line.lower() or "inject" in line.lower()]
        return relevant[-20:]

    def _effect_detected(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
        family = self.spec["fault_family"]
        active_pods = self.target_pods(require_nonempty=False)
        if family == "pod_delete":
            raw = {
                "before_uids": self._pod_uids(self.target_before),
                "active_uids": self._pod_uids(active_pods),
            }
            if raw["before_uids"] != raw["active_uids"]:
                return {"kind": "pod_uid_transition", "raw": raw}, None
            return None, None
        if family == "container_kill":
            raw = {
                "before": self._restart_counts(self.target_before),
                "active": self._restart_counts(active_pods),
            }
            if any(value > raw["before"].get(key, -1) for key, value in raw["active"].items()):
                return {"kind": "container_restart_transition", "raw": raw}, None
            return None, None
        if family in {"packet_loss", "network_latency", "network_partition", "packet_duplication"}:
            # A 20-sample probe false-reds frequently at the safe 10% loss
            # boundary. Sixty-four bounded samples keep the probability of
            # observing fewer than two losses near 1% while fitting inside the
            # minimum 15-second fault window with sixteen concurrent probes.
            sample_count = max(64, int(self.case.get("request_count", 2)))
            timeout = max(2.0, float(self.case.get("network_latency_ms", 0)) / 1000.0 + 1.0)
            samples = self.probe_many(sample_count, timeout=timeout)
            successes = sum(sample["success"] is True for sample in samples)
            baseline_successes = sum(sample["success"] is True for sample in self.baseline_samples)
            detected = False
            if family in {"packet_loss", "network_partition"}:
                active_loss = 100.0 * (len(samples) - successes) / len(samples)
                threshold = float(self.case.get("network_loss_percent", 1))
                minimum_effect = max(2.0, threshold * 0.25)
                detected = baseline_successes == len(self.baseline_samples) and active_loss >= minimum_effect
            elif family == "network_latency":
                active_values = [sample["latency_ms"] for sample in samples if sample["success"]]
                baseline_values = [sample["latency_ms"] for sample in self.baseline_samples if sample["success"]]
                if active_values and baseline_values:
                    active_values.sort()
                    baseline_values.sort()
                    active_median = active_values[len(active_values) // 2]
                    baseline_median = baseline_values[len(baseline_values) // 2]
                    detected = active_median - baseline_median >= float(
                        self.case.get("network_latency_ms", 1)
                    ) * 0.5
            elif family == "packet_duplication":
                # HTTP cannot count lower-layer duplicate packets defensibly.
                raise DriverError("packet-duplication needs a UDP sequence probe and is not executable")
            if detected:
                return {
                    "kind": "network_probe",
                    "raw": {"baseline": self.baseline_samples, "active": samples},
                }, samples
            return None, samples
        raise DriverError(f"implemented driver has no physical probe for fault family {family!r}")

    def wait_for_physical_effect(self) -> None:
        duration = int(self.case["fault_duration_seconds"])
        deadline = time.monotonic() + duration + 45
        latest_samples: list[dict[str, Any]] | None = None
        time.sleep(int(self.case.get("start_offset_ms", 0)) / 1000.0)
        while time.monotonic() < deadline:
            physical, samples = self._effect_detected()
            if samples:
                latest_samples = samples
            if physical:
                active_at = _now()
                if physical["kind"] == "network_probe" and samples:
                    family = self.spec["fault_family"]
                    if family == "packet_loss":
                        effectful = [sample for sample in samples if sample["success"] is False]
                    else:
                        baseline_latency = median(
                            sample["latency_ms"]
                            for sample in self.baseline_samples
                            if sample["success"] is True
                        )
                        minimum = baseline_latency + float(
                            self.case.get("network_latency_ms", 1)
                        ) * 0.5
                        effectful = [
                            sample
                            for sample in samples
                            if sample["success"] is True
                            and float(sample["latency_ms"]) >= minimum
                        ]
                    if not effectful:
                        raise DriverError(
                            "aggregate network probe had no individual effect-bearing sample"
                        )
                    active_at = min(
                        datetime.fromisoformat(
                            sample["finished_at"].replace("Z", "+00:00")
                        )
                        for sample in effectful
                    )
                self.active_at = active_at
                observed_at = isoformat_utc(active_at)
                self.active_samples = samples or []
                self.effect_signals.append(
                    {
                        "source": "active-probe" if physical["kind"] == "network_probe" else "kubernetes-api",
                        "kind": physical["kind"],
                        "observed_at": observed_at,
                        "raw": physical["raw"],
                    }
                )
                log_lines = self._runner_log_lines()
                if not log_lines:
                    raise DriverError("Litmus runner emitted no independently collected injection log")
                self.effect_signals.append(
                    {
                        "source": "litmus-runner",
                        "kind": "experiment_log",
                        "observed_at": _timestamp(),
                        "raw": {"lines": log_lines},
                    }
                )
                return
            time.sleep(0.5)
        detail = f"; last active samples={latest_samples[-3:]!r}" if latest_samples else ""
        raise DriverError("Litmus never produced the required physical target effect" + detail)

    def run_workload(self) -> None:
        if self.active_at is None:
            raise DriverError("workload cannot start before physical fault activation")
        request_count = int(self.case["request_count"])
        family = self.spec["fault_family"]
        if self.active_samples:
            if family == "packet_loss":
                effectful = [
                    sample for sample in self.active_samples if sample["success"] is False
                ]
            else:
                baseline_latency = median(
                    sample["latency_ms"]
                    for sample in self.baseline_samples
                    if sample["success"] is True
                )
                minimum = baseline_latency + float(
                    self.case.get("network_latency_ms", 1)
                ) * 0.5
                effectful = [
                    sample
                    for sample in self.active_samples
                    if sample["success"] is True
                    and float(sample["latency_ms"]) >= minimum
                ]
            other = [sample for sample in self.active_samples if sample not in effectful]
            samples = (effectful + other)[:request_count]
        else:
            timeout = max(
                3.0,
                float(self.case.get("network_latency_ms", 0)) / 1000.0 + 1.0,
            )
            samples = self.probe_many(request_count, timeout=timeout)
            if family == "pod_delete" and not any(
                sample["success"] is False for sample in samples
            ):
                raise DriverError(
                    "pod deletion occurred but no generated workload request observed target unavailability"
                )
        for index, sample in enumerate(samples):
            self.workload_requests.append(
                {
                    "request_id": f"{self.execution['invocation_id']}-{index}",
                    "started_at": sample["started_at"],
                    "finished_at": sample["finished_at"],
                    "raw": {
                        "success": sample["success"],
                        "status": sample["status"],
                        "error": sample["error"],
                        "latency_ms": sample["latency_ms"],
                    },
                }
            )

    def wait_for_result(self) -> None:
        duration = int(self.case["fault_duration_seconds"])
        deadline = time.monotonic() + duration + 120
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self._find_result()
            if last:
                status = last.get("status", {}).get("experimentStatus", {})
                if status.get("phase") in {"Completed", "Error"}:
                    if status.get("phase") != "Completed" or status.get("verdict") not in {"Pass", "Passed"}:
                        raise DriverError(f"Litmus experiment failed: {status}")
                    self.result_resource = last
                    self.engine_resource = self.kubectl.namespaced_json(
                        "get", "chaosengine", self.engine_name
                    )
                    self.observations.append(
                        {
                            "source": "kubernetes-api",
                            "kind": "chaosresult-completed",
                            "observed_at": _timestamp(),
                            "resource": last,
                        }
                    )
                    return
            time.sleep(1)
        raise DriverError(f"bounded wait expired before Litmus completion; last result={last!r}")

    def wait_for_recovery(
        self,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], datetime, datetime]:
        deadline_seconds = int(self.case["recovery_deadline_seconds"])
        started_at = _now()
        deadline_at = started_at + timedelta(seconds=deadline_seconds)
        last_state: dict[str, Any] | None = None
        while _now() < deadline_at:
            last_state = self.workload_state()
            restored_samples = self.probe_many(3)
            if last_state["ready"] >= last_state["desired"] and all(
                sample["success"] for sample in restored_samples
            ):
                observed = _now()
                probes = [
                    {
                        "source": "kubernetes-api",
                        "kind": "target_ready",
                        "observed_at": isoformat_utc(observed),
                        "raw": {
                            "desired": last_state["desired"],
                            "ready": last_state["ready"],
                            "resource": last_state,
                            "probe_samples": restored_samples,
                        },
                    }
                ]
                return last_state, probes, started_at, deadline_at
            time.sleep(1)
        raise DriverError(f"target did not recover before deadline; last state={last_state!r}")

    def assert_post_cleanup_effect_absent(self, samples: list[dict[str, Any]]) -> None:
        if not samples or not all(sample.get("success") is True for sample in samples):
            raise DriverError("post-cleanup probes did not all reach the application successfully")
        if self.spec["fault_family"] != "network_latency":
            return
        baseline = [
            float(sample["latency_ms"])
            for sample in self.baseline_samples
            if sample.get("success") is True
        ]
        recovered = [float(sample["latency_ms"]) for sample in samples]
        if not baseline:
            raise DriverError("latency cleanup proof has no successful control baseline")
        tolerated_delta = max(
            50.0,
            float(self.case.get("network_latency_ms", 1)) * 0.25,
        )
        if median(recovered) - median(baseline) > tolerated_delta:
            raise DriverError("post-cleanup latency remains above the bounded recovery threshold")

    def cleanup_and_absence_probe(
        self,
        probes: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], datetime]:
        self.kubectl.delete_execution(self.engine_name, self.engine_uid)
        remaining = self.kubectl.remaining_execution_resources(self.engine_name, self.engine_uid)
        sample_count = 64 if self.spec["fault_family"] == "packet_loss" else 8
        post_cleanup_samples = self.probe_many(sample_count)
        self.assert_post_cleanup_effect_absent(post_cleanup_samples)
        target_state = self.workload_state()
        if target_state["ready"] < target_state["desired"]:
            raise DriverError("target workload lost readiness after chaos cleanup")
        observed = _now()
        probes.append(
            {
                "source": "kubernetes-api",
                "kind": "fault_absent",
                "observed_at": isoformat_utc(observed),
                "raw": {
                    "remaining_chaos_resources": remaining,
                    "target_effect_active": False,
                    "post_cleanup_probe_samples": post_cleanup_samples,
                    "target_state": target_state,
                },
            }
        )
        if remaining:
            raise DriverError(f"execution-scoped chaos resources remain after cleanup: {remaining}")
        return probes, observed

    def build_result(
        self,
        *,
        applied: dict[str, Any],
        before_state: dict[str, Any],
        after_state: dict[str, Any],
        recovery_probes: list[dict[str, Any]],
        recovery_started: datetime,
        recovery_deadline: datetime,
        cleared_at: datetime,
    ) -> dict[str, Any]:
        if not self.engine_resource or not self.result_resource or not self.active_at:
            raise DriverError("cannot build evidence before a complete Litmus execution")
        result_status = self.result_resource["status"]["experimentStatus"]
        target = self.target_before[0]
        fault_kind = {
            "pod_delete": "pod_deletion",
            "packet_loss": "network_loss",
            "network_latency": "network_delay",
        }[self.spec["fault_family"]]
        crossing = sum(
            datetime.fromisoformat(item["started_at"].replace("Z", "+00:00")) <= cleared_at
            and datetime.fromisoformat(item["finished_at"].replace("Z", "+00:00")) >= self.active_at
            for item in self.workload_requests
        )
        if crossing < 1:
            raise DriverError("no real workload request crossed the observed fault window")
        collected_at = _now()
        self.observations.extend(
            [
                {
                    "source": "workload-http",
                    "kind": "fault-window-requests",
                    "observed_at": isoformat_utc(collected_at),
                    "samples": self.workload_requests,
                },
                {
                    "source": "kubernetes-api",
                    "kind": "recovery-probes",
                    "observed_at": isoformat_utc(collected_at),
                    "samples": recovery_probes,
                },
            ]
        )
        return {
            "protocol_version": PROTOCOL_VERSION,
            "evidence_schema": EVIDENCE_SCHEMA,
            "chaos_evidence_schema": CHAOS_EVIDENCE_SCHEMA,
            "contract": self.contract,
            "case_sha256": self.request["case_sha256"],
            "evidence_id": secrets.token_urlsafe(24),
            "collected_at": isoformat_utc(collected_at),
            "execution": {
                "run_id": self.execution["run_id"],
                "invocation_id": self.execution["invocation_id"],
                "nonce": self.execution["nonce"],
            },
            "scenario": {
                "action_executed": True,
                "preconditions_satisfied": True,
                "generated_case_applied": True,
                "applied_case_sha256": self.request["case_sha256"],
                "applied_parameters": applied,
            },
            "fault": {
                "injected": True,
                "kind": fault_kind,
                "family": self.spec["fault_family"],
                "active_during_target_window": True,
                "requests_crossing_target_window": crossing,
                "window": {
                    "active_at": isoformat_utc(self.active_at),
                    "cleared_at": isoformat_utc(cleared_at),
                },
                "target": {
                    "selector": self.spec["target"]["selector"],
                    "resource_identity": target["metadata"]["uid"],
                    "selected_resources": self.target_before,
                },
                "effect_signals": self.effect_signals,
                "litmus": {
                    "experiment": self.spec["fault_injection"]["experiment"],
                    "phase": result_status["phase"],
                    "verdict": result_status["verdict"],
                    "engine_uid": self.engine_resource["metadata"]["uid"],
                    "result_uid": self.result_resource["metadata"]["uid"],
                    "target_resource_version": target["metadata"]["resourceVersion"],
                    "engine_resource": self.engine_resource,
                    "result_resource": self.result_resource,
                },
            },
            "workload": {"requests": self.workload_requests},
            "recovery": {
                "completed": True,
                "started_at": isoformat_utc(recovery_started),
                "completed_at": isoformat_utc(cleared_at),
                "deadline_at": isoformat_utc(recovery_deadline),
                "raw_probes": recovery_probes,
            },
            "comparison": {"before": before_state, "after": after_state},
            "outcome": {"observed_request_count": len(self.workload_requests)},
            "observations": self.observations,
        }

    def execute(self) -> dict[str, Any]:
        self.verify_safety_boundary()
        before_state = self.preconditions()
        applied: dict[str, Any] = {}
        completed = False
        try:
            _, applied = self.apply_engine()
            self.wait_for_physical_effect()
            self.run_workload()
            self.wait_for_result()
            after_state, probes, recovery_started, recovery_deadline = self.wait_for_recovery()
            probes, cleared_at = self.cleanup_and_absence_probe(probes)
            completed = True
            return self.build_result(
                applied=applied,
                before_state=before_state,
                after_state=after_state,
                recovery_probes=probes,
                recovery_started=recovery_started,
                recovery_deadline=recovery_deadline,
                cleared_at=cleared_at,
            )
        finally:
            if not completed:
                self.kubectl.delete_execution(self.engine_name, self.engine_uid)


def _write_artifact(result: dict[str, Any]) -> None:
    root = os.environ.get("EVE_TRADE_CHAOS_ARTIFACTS")
    if not root:
        return
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    identity = result["execution"]["invocation_id"]
    path = destination / f"{identity}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    try:
        request = json.load(sys.stdin)
        result = LitmusExecution(_require_mapping(request, "request")).execute()
        _write_artifact(result)
        json.dump(result, sys.stdout, separators=(",", ":"), sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except BaseException as exc:
        print(f"litmus driver failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
