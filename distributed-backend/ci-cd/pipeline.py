from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import dagger


GO_IMAGE = "golang:1.26-bookworm@sha256:5f68ec6805843bd3981a951ffada82a26a0bd2631045c8f7dba483fa868f5ec5"
RUST_IMAGE = "rust:1-bookworm@sha256:19817ead3289c8c631c73df281e18b59b172f6a31f4f563290f69cddd06c30e9"
PYTHON_IMAGE = "python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f"
KUSTOMIZE_IMAGE = "alpine/k8s:1.33.1"
TERRAFORM_IMAGE = "hashicorp/terraform:1.10.5@sha256:679ac5e095bf550bc726742cd12efa6050f0913080df479fdabfeb202953af28"

ENCORE_CLI_VERSION = "1.57.9"
BUF_VERSION = "1.70.0"
PROTOC_GEN_GO_VERSION = "1.36.11"
SERVICE_IMAGE_NAMES = ("encore-backend", "trade-settlement", "quilkin")
DEPLOYMENT_NAMES = ("encore-backend", "trade-settlement")
STATEFULSET_NAMES = ("nsqd",)
TERRAFORM_ROOTS = {
    "aws": "distributed-backend/terraform/eks",
    "gcp": "distributed-backend/terraform/gke",
    "talos-omni": "distributed-backend/terraform/talos-omni",
}
SOURCE_EXCLUDES = [
    ".git",
    ".cache",
    ".ci-venv",
    ".venv",
    "ci-cd/out",
    ".o11y",
    "target",
    "distributed-backend/src/trade-settlement/target",
    "**/.pytest_cache",
    "**/__pycache__",
    "**/*.pyc",
]


def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def cloud_provider(explicit: str | None = None) -> str:
    aliases = {"eks": "aws", "gke": "gcp", "talos": "talos-omni", "omni": "talos-omni"}
    value = aliases.get((explicit or env("EVE_TRADE_CLOUD_PROVIDER", "aws")).lower(), explicit or env("EVE_TRADE_CLOUD_PROVIDER", "aws"))
    if value not in TERRAFORM_ROOTS:
        raise ValueError(f"deployment target must be one of: {', '.join(TERRAFORM_ROOTS)}")
    return value


def image_registry(explicit: str | None = None) -> str:
    return (explicit or env("IMAGE_REGISTRY") or env("CI_REGISTRY_IMAGE") or "registry.local/eve-trade").rstrip("/")


def image_tag(explicit: str | None = None) -> str:
    return explicit or env("IMAGE_TAG") or env("CI_COMMIT_TAG") or env("CI_COMMIT_SHORT_SHA") or env("CI_COMMIT_SHA", "local")


