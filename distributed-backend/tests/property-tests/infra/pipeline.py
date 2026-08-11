#!/usr/bin/env python3
"""Dagger-owned property-test pipeline with disposable Kind and supplied-cluster modes."""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import sys
from pathlib import Path

import dagger


REPO_ROOT = Path(__file__).resolve().parents[4]
PYTHON_IMAGE = "python:3.13-bookworm@sha256:62eafe52c91cad83c2c74e630bfde917da8c253673e695665d454def84fc9a13"
GOLANG_IMAGE = "golang:1.26.5-bookworm@sha256:53eeac89074db483fdf0ab3be1df32bf6e47562263d2d0d6baa7f26acb4957dd"
DEBIAN_IMAGE = "debian:bookworm-slim@sha256:abd67ffcfa541b485a3dff59865ab629aa048a6c613e639d36e7456b0b229241"
KIND_VERSION = "v0.31.0"
KIND_SHA256 = "eb244cbafcc157dff60cf68693c14c9a75c4e6e6fedaf9cd71c58117cb93e3fa"
KIND_NODE_IMAGE = "kindest/node:v1.34.3@sha256:08497ee19eace7b4b5348db5c6a1591d7752b164530a36f855cb0f2bdcbadd48"
KUBECTL_VERSION = "v1.33.0"
KUBECTL_SHA256 = "9efe8d3facb23e1618cba36fb1c4e15ac9dc3ed5a2c2e18109e4a66b2bac12dc"
HELM_VERSION = "v3.17.3"
HELM_SHA256 = "ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd"
TERRAFORM_VERSION = "1.15.8"
TERRAFORM_SHA256 = "d25ce7b6902013ad905db3d2eab0be4cd905887fe88b81a6171b8d5503c31f3d"
BUF_VERSION = "1.70.0"
BUF_SHA256 = "e2bbcdd324da09c16a15963dc2dae0525c955c05dc118223cf732f4f7509c5e6"
RUSTUP_VERSION = "1.28.2"
RUSTUP_SHA256 = "20a06e644b0d9bd2fbdbfd52d42540bdde820ea7df86e92e533c073da0cdd43c"
RUST_TOOLCHAIN = "1.95.0"
ENCORE_VERSION = "1.57.9"
ENCORE_SHA256 = "dfd43dcd456f91414a823315480da921333e6d1e3535ab48c47c09225d022af5"
SOURCE_EXCLUDES = [
    ".git", ".agents", ".codex", ".codex-task-state", ".terraform",
    ".venv", ".ci-venv", ".dagger-ci-venv", ".dagger-ci-bin",
    ".o11y", "target", "node_modules", "**/__pycache__", "**/.pytest_cache", "**/*.pyc",
]
FORWARDED_ENV = (
    "EVE_TRADE_ENCORE_URL",
    "EVE_TRADE_SIMULATOR_URL",
    "EVE_TRADE_SETTLEMENT_GRPC",
    "EVE_TRADE_NSQ_TCP",
    "EVE_TRADE_NSQ_HTTP",
    "EVE_TRADE_DATABASE_URL",
    "EVE_TRADE_MARKET_DATABASE_URL",
    "EVE_TRADE_RUNTIME_DATABASE_URL",
    "EVE_TRADE_QUILKIN_UDP_HOST",
    "EVE_TRADE_QUILKIN_UDP_PORT",
    "EVE_TRADE_EDGE_RESPONSE_KEY_ID",
    "EVE_TRADE_EDGE_RESPONSE_SECRET",
    "EVE_TRADE_EDGE_SELLER_KEY_ID",
    "EVE_TRADE_EDGE_SELLER_SECRET",
    "EVE_TRADE_EDGE_BUYER_KEY_ID",
    "EVE_TRADE_EDGE_BUYER_SECRET",
    "EVE_TRADE_EDGE_OTHER_KEY_ID",
    "EVE_TRADE_EDGE_OTHER_SECRET",
    "EVE_TRADE_APP_NAMESPACE",
    "EVE_TRADE_DISPOSABLE_CONTEXT",
)


