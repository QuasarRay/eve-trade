from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import dagger


GO_IMAGE = "golang:1.26-bookworm"
RUST_IMAGE = "rust:1-bookworm"
PYTHON_IMAGE = "python:3.12-slim-bookworm"
DEBIAN_IMAGE = "debian:bookworm-slim"
POSTGRES_IMAGE = "postgres:16"
KUSTOMIZE_IMAGE = "alpine/k8s:1.33.1"
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.27.2"
TRIVY_IMAGE = "aquasec/trivy:0.64.1"

BUF_VERSION = "1.53.0"
PROTOC_GEN_GO_VERSION = "1.36.11"
PROTOC_GEN_CONNECT_GO_VERSION = "1.20.0"

SERVICE_IMAGE_NAMES = ("api-gateway", "market", "trade-settlement")

SOURCE_EXCLUDES = [
    ".git",
    ".cache",
    ".ci-venv",
    ".venv",
    "ci-cd/out",
    "target",
    "distributed-backend/src/trade-settlement/target",
    "**/.pytest_cache",
    "**/__pycache__",
    "**/*.pyc",
]


@dataclass(frozen=True)
class GoService:
    name: str
    binary: str
    package: str
    port: int


GO_SERVICES = {
    "api-gateway": GoService(
        name="api-gateway",
        binary="api-gateway",
        package="./distributed-backend/src/api-gateway/cmd/api-gateway",
        port=8080,
    ),
    "market": GoService(
        name="market",
        binary="market",
        package="./distributed-backend/src/market/cmd/market",
        port=8081,
    ),
}


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value else default


def image_registry(explicit: str | None = None) -> str:
    return (
        explicit
        or env("IMAGE_REGISTRY")
        or env("CI_REGISTRY_IMAGE")
        or "registry.local/eve-trade"
    ).rstrip("/")


def image_tag(explicit: str | None = None) -> str:
    return (
        explicit
        or env("IMAGE_TAG")
        or env("CI_COMMIT_TAG")
        or env("CI_COMMIT_SHORT_SHA")
        or env("CI_COMMIT_SHA", "local")
    )


def revision() -> str:
    return env("CI_COMMIT_SHA", "local")


def source_url() -> str:
    return env("CI_PROJECT_URL", "https://example.invalid/eve-trade")


