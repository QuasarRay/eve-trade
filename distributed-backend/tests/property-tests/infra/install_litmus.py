#!/usr/bin/env python3
"""Install the exact pinned Litmus operator and fault resources into a safe namespace."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml


INFRA_ROOT = Path(__file__).resolve().parent
LITMUS_HELM_REPOSITORY = "https://github.com/litmuschaos/litmus-helm.git"
CHAOS_CHARTS_REPOSITORY = "https://github.com/litmuschaos/chaos-charts.git"
LITMUS_HELM_COMMIT = "9e409651ce4c2293a44de56ba46ac9ab9fe1d329"
CHAOS_CHARTS_COMMIT = "57ffd877b859a71a05821bb66ff9be76d981d7e4"
LITMUS_RELEASE_TAG = "litmus-core-3.31.0"
LITMUS_AGENT_CHART = "litmus-agent"
LITMUS_AGENT_CHART_VERSION = "3.30.0"
LITMUS_OPERATOR_IMAGE_TAG = "3.30.0"
LITMUS_RUNNER_IMAGE_TAG = "3.30.0"
HELM_RELEASE = "eve-trade-litmus"


class InstallError(RuntimeError):
    pass


def validate_manifest_pins(pins: dict[str, Any]) -> None:
    expected_pins = {
        "release_tag": LITMUS_RELEASE_TAG,
        "helm_git_commit": LITMUS_HELM_COMMIT,
        "chaos_charts_git_commit": CHAOS_CHARTS_COMMIT,
        "installed_chart": LITMUS_AGENT_CHART,
        "installed_chart_version": LITMUS_AGENT_CHART_VERSION,
        "operator_image_tag": LITMUS_OPERATOR_IMAGE_TAG,
        "runner_image_tag": LITMUS_RUNNER_IMAGE_TAG,
    }
    for key, expected in expected_pins.items():
        if pins.get(key) != expected:
            raise InstallError(
                f"canonical Litmus {key} pin {pins.get(key)!r} differs from installer {expected!r}"
            )


def validate_chart_pins(chart: Path) -> None:
    chart_metadata = yaml.safe_load((chart / "Chart.yaml").read_text(encoding="utf-8"))
    if not isinstance(chart_metadata, dict) or chart_metadata.get("name") != LITMUS_AGENT_CHART:
        raise InstallError("pinned Litmus checkout has an unexpected installed chart")
    if str(chart_metadata.get("version")) != LITMUS_AGENT_CHART_VERSION:
        raise InstallError("pinned Litmus checkout has an unexpected installed chart version")
    operator_metadata = yaml.safe_load(
        (chart / "charts" / "chaos-operator" / "Chart.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(operator_metadata, dict):
        raise InstallError("pinned Litmus checkout has no chaos-operator chart metadata")
    if str(operator_metadata.get("appVersion")) != LITMUS_OPERATOR_IMAGE_TAG:
        raise InstallError("pinned chaos-operator appVersion differs from the explicit image pin")


def run(
    command: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 180,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if check and process.returncode != 0:
        raise InstallError(
            f"command failed ({process.returncode}): {' '.join(command)}\n"
            f"stdout={process.stdout[-8192:]}\nstderr={process.stderr[-8192:]}"
        )
    return process


def clone_exact(repository: str, commit: str, destination: Path) -> None:
    run(["git", "init", "--quiet", str(destination)])
    run(["git", "-C", str(destination), "remote", "add", "origin", repository])
    run(
        ["git", "-C", str(destination), "fetch", "--depth=1", "origin", commit],
        timeout=300,
    )
    run(["git", "-C", str(destination), "checkout", "--quiet", "--detach", "FETCH_HEAD"])
    actual = run(["git", "-C", str(destination), "rev-parse", "HEAD"]).stdout.strip()
    if actual != commit:
        raise InstallError(f"pinned checkout mismatch: expected {commit}, got {actual}")


def apply_documents(namespace: str, documents: list[dict[str, Any]]) -> None:
    rendered = yaml.safe_dump_all(documents, sort_keys=False)
    run(["kubectl", "-n", namespace, "apply", "-f", "-"], input_text=rendered)


def rbac_documents(namespace: str, run_id: str) -> list[dict[str, Any]]:
    suffix = run_id[-24:]
    cluster_name = f"eve-trade-chaos-{suffix}"[:63].rstrip("-")
    labels = {"eve-trade.io/run-id": run_id, "eve-trade.io/managed-by": "property-tests"}
    return [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "eve-trade-chaos-runner", "namespace": namespace, "labels": labels},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {"name": "eve-trade-chaos-runner", "namespace": namespace, "labels": labels},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": [
                        "pods", "pods/log", "pods/exec", "events", "configmaps", "services",
                        "persistentvolumeclaims", "replicationcontrollers",
                    ],
                    "verbs": ["create", "delete", "deletecollection", "get", "list", "patch", "update", "watch"],
                },
                {
                    "apiGroups": ["apps", "apps.openshift.io", "argoproj.io"],
                    "resources": ["deployments", "statefulsets", "daemonsets", "replicasets", "deploymentconfigs", "rollouts"],
                    "verbs": ["get", "list", "patch", "update", "watch"],
                },
                {
                    "apiGroups": ["batch"],
                    "resources": ["jobs"],
                    "verbs": ["create", "delete", "deletecollection", "get", "list", "patch", "update", "watch"],
                },
                {
                    "apiGroups": ["networking.k8s.io"],
                    "resources": ["networkpolicies"],
                    "verbs": ["create", "delete", "deletecollection", "get", "list", "patch", "update", "watch"],
                },
                {
                    "apiGroups": ["litmuschaos.io"],
                    "resources": ["chaosengines", "chaosexperiments", "chaosresults"],
                    "verbs": ["create", "delete", "deletecollection", "get", "list", "patch", "update", "watch"],
                },
                {
                    "apiGroups": ["policy"],
                    "resources": ["pods/eviction"],
                    "verbs": ["create", "get"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": "eve-trade-chaos-runner", "namespace": namespace, "labels": labels},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "eve-trade-chaos-runner",
            },
            "subjects": [
                {"kind": "ServiceAccount", "name": "eve-trade-chaos-runner", "namespace": namespace}
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": cluster_name, "labels": labels},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": ["nodes"],
                    "verbs": ["get", "list", "patch", "update", "watch"],
                }
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": cluster_name, "labels": labels},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": cluster_name,
            },
            "subjects": [
                {"kind": "ServiceAccount", "name": "eve-trade-chaos-runner", "namespace": namespace}
            ],
        },
    ]


def assert_safety_marker(namespace: str, run_id: str) -> None:
    raw_namespace = json.loads(
        run(["kubectl", "get", "namespace", namespace, "-o", "json"]).stdout
    )
    labels = raw_namespace.get("metadata", {}).get("labels", {})
    if labels.get("eve-trade.io/chaos-safe") != "true" or labels.get("eve-trade.io/run-id") != run_id:
        raise InstallError("namespace is not marked chaos-safe for this exact run")
    raw_marker = json.loads(
        run(["kubectl", "-n", namespace, "get", "configmap", "eve-trade-chaos-safety", "-o", "json"]).stdout
    )
    if raw_marker.get("data", {}).get("run_id") != run_id:
        raise InstallError("chaos safety ConfigMap belongs to another run")


def wait_for_operator(namespace: str) -> None:
    """Wait for the pinned chart's operator without assuming one label dialect."""
    for selector in (
        "app.kubernetes.io/name=chaos-operator",
        "app=chaos-operator",
    ):
        process = run(
            [
                "kubectl", "-n", namespace, "get", "deployment",
                "-l", selector, "-o", "json",
            ]
        )
        items = json.loads(process.stdout).get("items", [])
        names = sorted(
            str(item.get("metadata", {}).get("name", ""))
            for item in items
            if item.get("metadata", {}).get("name")
        )
        if not names:
            continue
        for name in names:
            run(
                [
                    "kubectl", "-n", namespace, "rollout", "status",
                    f"deployment/{name}", "--timeout=180s",
                ],
                timeout=200,
            )
        return
    raise InstallError("pinned Litmus chart installed no discoverable chaos-operator deployment")


