"""Independent dependency/source security gates."""
from __future__ import annotations

import json

from _common import sh, stage, with_source
from _images import (
    GOLANG_IMAGE,
    PYTHON_BOOKWORM_IMAGE,
    PYTHON_SLIM_IMAGE,
    RUST_IMAGE,
    TRIVY_IMAGE,
)
from kubernetes import KUBECTL_SHA256, KUBECTL_VERSION


SECURITY_REQUIREMENTS = (
    ".github/dagger/requirements-bootstrap.txt",
    ".github/dagger/requirements-release.txt",
    ".github/dagger/requirements-reliability.txt",
    "distributed-backend/ci-cd/requirements.txt",
    "distributed-backend/observability/requirements-test.txt",
    "distributed-backend/observability/requirements.txt",
    "distributed-backend/tests/e2e/requirements.txt",
    "distributed-backend/tests/property-tests/e2e/requirements.txt",
    "distributed-backend/tests/property-tests/infra/requirements.txt",
    "simulator/requirements-test.txt",
    "simulator/requirements.txt",
)
RUST_ADVISORY_ALLOWLIST = ".github/security-advisory-allowlist.json"
RUST_IGNORED_ADVISORY = "RUSTSEC-2023-0071"
RUST_IGNORED_DEPENDENCY = "rsa"
RUST_TARGET = "x86_64-unknown-linux-gnu"
KUSTOMIZE_TARGETS = (
    "distributed-backend/orchestration/kubernetes/platform/istio/prod",
    "distributed-backend/orchestration/kubernetes/platform/gateway/prod",
    "distributed-backend/orchestration/kubernetes/base/observability",
    "distributed-backend/orchestration/kubernetes/overlay/local",
    "distributed-backend/orchestration/kubernetes/overlay/prod",
    "distributed-backend/orchestration/kubernetes/chaos/litmus/overlays/prod",
)
GO_AUDIT_COMMANDS = (
    "go version",
    "go install golang.org/x/vuln/cmd/govulncheck@v1.5.0",
    "govulncheck -version",
    "govulncheck ./...",
)
RUST_AUDIT_COMMANDS = (
    "cargo install cargo-audit --locked --version 0.22.2",
    f'test -z "$(cargo tree --locked --target {RUST_TARGET} '
    f'--all-features -i {RUST_IGNORED_DEPENDENCY})"',
    f"cargo audit --deny warnings --ignore {RUST_IGNORED_ADVISORY}",
)
PYTHON_AUDIT_COMMANDS = (
    "python -m pip install --disable-pip-version-check 'pip-audit==2.10.1'",
    f"python .github/scripts/validate_security_advisory_allowlist.py {RUST_ADVISORY_ALLOWLIST}",
    *(f"pip-audit --requirement {path}" for path in SECURITY_REQUIREMENTS),
)
TRIVY_SOURCE_ARGUMENTS = (
    "trivy",
    "fs",
    "--scanners",
    "vuln,secret",
    "--severity",
    "HIGH,CRITICAL",
    "--ignore-unfixed",
    "--ignorefile",
    ".trivyignore.yaml",
    "--show-suppressed",
    "--exit-code",
    "1",
    "--no-progress",
    ".",
)
TRIVY_CONFIGURATION_ARGUMENTS = (
    "trivy",
    "fs",
    "--scanners",
    "misconfig",
    "--severity",
    "HIGH,CRITICAL",
    "--ignorefile",
    ".trivyignore.yaml",
    "--show-suppressed",
    "--exit-code",
    "1",
    "--no-progress",
    ".",
)
TRIVY_CANARY_ARGUMENTS = (
    "trivy",
    "fs",
    "--scanners",
    "secret",
    "--format",
    "json",
    "--output",
    "/tmp/trivy-canary.json",
    "--exit-code",
    "0",
    "--no-progress",
    "/canary",
)


def trivy_secret_findings(document: object) -> tuple[tuple[str, str], ...]:
    """Extract target/rule identities from Trivy JSON, rejecting bad schemas."""
    if not isinstance(document, dict) or not isinstance(document.get("Results"), list):
        raise RuntimeError("Trivy canary output has no Results list")
    findings: list[tuple[str, str]] = []
    for result in document["Results"]:
        if not isinstance(result, dict):
            raise RuntimeError("Trivy canary result must be an object")
        secrets = result.get("Secrets") or []
        if not isinstance(secrets, list):
            raise RuntimeError("Trivy Secrets field must be a list")
        for secret in secrets:
            if not isinstance(secret, dict):
                raise RuntimeError("Trivy secret finding must be an object")
            findings.append((str(result.get("Target", "")), str(secret.get("RuleID", ""))))
    return tuple(findings)