class EveTradePipeline:
    def __init__(self, client: dagger.Client):
        self.client = client
        self.source = client.host().directory(
            ".",
            exclude=SOURCE_EXCLUDES,
            gitignore=True,
        )

    async def run_container(self, title: str, container: dagger.Container) -> str:
        print(f"\n==> {title}", flush=True)
        output = await container.stdout()
        if output.strip():
            print(output, flush=True)
        return output

    def go_base(self) -> dagger.Container:
        return (
            self.client.container()
            .from_(GO_IMAGE)
            .with_mounted_cache("/go/pkg/mod", self.client.cache_volume("go-mod"))
            .with_mounted_cache(
                "/root/.cache/go-build",
                self.client.cache_volume("go-build"),
            )
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
        )

    def rust_base(self) -> dagger.Container:
        return (
            self.client.container()
            .from_(RUST_IMAGE)
            .with_exec(
                [
                    "bash",
                    "-lc",
                    "apt-get update && apt-get install -y --no-install-recommends "
                    "protobuf-compiler pkg-config ca-certificates && "
                    "rm -rf /var/lib/apt/lists/*",
                ]
            )
            .with_exec(["rustup", "component", "add", "rustfmt", "clippy"])
            .with_mounted_cache(
                "/usr/local/cargo/registry",
                self.client.cache_volume("cargo-registry"),
            )
            .with_mounted_cache(
                "/usr/local/cargo/git",
                self.client.cache_volume("cargo-git"),
            )
            .with_directory("/workspace", self.source)
            .with_mounted_cache(
                "/workspace/distributed-backend/src/trade-settlement/target",
                self.client.cache_volume("trade-settlement-target"),
            )
            .with_workdir("/workspace/distributed-backend/src/trade-settlement")
        )

    def python_e2e_base(self) -> dagger.Container:
        return (
            self.client.container()
            .from_(PYTHON_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_mounted_cache("/root/.cache/pip", self.client.cache_volume("pip"))
            .with_exec(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    "distributed-backend/tests/e2e/requirements.txt",
                ]
            )
        )

    def proto_toolchain(self) -> dagger.Container:
        install = f"""
set -euo pipefail
GOBIN=/usr/local/bin go install github.com/bufbuild/buf/cmd/buf@v{BUF_VERSION}
GOBIN=/usr/local/bin go install google.golang.org/protobuf/cmd/protoc-gen-go@v{PROTOC_GEN_GO_VERSION}
GOBIN=/usr/local/bin go install connectrpc.com/connect/cmd/protoc-gen-connect-go@v{PROTOC_GEN_CONNECT_GO_VERSION}
"""
        return self.go_base().with_exec(["bash", "-lc", install])

    async def proto_lint(self) -> None:
        await self.run_container(
            "buf lint",
            self.proto_toolchain().with_exec(["buf", "lint"]),
        )

    async def proto_generate_check(self) -> None:
        script = r"""
set -euo pipefail
snapshot() {
  if [ -d distributed-backend/proto/gen ]; then
    find distributed-backend/proto/gen -type f -print | sort | xargs --no-run-if-empty sha256sum
  fi
}
before="$(snapshot)"
buf generate
after="$(snapshot)"
if [ "$before" != "$after" ]; then
  echo "Generated protobuf files are stale. Run: buf generate"
  exit 1
fi
"""
        await self.run_container(
            "protobuf generation drift check",
            self.proto_toolchain().with_exec(["bash", "-lc", script]),
        )

    async def go_checks(self) -> None:
        script = r"""
set -euo pipefail
unformatted="$(gofmt -l distributed-backend/src/api-gateway distributed-backend/src/market distributed-backend/proto/gen)"
if [ -n "$unformatted" ]; then
  echo "Go files need gofmt:"
  echo "$unformatted"
  exit 1
fi
go test ./...
"""
        await self.run_container(
            "Go format and tests",
            self.go_base().with_exec(["bash", "-lc", script]),
        )

    async def rust_checks(self) -> None:
        await self.run_container(
            "Rust fmt, clippy, and tests",
            self.rust_base().with_exec(
                [
                    "bash",
                    "-lc",
                    "cargo fmt -- --check && "
                    "cargo clippy --locked --all-targets -- -D warnings && "
                    "cargo test --locked",
                ]
            ),
        )

    async def python_contract_tests(self) -> None:
        await self.run_container(
            "Python e2e contract tests",
            self.python_e2e_base().with_exec(
                ["pytest", "distributed-backend/tests/e2e/contracts", "-q"]
            ),
        )

    async def security_scan(self) -> None:
        gitleaks = (
            self.client.container()
            .from_(GITLEAKS_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_exec(
                ["detect", "--source", "/workspace", "--no-git", "--redact"],
                use_entrypoint=True,
            )
        )
        trivy = (
            self.client.container()
            .from_(TRIVY_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_exec(
                [
                    "fs",
                    "--exit-code",
                    "1",
                    "--severity",
                    "HIGH,CRITICAL",
                    "--ignore-unfixed",
                    "--skip-dirs",
                    "target",
                    "--skip-dirs",
                    "distributed-backend/src/trade-settlement/target",
                    "/workspace",
                ],
                use_entrypoint=True,
            )
        )
        await self.run_container("secret scan", gitleaks)
        await self.run_container("dependency and filesystem vulnerability scan", trivy)

    def go_runtime_image(self, spec: GoService) -> dagger.Container:
        build = self.go_base().with_exec(
            [
                "go",
                "build",
                "-trimpath",
                "-ldflags=-s -w",
                "-o",
                f"/out/{spec.binary}",
                spec.package,
            ]
        )
        return (
            self.client.container()
            .from_(DEBIAN_IMAGE)
            .with_exec(
                [
                    "bash",
                    "-lc",
                    "apt-get update && apt-get install -y --no-install-recommends "
                    "ca-certificates passwd && rm -rf /var/lib/apt/lists/* && "
                    "useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser",
                ]
            )
            .with_workdir("/app")
            .with_file(
                f"/app/{spec.binary}",
                build.file(f"/out/{spec.binary}"),
                permissions=0o755,
                owner="appuser:appuser",
            )
            .with_user("appuser")
            .with_exposed_port(spec.port)
            .with_entrypoint([f"/app/{spec.binary}"])
            .with_label("org.opencontainers.image.title", spec.name)
            .with_label("org.opencontainers.image.source", source_url())
            .with_label("org.opencontainers.image.revision", revision())
        )

    def trade_settlement_image(self) -> dagger.Container:
        build = self.rust_base().with_exec(["cargo", "build", "--locked", "--release"])
        return (
            self.client.container()
            .from_(DEBIAN_IMAGE)
            .with_exec(
                [
                    "bash",
                    "-lc",
                    "apt-get update && apt-get install -y --no-install-recommends "
                    "ca-certificates passwd && rm -rf /var/lib/apt/lists/* && "
                    "useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser",
                ]
            )
            .with_workdir("/app")
            .with_file(
                "/app/trade-settlement",
                build.file(
                    "/workspace/distributed-backend/src/trade-settlement/target/release/trade-settlement"
                ),
                permissions=0o755,
                owner="appuser:appuser",
            )
            .with_directory(
                "/app/config",
                self.source.directory(
                    "distributed-backend/src/trade-settlement/config"
                ),
                owner="appuser:appuser",
            )
            .with_user("appuser")
            .with_exposed_port(9092)
            .with_entrypoint(["/app/trade-settlement"])
            .with_label("org.opencontainers.image.title", "trade-settlement")
            .with_label("org.opencontainers.image.source", source_url())
            .with_label("org.opencontainers.image.revision", revision())
        )

    def service_image(self, name: str) -> dagger.Container:
        if name in GO_SERVICES:
            return self.go_runtime_image(GO_SERVICES[name])
        if name == "trade-settlement":
            return self.trade_settlement_image()
        raise ValueError(f"unknown service image: {name}")

    async def build_images(self) -> None:
        for name in SERVICE_IMAGE_NAMES:
            await self.run_container(
                f"build image {name}",
                self.service_image(name).with_exec(["true"]),
            )

    async def publish_images(self, registry: str, tag: str) -> None:
        registry_host = env("CI_REGISTRY") or registry.split("/")[0]
        username = env("CI_REGISTRY_USER")
        password = env("CI_REGISTRY_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "CI_REGISTRY_USER and CI_REGISTRY_PASSWORD are required to publish"
            )
        password_secret = self.client.set_secret("ci-registry-password", password)

        for name in SERVICE_IMAGE_NAMES:
            reference = f"{registry}/{name}:{tag}"
            container = self.service_image(name).with_registry_auth(
                registry_host,
                username,
                password_secret,
            )
            digest = await container.publish(reference)
            print(f"published {name}: {digest}", flush=True)

    def kubernetes_render_file(self, registry: str, tag: str) -> dagger.File:
        image_commands = " && ".join(
            f"kustomize edit set image eve-trade/{name}={registry}/{name}:{tag}"
            for name in SERVICE_IMAGE_NAMES
        )
        script = f"""
set -eu
rm -rf /tmp/kubernetes
mkdir -p /out
cp -R distributed-backend/orchestration/kubernetes /tmp/kubernetes
cd /tmp/kubernetes
{image_commands}
kustomize build . > /out/kubernetes.yaml
"""
        return (
            self.client.container()
            .from_(KUSTOMIZE_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_exec(["sh", "-c", script])
            .file("/out/kubernetes.yaml")
        )

    async def render_kubernetes(self, registry: str, tag: str, output: str) -> None:
        rendered = self.kubernetes_render_file(registry, tag)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        await rendered.export(output, allow_parent_dir_path=True)
        print(f"rendered Kubernetes manifests to {output}", flush=True)

    async def validate_kubernetes_render(self) -> None:
        rendered = self.kubernetes_render_file("registry.local/eve-trade", "ci")
        await rendered.contents()

    async def deploy(self, registry: str, tag: str) -> None:
        kubeconfig = env("KUBE_CONFIG_B64")
        if not kubeconfig:
            raise RuntimeError("KUBE_CONFIG_B64 is required for deploy")
        kubeconfig_secret = self.client.set_secret("kubeconfig-b64", kubeconfig)
        image_commands = " && ".join(
            f"kustomize edit set image eve-trade/{name}={registry}/{name}:{tag}"
            for name in SERVICE_IMAGE_NAMES
        )
        script = f"""
set -eu
rm -rf /tmp/kubernetes
cp -R distributed-backend/orchestration/kubernetes /tmp/kubernetes
printf '%s' "$KUBE_CONFIG_B64" | base64 -d > /tmp/kubeconfig
chmod 600 /tmp/kubeconfig
cd /tmp/kubernetes
{image_commands}
kubectl --kubeconfig /tmp/kubeconfig apply -k .
"""
        await self.run_container(
            "deploy Kubernetes manifests",
            self.client.container()
            .from_(KUSTOMIZE_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_secret_variable("KUBE_CONFIG_B64", kubeconfig_secret)
            .with_exec(["sh", "-c", script]),
        )

    async def integration(self) -> None:
        postgres = (
            self.client.container()
            .from_(POSTGRES_IMAGE)
            .with_env_variable("POSTGRES_USER", "postgres")
            .with_env_variable("POSTGRES_PASSWORD", "postgres")
            .with_env_variable("POSTGRES_DB", "eve_trade")
            .with_exposed_port(5432)
            .as_service()
        )

        migrate_script = r"""
set -euo pipefail
until pg_isready -h postgres -U postgres -d eve_trade; do sleep 1; done
psql -h postgres -U postgres -d eve_trade -v ON_ERROR_STOP=1 \
  -f distributed-backend/migrations/postgresql/001_create_trade_schema.up.sql
"""
        migrator = (
            self.client.container()
            .from_(POSTGRES_IMAGE)
            .with_service_binding("postgres", postgres)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_env_variable("PGPASSWORD", "postgres")
            .with_exec(["bash", "-lc", migrate_script])
        )
        await self.run_container("apply PostgreSQL migrations", migrator)

        settlement = (
            self.trade_settlement_image()
            .with_service_binding("postgres", postgres)
            .with_env_variable(
                "DATABASE_URL",
                "postgres://postgres:postgres@postgres:5432/eve_trade",
            )
            .as_service()
        )
        market = (
            self.go_runtime_image(GO_SERVICES["market"])
            .with_service_binding("trade-settlement", settlement)
            .with_env_variable("MARKET_ADDR", ":8081")
            .with_env_variable("SETTLEMENT_URL", "http://trade-settlement:9092")
            .as_service()
        )
        gateway = (
            self.go_runtime_image(GO_SERVICES["api-gateway"])
            .with_service_binding("market", market)
            .with_env_variable("API_GATEWAY_ADDR", ":8080")
            .with_env_variable("MARKET_URL", "http://market:8081")
            .as_service()
        )

        wait_and_test = r"""
set -eu
python - <<'PY'
import socket
import time

targets = [
    ("postgres", 5432),
    ("trade-settlement", 9092),
    ("market", 8081),
    ("api-gateway", 8080),
]
deadline = time.time() + 90
for host, port in targets:
    while True:
        try:
            with socket.create_connection((host, port), timeout=2):
                break
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(1)
PY
pytest distributed-backend/tests/e2e -q
"""
        tests = (
            self.python_e2e_base()
            .with_service_binding("postgres", postgres)
            .with_service_binding("trade-settlement", settlement)
            .with_service_binding("market", market)
            .with_service_binding("api-gateway", gateway)
            .with_env_variable(
                "EVE_TRADE_DATABASE_URL",
                "postgres://postgres:postgres@postgres:5432/eve_trade",
            )
            .with_env_variable("EVE_TRADE_SETTLEMENT_GRPC", "trade-settlement:9092")
            .with_env_variable("EVE_TRADE_MARKET_GRPC", "market:8081")
            .with_env_variable("EVE_TRADE_GATEWAY_GRPC", "api-gateway:8080")
            .with_exec(["sh", "-c", wait_and_test])
        )
        await self.run_container("live Python e2e tests", tests)

    async def check(self) -> None:
        await self.proto_lint()
        await self.proto_generate_check()
        await self.validate_kubernetes_render()
        await self.security_scan()

    async def test(self) -> None:
        await self.go_checks()
        await self.rust_checks()
        await self.python_contract_tests()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Eve Trade Dagger CI/CD")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--registry",
        default=None,
        help="Container registry/repository prefix. Defaults to IMAGE_REGISTRY or CI_REGISTRY_IMAGE.",
    )
    shared.add_argument(
        "--tag",
        default=None,
        help="Image tag. Defaults to IMAGE_TAG, CI_COMMIT_TAG, CI_COMMIT_SHORT_SHA, or local.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check", parents=[shared])
    subcommands.add_parser("test", parents=[shared])
    subcommands.add_parser("security", parents=[shared])
    subcommands.add_parser("build", parents=[shared])
    subcommands.add_parser("publish", parents=[shared])
    subcommands.add_parser("integration", parents=[shared])

    render = subcommands.add_parser("render-kubernetes", parents=[shared])
    render.add_argument(
        "--output",
        default="ci-cd/out/kubernetes.yaml",
        help="Path to write rendered Kubernetes YAML.",
    )

    subcommands.add_parser("deploy", parents=[shared])
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    config = dagger.Config(log_output=sys.stderr)
    async with dagger.Connection(config) as client:
        pipeline = EveTradePipeline(client)
        registry = image_registry(args.registry)
        tag = image_tag(args.tag)

        if args.command == "check":
            await pipeline.check()
        elif args.command == "test":
            await pipeline.test()
        elif args.command == "security":
            await pipeline.security_scan()
        elif args.command == "build":
            await pipeline.build_images()
        elif args.command == "publish":
            await pipeline.publish_images(registry, tag)
        elif args.command == "integration":
            await pipeline.integration()
        elif args.command == "render-kubernetes":
            await pipeline.render_kubernetes(registry, tag, args.output)
        elif args.command == "deploy":
            await pipeline.deploy(registry, tag)
        else:
            raise ValueError(f"unknown command: {args.command}")


def main() -> None:
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
