"""Kubernetes/Kustomize rendering and policy stage."""
from __future__ import annotations

from _common import sh, stage, with_source
from _images import PYTHON_BOOKWORM_IMAGE

KUBECTL_VERSION = "v1.33.0"
KUBECTL_SHA256 = "9efe8d3facb23e1618cba36fb1c4e15ac9dc3ed5a2c2e18109e4a66b2bac12dc"


async def run(dag) -> None:
    ctr = dag.container().from_(PYTHON_BOOKWORM_IMAGE)
    ctr = sh(
        ctr,
        [
            "apt-get update",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates",
            "rm -rf /var/lib/apt/lists/*",
            f"curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/{KUBECTL_VERSION}/bin/linux/amd64/kubectl",
            f"echo '{KUBECTL_SHA256}  /usr/local/bin/kubectl' | sha256sum -c -",
            "chmod +x /usr/local/bin/kubectl",
            "kubectl version --client",
        ],
    )
    ctr = with_source(ctr, dag)
    ctr = sh(
        ctr,
        [
            "kubectl kustomize distributed-backend/orchestration/kubernetes/platform/istio/prod >/tmp/eve-trade-istio.yaml",
            "kubectl kustomize distributed-backend/orchestration/kubernetes/platform/gateway/prod >/tmp/eve-trade-gateway.yaml",
            "kubectl kustomize distributed-backend/orchestration/kubernetes/base/observability >/tmp/eve-trade-observability.yaml",
            "kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/local >/tmp/eve-trade-local.yaml",
            "kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod >/tmp/eve-trade-prod.yaml",
            "kubectl kustomize distributed-backend/orchestration/kubernetes/chaos/litmus/overlays/prod >/tmp/eve-trade-chaos.yaml",
            "python scripts/verify_rendered_kubernetes.py --allow-application-image-templates /tmp/eve-trade-prod.yaml",
            "python -m unittest -v scripts.tests.test_verify_rendered_kubernetes",
        ],
    )
    await ctr.sync()


if __name__ == "__main__":
    stage("kubernetes", run)