class EveTradePipeline:
    def __init__(self, client: dagger.Client):
        self.client = client
        self.source = client.host().directory(".", exclude=SOURCE_EXCLUDES, gitignore=True)

    async def run_container(self, title: str, container: dagger.Container) -> str:
        print(f"\n==> {title}", flush=True)
        output = await container.stdout()
        if output.strip():
            print(output, flush=True)
        return output

    def go_base(self) -> dagger.Container:
        install = f"""
set -euo pipefail
GOBIN=/usr/local/bin go install github.com/bufbuild/buf/cmd/buf@v{BUF_VERSION}
GOBIN=/usr/local/bin go install google.golang.org/protobuf/cmd/protoc-gen-go@v{PROTOC_GEN_GO_VERSION}
curl -L https://encore.dev/install.sh | bash -s -- --version {ENCORE_CLI_VERSION}
ln -sf /root/.encore/bin/encore /usr/local/bin/encore
"""
        return (
            self.client.container()
            .from_(GO_IMAGE)
            .with_mounted_cache("/go/pkg/mod", self.client.cache_volume("go-mod"))
            .with_mounted_cache("/root/.cache/go-build", self.client.cache_volume("go-build"))
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_exec(["bash", "-lc", install])
        )

    def rust_base(self) -> dagger.Container:
        return (
            self.client.container()
            .from_(RUST_IMAGE)
            .with_exec(["bash", "-lc", "apt-get update && apt-get install -y --no-install-recommends protobuf-compiler pkg-config ca-certificates && rm -rf /var/lib/apt/lists/*"])
            .with_exec(["rustup", "component", "add", "rustfmt", "clippy"])
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace/distributed-backend/src/trade-settlement")
        )

    async def proto_checks(self) -> None:
        script = "buf build && buf lint && buf format --diff --exit-code && buf generate && git diff --exit-code -- proto/gen"
        await self.run_container("protobuf contract checks", self.go_base().with_exec(["bash", "-lc", script]))

    async def go_checks(self) -> None:
        script = r"""
set -euo pipefail
go mod tidy
git diff --exit-code -- go.mod go.sum
test -z "$(gofmt -l gateway market settlement settlementworker internal proto/gen go_modules_test.go)"
encore test ./...
go vet ./...
"""
        await self.run_container("Encore Go checks", self.go_base().with_exec(["bash", "-lc", script]))

    async def rust_checks(self) -> None:
        script = "cargo fmt --all -- --check && cargo check --locked --all-targets --all-features && cargo test --locked --all-features && cargo clippy --locked --all-targets --all-features -- -D warnings"
        await self.run_container("Rust settlement checks", self.rust_base().with_exec(["bash", "-lc", script]))

    async def python_checks(self) -> None:
        script = r"""
set -euo pipefail
python -m compileall simulator/eve_trade_simulator simulator/trade_gui distributed-backend/tests/e2e observability
python -m pip install -r simulator/requirements-test.txt -r observability/requirements-test.txt
(cd simulator && python -m coverage run --rcfile=.coveragerc manage.py test trade_gui && python -m coverage report --rcfile=.coveragerc --fail-under=80)
python -m coverage erase
python -m coverage run --rcfile=observability/.coveragerc -m unittest discover -s observability/tests -v
python -m coverage report --rcfile=observability/.coveragerc --fail-under=35
"""
        await self.run_container("Python checks", self.client.container().from_(PYTHON_IMAGE).with_directory("/workspace", self.source).with_workdir("/workspace").with_exec(["bash", "-lc", script]))

    async def kubernetes_checks(self) -> None:
        script = r"""
set -euo pipefail
kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/local >/tmp/eve-trade-local.yaml
kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod >/tmp/eve-trade-prod.yaml
kubectl kustomize distributed-backend/orchestration/kubernetes/chaos/litmus/overlays/prod >/tmp/eve-trade-chaos.yaml
python scripts/verify_rendered_kubernetes.py /tmp/eve-trade-prod.yaml
"""
        await self.run_container("Kubernetes render checks", self.client.container().from_(KUSTOMIZE_IMAGE).with_directory("/workspace", self.source).with_workdir("/workspace").with_exec(["sh", "-c", script]))

    async def terraform_checks(self, providers: tuple[str, ...]) -> None:
        for provider in providers:
            root = TERRAFORM_ROOTS[provider]
            script = f"terraform fmt -check -recursive distributed-backend/terraform && terraform -chdir={root} init -backend=false && terraform -chdir={root} validate && terraform -chdir={root} test"
            await self.run_container(f"Terraform {provider}", self.client.container().from_(TERRAFORM_IMAGE).with_directory("/workspace", self.source).with_workdir("/workspace").with_exec(["sh", "-c", script]))

    async def build_images(self, registry: str, tag: str) -> None:
        script = f"encore build docker --config infra/encore/self-host.nsq.json {registry}/encore-backend:{tag}"
        await self.run_container("Build Encore backend image", self.go_base().with_exec(["bash", "-lc", script]))
        Path("ci-cd/out").mkdir(parents=True, exist_ok=True)
        Path("ci-cd/out/image-digests.json").write_text(json.dumps({name: f"{registry}/{name}:{tag}" for name in SERVICE_IMAGE_NAMES}, indent=2), encoding="utf-8")

    async def render_kubernetes(self, registry: str, tag: str, output: str) -> None:
        script = f"""
set -euo pipefail
kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod > /tmp/rendered.yaml
cp /tmp/rendered.yaml /workspace/{output}
"""
        await self.run_container("Render Kubernetes", self.client.container().from_(KUSTOMIZE_IMAGE).with_directory("/workspace", self.source).with_workdir("/workspace").with_exec(["sh", "-c", script]))

    async def render_chaos(self, output: str) -> None:
        script = f"kubectl kustomize distributed-backend/orchestration/kubernetes/chaos/litmus/overlays/prod > /workspace/{output}"
        await self.run_container("Render Litmus chaos", self.client.container().from_(KUSTOMIZE_IMAGE).with_directory("/workspace", self.source).with_workdir("/workspace").with_exec(["sh", "-c", script]))

    async def check(self) -> None:
        await self.proto_checks()
        await self.kubernetes_checks()
        await self.terraform_checks(tuple(TERRAFORM_ROOTS))

    async def test(self) -> None:
        await self.go_checks()
        await self.rust_checks()
        await self.python_checks()

    async def integration(self) -> None:
        await self.go_checks()

    async def deploy(self, registry: str, tag: str) -> None:
        await self.render_kubernetes(registry, tag, "ci-cd/out/kubernetes.yaml")

    async def chaos(self, namespace: str, selector: str, timeout_seconds: int, cleanup: bool) -> None:
        await self.render_chaos("ci-cd/out/chaos-litmus.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Eve Trade Dagger CI/CD")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--registry", default=None)
    shared.add_argument("--tag", default=None)
    shared.add_argument("--cloud-provider", "--deployment-target", dest="cloud_provider", default=None)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "test", "security", "build", "publish", "integration", "deploy"):
        subcommands.add_parser(command, parents=[shared])
    terraform = subcommands.add_parser("terraform", parents=[shared])
    terraform.add_argument("--all-clouds", "--all-targets", dest="all_clouds", action="store_true")
    render = subcommands.add_parser("render-kubernetes", parents=[shared])
    render.add_argument("--output", default="ci-cd/out/kubernetes.yaml")
    render_chaos = subcommands.add_parser("render-chaos", parents=[shared])
    render_chaos.add_argument("--output", default="ci-cd/out/chaos-litmus.yaml")
    chaos = subcommands.add_parser("chaos", parents=[shared])
    chaos.add_argument("--namespace", default=env("CHAOS_NAMESPACE", "eve-trade"))
    chaos.add_argument("--selector", default=env("CHAOS_SELECTOR", "chaos.eve-trade.io/suite=pod-resilience"))
    chaos.add_argument("--timeout-seconds", type=int, default=int(env("CHAOS_TIMEOUT_SECONDS", "900")))
    chaos.add_argument("--cleanup", action="store_true")
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    async with dagger.Connection(dagger.Config(log_output=sys.stderr)) as client:
        pipeline = EveTradePipeline(client)
        provider = cloud_provider(args.cloud_provider)
        registry = image_registry(args.registry)
        tag = image_tag(args.tag)
        if args.command == "check":
            await pipeline.check()
        elif args.command == "test":
            await pipeline.test()
        elif args.command == "security":
            await pipeline.kubernetes_checks()
        elif args.command == "build":
            await pipeline.build_images(registry, tag)
        elif args.command == "publish":
            await pipeline.build_images(registry, tag)
        elif args.command == "integration":
            await pipeline.integration()
        elif args.command == "terraform":
            await pipeline.terraform_checks(tuple(TERRAFORM_ROOTS) if args.all_clouds else (provider,))
        elif args.command == "render-kubernetes":
            await pipeline.render_kubernetes(registry, tag, args.output)
        elif args.command == "render-chaos":
            await pipeline.render_chaos(args.output)
        elif args.command == "deploy":
            await pipeline.deploy(registry, tag)
        elif args.command == "chaos":
            await pipeline.chaos(args.namespace, args.selector, args.timeout_seconds, args.cleanup)


def main() -> None:
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
