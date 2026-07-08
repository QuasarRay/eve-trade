"""Read-only Kubernetes evidence collector."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .redaction import redact_text
from .run_context import RunContext
from .storage import RunStorage


def collect_kubernetes(context: RunContext, storage: RunStorage | None = None, *, namespace: str = "eve-trade") -> dict[str, Any]:
    storage = storage or RunStorage(context.run_dir)
    metadata: dict[str, Any] = {"available": False, "namespace": namespace, "commands": {}, "pods": []}
    if not shutil.which("kubectl"):
        metadata["error"] = "kubectl executable not found"
        storage.write_json("kubernetes/metadata.json", metadata)
        return metadata
    context_code, current_context = _capture(["kubectl", "config", "current-context"], context.repo_root)
    if context_code:
        metadata["error"] = "kubectl has no configured current context"
        storage.write_text("kubernetes/current-context.txt", redact_text(current_context))
        storage.write_json("kubernetes/metadata.json", metadata)
        return metadata
    metadata.update({"available": True, "current_context": current_context.strip()})
    commands = {
        "current-context.txt": ["kubectl", "config", "current-context"],
        "namespaces.txt": ["kubectl", "get", "namespaces", "-o", "wide"],
        "pods.txt": ["kubectl", "-n", namespace, "get", "pods", "-o", "wide"],
        "services.txt": ["kubectl", "-n", namespace, "get", "services", "-o", "wide"],
        "deployments.txt": ["kubectl", "-n", namespace, "get", "deployments", "-o", "wide"],
        "events.txt": ["kubectl", "-n", namespace, "get", "events", "--sort-by=.lastTimestamp"],
        "rendered-local.yaml": ["kubectl", "kustomize", "distributed-backend/orchestration/kubernetes/overlay/local"],
    }
    for filename, argv in commands.items():
        code, output = _capture(argv, context.repo_root, timeout=90)
        storage.write_text(Path("kubernetes") / filename, redact_text(output))
        metadata["commands"][filename] = code
    code, pods = _capture(["kubectl", "-n", namespace, "get", "pods", "-o", "name"], context.repo_root)
    if code == 0:
        for pod in [line.strip() for line in pods.splitlines() if line.strip()]:
            safe_name = pod.replace("/", "-")
            log_code, logs = _capture(["kubectl", "-n", namespace, "logs", pod, "--all-containers=true", "--tail=1000", "--timestamps"], context.repo_root, timeout=90)
            storage.write_text(f"kubernetes/logs/{safe_name}.log", redact_text(logs))
            metadata["pods"].append({"kubernetes.namespace": namespace, "kubernetes.pod.name": pod.removeprefix("pod/"), "log_exit_code": log_code})
    json_code, pod_json = _capture(["kubectl", "-n", namespace, "get", "pods", "-o", "json"], context.repo_root, timeout=60)
    if json_code == 0:
        try:
            safe_pods = []
            for item in json.loads(pod_json).get("items", []):
                pod_name = str(item.get("metadata", {}).get("name", ""))
                for container in item.get("spec", {}).get("containers", []):
                    safe_pods.append(
                        {
                            "kubernetes.namespace": namespace,
                            "kubernetes.pod.name": pod_name,
                            "kubernetes.container.name": str(container.get("name", "")),
                            "container.image": str(container.get("image", "")),
                        }
                    )
            storage.write_json("kubernetes/pods-metadata.json", safe_pods)
            metadata["containers"] = safe_pods
        except (json.JSONDecodeError, AttributeError):
            metadata["commands"]["pods-metadata.json"] = 1
    storage.write_json("kubernetes/metadata.json", metadata)
    return metadata


def _capture(argv: list[str], cwd: Path, *, timeout: float = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}\n"