def with_forwarded_environment(
    container: dagger.Container,
    client: dagger.Client,
) -> dagger.Container:
    """Forward integration settings without exposing credentials in DAG metadata."""
    for name in FORWARDED_ENV:
        value = os.environ.get(name)
        if value:
            container = container.with_secret_variable(
                name,
                client.set_secret(f"eve-trade-property-{name.lower()}", value),
            )
    return container


def run_id(explicit: str | None) -> str:
    value = (explicit or os.environ.get("EVE_TRADE_TEST_RUN_ID") or "").strip().lower()
    if not value:
        import secrets

        value = "pt-" + secrets.token_hex(8)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("run ID must be a DNS-safe label of at most 63 characters")
    return value


def docker_socket_path() -> Path:
    return Path(os.environ.get("DOCKER_HOST_SOCKET", "/var/run/docker.sock"))


def tool_bootstrap() -> str:
    return f"""
set -euo pipefail
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends docker.io postgresql-client python3 python3-venv python3-pip curl ca-certificates git unzip
rm -rf /var/lib/apt/lists/*
curl -fsSLo /usr/local/bin/kind https://kind.sigs.k8s.io/dl/{KIND_VERSION}/kind-linux-amd64
curl -fsSLo /usr/local/bin/kubectl https://dl.k8s.io/release/{KUBECTL_VERSION}/bin/linux/amd64/kubectl
curl -fsSLo /tmp/helm.tar.gz https://get.helm.sh/helm-{HELM_VERSION}-linux-amd64.tar.gz
curl -fsSLo /tmp/terraform.zip https://releases.hashicorp.com/terraform/{TERRAFORM_VERSION}/terraform_{TERRAFORM_VERSION}_linux_amd64.zip
curl -fsSLo /usr/local/bin/buf https://github.com/bufbuild/buf/releases/download/v{BUF_VERSION}/buf-Linux-x86_64
curl -fsSLo /tmp/rustup-init https://static.rust-lang.org/rustup/archive/{RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init
echo '{KIND_SHA256}  /usr/local/bin/kind' | sha256sum --check --strict
echo '{KUBECTL_SHA256}  /usr/local/bin/kubectl' | sha256sum --check --strict
echo '{HELM_SHA256}  /tmp/helm.tar.gz' | sha256sum --check --strict
echo '{TERRAFORM_SHA256}  /tmp/terraform.zip' | sha256sum --check --strict
echo '{BUF_SHA256}  /usr/local/bin/buf' | sha256sum --check --strict
echo '{RUSTUP_SHA256}  /tmp/rustup-init' | sha256sum --check --strict
tar -xzf /tmp/helm.tar.gz -C /tmp
install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm
unzip -q /tmp/terraform.zip -d /usr/local/bin
chmod 0755 /tmp/rustup-init
/tmp/rustup-init -y --profile minimal --default-toolchain {RUST_TOOLCHAIN}
ln -sf /root/.cargo/bin/cargo /usr/local/bin/cargo
ln -sf /root/.cargo/bin/rustc /usr/local/bin/rustc
rm -rf /tmp/helm.tar.gz /tmp/linux-amd64 /tmp/terraform.zip /tmp/rustup-init
chmod 0755 /usr/local/bin/kind /usr/local/bin/kubectl /usr/local/bin/buf /usr/local/bin/terraform
python3 -m venv /opt/property-venv
. /opt/property-venv/bin/activate
python -m pip install --disable-pip-version-check \
  -r distributed-backend/tests/e2e/requirements.txt \
  -r distributed-backend/tests/property-tests/e2e/requirements.txt \
  -r distributed-backend/tests/property-tests/infra/requirements.txt
"""


async def validate_source(client: dagger.Client, source: dagger.Directory) -> None:
    script = """
set -euo pipefail
python -m pip install --disable-pip-version-check \
  -r distributed-backend/tests/property-tests/e2e/requirements.txt \
  -r distributed-backend/tests/property-tests/infra/requirements.txt
python -m compileall -q distributed-backend/tests/property-tests/e2e distributed-backend/tests/property-tests/infra
python distributed-backend/tests/property-tests/e2e/tools/sync_authoritative_catalog.py --check
python distributed-backend/tests/property-tests/infra/generate_contract_manifests.py --check
python distributed-backend/tests/property-tests/e2e/tools/generate_hypothesis_audit.py --check
python distributed-backend/tests/property-tests/e2e/tools/generate_delivery_integrity.py --check
python -m pytest -q distributed-backend/tests/property-tests/e2e/tests
python distributed-backend/tests/property-tests/infra/verify_collection.py
"""
    container = (
        client.container()
        .from_(PYTHON_IMAGE)
        .with_directory("/src", source)
        .with_workdir("/src")
        .with_exec(["bash", "-euo", "pipefail", "-c", script])
    )
    output = await container.stdout()
    if output.strip():
        print(output, flush=True)