def install(namespace: str, run_id: str) -> None:
    assert_safety_marker(namespace, run_id)
    manifest = json.loads((INFRA_ROOT / "litmus-contracts.json").read_text(encoding="utf-8"))
    pins = manifest["litmus_core"]
    validate_manifest_pins(pins)
    experiments = sorted(
        {record["fault_injection"]["experiment"] for record in manifest["contracts"]}
    )
    with tempfile.TemporaryDirectory(prefix="eve-trade-litmus-") as temporary:
        root = Path(temporary)
        helm_checkout = root / "litmus-helm"
        charts_checkout = root / "chaos-charts"
        clone_exact(LITMUS_HELM_REPOSITORY, LITMUS_HELM_COMMIT, helm_checkout)
        clone_exact(CHAOS_CHARTS_REPOSITORY, CHAOS_CHARTS_COMMIT, charts_checkout)
        chart = helm_checkout / "charts" / LITMUS_AGENT_CHART
        validate_chart_pins(chart)
        run(
            [
                "helm", "upgrade", "--install", HELM_RELEASE, str(chart),
                "--namespace", namespace,
                "--set", "global.INFRA_MODE=namespace",
                "--set", "enablePreInstallJob=false",
                "--set", "subscriber.enabled=false",
                "--set", "event-tracker.enabled=false",
                "--set", "chaos-exporter.enabled=false",
                "--set", "workflow-controller.enabled=false",
                "--set", f"chaos-operator.image.tag={LITMUS_OPERATOR_IMAGE_TAG}",
                "--set", "chaos-operator.image.pullPolicy=IfNotPresent",
                "--set", f"chaos-operator.runner.image.tag={LITMUS_RUNNER_IMAGE_TAG}",
                "--wait", "--timeout", "240s",
            ],
            timeout=300,
        )
        apply_documents(namespace, rbac_documents(namespace, run_id))
        for experiment in experiments:
            path = charts_checkout / "faults" / "kubernetes" / experiment / "fault.yaml"
            if not path.is_file():
                raise InstallError(f"pinned Chaos Charts checkout lacks {experiment}: {path}")
            raw_documents = [
                document for document in yaml.safe_load_all(path.read_text(encoding="utf-8")) if document
            ]
            for document in raw_documents:
                document.setdefault("metadata", {})["namespace"] = namespace
                document["metadata"].setdefault("labels", {})["eve-trade.io/run-id"] = run_id
            apply_documents(namespace, raw_documents)
    wait_for_operator(namespace)
    for experiment in experiments:
        run(["kubectl", "-n", namespace, "get", "chaosexperiment", experiment])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    install(args.namespace, args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