def assert_trivy_canary_detected(document: object, *, target: str = "credentials") -> None:
    matching = [item for item in trivy_secret_findings(document) if item[0].endswith(target) and item[1]]
    if not matching:
        raise RuntimeError(f"Trivy did not detect the known fake secret in {target!r}")


def _fake_secret_canary() -> str:
    # Split the nonfunctional fixture so repository source scanning does not
    # mistake the generator itself for a credential. The materialized file is
    # intentionally shaped like AWS credentials and contains no usable secret.
    access_key = "AK" + "IAZZZZZZZZZZZZZZZZ"
    secret_key = "wJalrXUtnFEMI/" + "K7MDENG/bPxRfiCYFAKESECRET1"
    return (
        "[canary]\n"
        f"aws_access_key_id = {access_key}\n"
        f"aws_secret_access_key = {secret_key}\n"
    )


async def run(dag) -> None:
    import anyio

    async def go_audit() -> None:
        ctr = with_source(dag.container().from_(GOLANG_IMAGE), dag)
        ctr = sh(ctr, GO_AUDIT_COMMANDS)
        await ctr.sync()

    async def rust_audit() -> None:
        ctr = with_source(dag.container().from_(RUST_IMAGE), dag)
        ctr = ctr.with_workdir("/src/distributed-backend/src/trade-settlement")
        ctr = sh(
            ctr,
            # Ignoring the lockfile advisory is permitted only while the
            # affected crate remains absent from the complete production
            # feature graph. cargo-tree uses an empty result for absence.
            RUST_AUDIT_COMMANDS,
        )
        await ctr.sync()

    async def python_audit() -> None:
        ctr = with_source(dag.container().from_(PYTHON_SLIM_IMAGE), dag)
        ctr = sh(ctr, PYTHON_AUDIT_COMMANDS)
        await ctr.sync()

    async def trivy_source() -> None:
        ctr = dag.container().from_(TRIVY_IMAGE).with_entrypoint([])
        ctr = with_source(ctr, dag)
        ctr = ctr.with_exec(list(TRIVY_SOURCE_ARGUMENTS))
        await ctr.sync()

    async def trivy_rendered_configuration() -> None:
        renderer = dag.container().from_(PYTHON_BOOKWORM_IMAGE)
        renderer = sh(
            renderer,
            [
                "apt-get update",
                "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates",
                "rm -rf /var/lib/apt/lists/*",
                f"curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/{KUBECTL_VERSION}/bin/linux/amd64/kubectl",
                f"echo '{KUBECTL_SHA256}  /usr/local/bin/kubectl' | sha256sum -c -",
                "chmod +x /usr/local/bin/kubectl",
            ],
        )
        renderer = with_source(renderer, dag)
        renderer = sh(
            renderer,
            [
                "mkdir -p /tmp/rendered-kubernetes",
                *(
                    f"kubectl kustomize {target} >"
                    f"/tmp/rendered-kubernetes/{index:02d}.yaml"
                    for index, target in enumerate(KUSTOMIZE_TARGETS)
                ),
            ],
        )

        ctr = dag.container().from_(TRIVY_IMAGE).with_entrypoint([])
        ctr = with_source(ctr, dag)
        ctr = ctr.with_directory(
            "/src/.rendered-kubernetes",
            renderer.directory("/tmp/rendered-kubernetes"),
        )
        ctr = ctr.with_exec(list(TRIVY_CONFIGURATION_ARGUMENTS))
        await ctr.sync()

    async def trivy_secret_canary() -> None:
        ctr = dag.container().from_(TRIVY_IMAGE).with_entrypoint([])
        ctr = ctr.with_new_file("/canary/credentials", contents=_fake_secret_canary())
        ctr = ctr.with_exec(list(TRIVY_CANARY_ARGUMENTS))
        document = json.loads(await ctr.file("/tmp/trivy-canary.json").contents())
        assert_trivy_canary_detected(document)

    failures = {}

    async def guarded(name, fn):
        try:
            await fn()
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"

    async with anyio.create_task_group() as tg:
        tg.start_soon(guarded, "go-audit", go_audit)
        tg.start_soon(guarded, "rust-audit", rust_audit)
        tg.start_soon(guarded, "python-audit", python_audit)
        tg.start_soon(guarded, "trivy-source", trivy_source)
        tg.start_soon(guarded, "trivy-rendered-configuration", trivy_rendered_configuration)
        tg.start_soon(guarded, "trivy-secret-canary", trivy_secret_canary)

    if failures:
        raise RuntimeError("security subcheck failures: " + repr(failures))
    return {
        "subchecks": [
            "go-audit",
            "rust-audit",
            "python-audit",
            "trivy-source",
            "trivy-rendered-configuration",
            "trivy-secret-canary",
        ]
    }


if __name__ == "__main__":
    stage("security", run)