async def run_pipeline(args: argparse.Namespace) -> None:
    rid = run_id(args.run_id)
    source = args.client.host().directory(str(REPO_ROOT), exclude=SOURCE_EXCLUDES, gitignore=True)
    await validate_source(args.client, source)
    if args.mode == "structural":
        return
    artifacts = REPO_ROOT / ".o11y" / "runs" / f"local-property-{rid}"
    artifacts.mkdir(parents=True, exist_ok=True)

    if args.mode == "supplied":
        if not args.kubeconfig:
            raise ValueError("--kubeconfig is required in supplied mode")
        kubeconfig = Path(args.kubeconfig).expanduser().resolve()
        if not kubeconfig.is_file():
            raise ValueError(f"supplied kubeconfig does not exist: {kubeconfig}")
        if not os.environ.get("EVE_TRADE_DISPOSABLE_CONTEXT"):
            raise ValueError("supplied mode requires EVE_TRADE_DISPOSABLE_CONTEXT")
        supplied = (
            args.client.container()
            .from_(GOLANG_IMAGE)
            .with_directory("/src", source)
            .with_file("/kubeconfig", args.client.host().file(str(kubeconfig)))
            .with_workdir("/src")
            .with_env_variable("KUBECONFIG", "/kubeconfig")
            .with_env_variable("EVE_TRADE_TEST_RUN_ID", rid)
            .with_env_variable("EVE_TRADE_PROPERTY_SUITE", args.suite)
            .with_env_variable(
                "EVE_TRADE_HYPOTHESIS_CHAOS_EXAMPLES", str(args.chaos_examples)
            )
            .with_env_variable("EVE_TRADE_PROPERTY_ARTIFACTS", "/artifacts")
        )
        supplied = with_forwarded_environment(supplied, args.client)
        executed = supplied.with_exec(
            [
                "bash", "-euo", "pipefail", "-c",
                "mkdir -p /artifacts\n"
                + tool_bootstrap()
                + "\nbash distributed-backend/tests/property-tests/infra/run_supplied_property_tests.sh",
            ],
            expect=dagger.ReturnType.ANY,
        )
        output = await executed.stdout()
        error_output = await executed.stderr()
        exit_status = await executed.exit_code()
        await executed.directory("/artifacts").export(str(artifacts))
        if output.strip():
            print(output, flush=True)
        if error_output.strip():
            print(error_output, file=sys.stderr, flush=True)
        print(f"property evidence: {artifacts}", flush=True)
        if exit_status != 0:
            raise RuntimeError(
                f"supplied-cluster property runner exited {exit_status}; artifacts were exported"
            )
        return

    socket_path = docker_socket_path()
    if not socket_path.exists():
        raise RuntimeError(f"Docker socket is required for Kind property pipeline: {socket_path}")
    outer = (
        args.client.container()
        .from_(DEBIAN_IMAGE)
        .with_directory("/validated-src", source)
        .with_unix_socket("/var/run/docker.sock", args.client.host().unix_socket(str(socket_path)))
        .with_env_variable("EVE_TRADE_TEST_RUN_ID", rid)
        .with_env_variable("EVE_TRADE_PROPERTY_SUITE", args.suite)
        .with_env_variable(
            "EVE_TRADE_HYPOTHESIS_CHAOS_EXAMPLES", str(args.chaos_examples)
        )
        .with_env_variable("EVE_TRADE_PROPERTY_ARTIFACTS", "/artifacts")
        .with_env_variable("ENCORE_CLI_VERSION", ENCORE_VERSION)
        .with_env_variable("ENCORE_CLI_SHA256", ENCORE_SHA256)
        .with_env_variable(
            "EVE_TRADE_E2E_RUNNER",
            "distributed-backend/tests/property-tests/infra/run_kind_property_tests.sh",
        )
        .with_env_variable("EVE_TRADE_KIND_NODE_IMAGE", KIND_NODE_IMAGE)
        .with_exec(
            [
                "bash", "-euo", "pipefail", "-c",
                "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y "
                "--no-install-recommends docker.io ca-certificates && "
                "rm -rf /var/lib/apt/lists/*",
            ]
        )
    )
    outer = with_forwarded_environment(outer, args.client)
    runner = f"""
export PATH=/opt/encore/bin:$PATH
ENCORE_INSTALL=/opt/encore bash scripts/install_encore_cli.sh
bash scripts/harden_encore_install.sh /opt/encore
export EVE_TRADE_KIND_CLUSTER=eve-trade-{rid}
bash scripts/run_kind_e2e.sh
"""
    inner = "mkdir -p /artifacts\n" + tool_bootstrap() + "\n" + runner
    container_name = f"eve-trade-property-{rid}"
    docker_env = [
        "-e", f"EVE_TRADE_TEST_RUN_ID={rid}",
        "-e", f"EVE_TRADE_PROPERTY_SUITE={args.suite}",
        "-e", f"EVE_TRADE_HYPOTHESIS_CHAOS_EXAMPLES={args.chaos_examples}",
        "-e", "EVE_TRADE_PROPERTY_ARTIFACTS=/artifacts",
        "-e", f"ENCORE_CLI_VERSION={ENCORE_VERSION}",
        "-e", f"ENCORE_CLI_SHA256={ENCORE_SHA256}",
        "-e", "EVE_TRADE_E2E_RUNNER=distributed-backend/tests/property-tests/infra/run_kind_property_tests.sh",
        "-e", f"EVE_TRADE_KIND_NODE_IMAGE={KIND_NODE_IMAGE}",
    ]
    for name in FORWARDED_ENV:
        if os.environ.get(name):
            docker_env.extend(["-e", name])
    create_command = shlex.join(
        [
            "docker", "create", "--name", container_name, "--network", "host",
            *docker_env,
            "-w", "/src", GOLANG_IMAGE,
            "bash", "/tmp/eve-kind-inner.sh",
        ]
    )
    launch = f"""
set -euo pipefail
container_name={shlex.quote(container_name)}
created=0
cleanup() {{
  if [ "$created" = 1 ]; then
    docker rm -f "$container_name" >/dev/null 2>&1 || true
  fi
}}
trap cleanup EXIT
{create_command}
created=1
docker cp /validated-src/. "$container_name:/src"
docker cp /eve-kind-inner.sh "$container_name:/tmp/eve-kind-inner.sh"
set +e
docker start -a "$container_name"
status=$?
set -e
mkdir -p /artifacts
docker cp "$container_name:/artifacts/." /artifacts/
exit "$status"
"""
    executed = outer.with_new_file("/eve-kind-inner.sh", inner).with_exec(
        [
            "bash", "-euo", "pipefail", "-c",
            launch,
        ],
        expect=dagger.ReturnType.ANY,
    )
    output = await executed.stdout()
    error_output = await executed.stderr()
    exit_status = await executed.exit_code()
    await executed.directory("/artifacts").export(str(artifacts))
    if output.strip():
        print(output, flush=True)
    if error_output.strip():
        print(error_output, file=sys.stderr, flush=True)
    print(f"property evidence: {artifacts}", flush=True)
    if exit_status != 0:
        raise RuntimeError(
            f"Kind property runner exited {exit_status}; artifacts were exported"
        )


async def async_main(args: argparse.Namespace) -> None:
    async with dagger.Connection(dagger.Config(log_output=sys.stderr)) as client:
        args.client = client
        await run_pipeline(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("structural", "kind", "supplied"), default="kind")
    parser.add_argument("--suite", choices=("structural", "implemented", "full-strict"), default="implemented")
    parser.add_argument("--kubeconfig")
    parser.add_argument("--run-id")
    parser.add_argument("--chaos-examples", type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    asyncio.run(async_main(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
