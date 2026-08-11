"""Kind end-to-end stage controlled by Dagger.

Kind is run in a host-networked driver container because its kubeconfig points to
the GitHub runner loopback while the cluster nodes live in the runner Docker
daemon. Dagger remains the only CI orchestrator.
"""
from __future__ import annotations

import json
import os
import re
import sys
from _common import REPO_ROOT, StageFailure, redact_text, sh, stage, with_docker_socket, with_env
from _images import DEBIAN_IMAGE, GOLANG_IMAGE

KIND_VERSION = "v0.31.0"
KIND_SHA256 = "eb244cbafcc157dff60cf68693c14c9a75c4e6e6fedaf9cd71c58117cb93e3fa"
KUBECTL_VERSION = "v1.33.0"
KUBECTL_SHA256 = "9efe8d3facb23e1618cba36fb1c4e15ac9dc3ed5a2c2e18109e4a66b2bac12dc"
KIND_NODE_IMAGE = "kindest/node:v1.34.3@sha256:08497ee19eace7b4b5348db5c6a1591d7752b164530a36f855cb0f2bdcbadd48"
ENCORE_VERSION = "1.57.9"
ENCORE_SHA256 = "dfd43dcd456f91414a823315480da921333e6d1e3535ab48c47c09225d022af5"
E2E_SCRIPT = "scripts/run_kind_e2e.sh"


async def run(dag, e2e_script: str = E2E_SCRIPT) -> None:
    if e2e_script != E2E_SCRIPT:
        raise RuntimeError(f"unexpected E2E script: {e2e_script}")
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace:
        raise RuntimeError("GITHUB_WORKSPACE is required for the host-Docker Kind driver")
    # E2E intentionally uses the local collector.  No telemetry credential is
    # exposed to PR-controlled source, package installers, or nested containers.
    collector = "./distributed-backend/observability/collector/otel-collector.local.yaml"

    ctr = dag.container().from_(DEBIAN_IMAGE)
    ctr = sh(
        ctr,
        [
            "apt-get update",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io ca-certificates",
            "rm -rf /var/lib/apt/lists/*",
        ],
    )
    ctr = with_docker_socket(ctr, dag)
    ctr = with_env(
        ctr,
        {
            "OBSERVABILITY_ENV": "github-actions",
            "OBSERVABILITY_STRICT": "true",
            "HONEYCOMB_DATASET": "eve-trade-ci",
            "OTEL_COLLECTOR_CONFIG": collector,
        },
    )
    driver = f'''docker run --rm --network host \\
      -v /var/run/docker.sock:/var/run/docker.sock \\
      -v "{workspace}:/src" -w /src \\
      -e OBSERVABILITY_ENV -e OBSERVABILITY_STRICT -e HONEYCOMB_DATASET -e OTEL_COLLECTOR_CONFIG \\
      -e ENCORE_CLI_VERSION={ENCORE_VERSION} -e ENCORE_CLI_SHA256={ENCORE_SHA256} \\
      {GOLANG_IMAGE} bash -euo pipefail -c '
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io postgresql-client python3 python3-venv python3-pip curl ca-certificates
        rm -rf /var/lib/apt/lists/*
        curl -fsSLo /usr/local/bin/kind https://kind.sigs.k8s.io/dl/{KIND_VERSION}/kind-linux-amd64
        curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/{KUBECTL_VERSION}/bin/linux/amd64/kubectl
        echo "{KIND_SHA256}  /usr/local/bin/kind" | sha256sum -c -
        echo "{KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c -
        chmod +x /usr/local/bin/kind /usr/local/bin/kubectl
        python3 -m venv /opt/ci-venv
        . /opt/ci-venv/bin/activate
        python -m pip install --disable-pip-version-check -r distributed-backend/tests/e2e/requirements.txt -r distributed-backend/observability/requirements.txt
        ENCORE_INSTALL=/opt/encore bash scripts/install_encore_cli.sh
        bash scripts/harden_encore_install.sh /opt/encore
        export PATH=/opt/encore/bin:$PATH
        cleanup() {{ kind delete cluster --name eve-trade-ci >/dev/null 2>&1 || true; }}
        trap cleanup EXIT
        kind create cluster --name eve-trade-ci --config .github/kind-e2e.yaml --image {KIND_NODE_IMAGE} --wait 120s
        bash {e2e_script}
      ' '''
    executed = sh(ctr, [
        "set +e",
        f"{driver} 2>&1 | tee /tmp/e2e.log",
        "rc=${PIPESTATUS[0]}",
        "set -e",
        "printf '%s\\n' \"$rc\" > /tmp/e2e-exit-code",
        "exit 0",
    ])
    await executed.sync()
    log = await executed.file("/tmp/e2e.log").contents()
    safe_log = redact_text(log)
    if len(safe_log) > 2_000_000:
        safe_log = safe_log[:1_000_000] + "\n... <redacted E2E log truncated> ...\n" + safe_log[-1_000_000:]
    rc = int((await executed.file("/tmp/e2e-exit-code").contents()).strip())
    log_path = REPO_ROOT / ".o11y" / "e2e.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(safe_log, encoding="utf-8")
    matches = re.findall(r"(?m)^E2E_SUMMARY=(\{.*\})$", safe_log)
    summary = json.loads(matches[-1]) if matches else None
    if isinstance(summary, dict):
        print("E2E_SUMMARY=" + json.dumps(summary, separators=(",", ":"), sort_keys=True), flush=True)
    if rc:
        raise StageFailure(
            f"observed E2E run failed with exit code {rc}; log captured",
            payload={"exit_code": rc, "e2e_summary": summary, "log_tail": safe_log[-4000:]},
        )
    if summary is None:
        raise StageFailure(
            "observed E2E run succeeded but emitted no E2E_SUMMARY evidence",
            payload={"exit_code": rc, "log_tail": safe_log[-4000:]},
        )
    if int(summary.get("collected_count", 0)) <= 0 or int(summary.get("passed_count", 0)) <= 0:
        raise StageFailure(f"invalid passing E2E summary: {summary}", payload={"exit_code": rc, "e2e_summary": summary, "log_tail": safe_log[-4000:]})
    if int(summary.get("failed_count", 0)) or int(summary.get("error_count", 0)):
        raise StageFailure(f"E2E summary reports failures: {summary}", payload={"exit_code": rc, "e2e_summary": summary, "log_tail": safe_log[-4000:]})
    if float(summary.get("duration_seconds", 0.0)) <= 0:
        raise StageFailure(f"E2E summary reports zero duration: {summary}", payload={"exit_code": rc, "e2e_summary": summary, "log_tail": safe_log[-4000:]})
    evidence = REPO_ROOT / ".o11y" / "e2e-summary.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"e2e_summary": summary}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != E2E_SCRIPT:
        raise SystemExit(f"usage: {sys.argv[0]} {E2E_SCRIPT}")
    stage("e2e", lambda dag: run(dag, sys.argv[1]))
