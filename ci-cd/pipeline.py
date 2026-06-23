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
PYTHON_IMAGE = "python:3.13-slim-bookworm"
DEBIAN_IMAGE = "debian:bookworm-slim"
POSTGRES_IMAGE = "postgres:16"
RABBITMQ_IMAGE = "rabbitmq:3.13.7-management"
KUSTOMIZE_IMAGE = "alpine/k8s:1.33.1"
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.27.2"
TRIVY_IMAGE = "aquasec/trivy:0.64.1"
TERRAFORM_IMAGE = "hashicorp/terraform:1.10.5"

BUF_VERSION = "1.70.0"
PROTOC_GEN_GO_VERSION = "1.36.11"
PROTOC_GEN_CONNECT_GO_VERSION = "1.20.0"

KUBERNETES_NAMESPACE = "eve-trade"
SERVICE_IMAGE_NAMES = (
    "api-gateway",
    "market",
    "settlement-worker",
    "trade-settlement",
)
DEPLOYMENT_NAMES = (
    "trade-settlement",
    "settlement-worker",
    "market",
    "api-gateway",
)
STATEFULSET_NAMES = ("rabbitmq",)
TERRAFORM_ROOTS = {
    "aws": "distributed-backend/terraform/eks",
    "gcp": "distributed-backend/terraform/gke",
    "talos-omni": "distributed-backend/terraform/talos-omni",
}
DEPLOYMENT_TARGET_CHOICES = sorted(
    {
        *TERRAFORM_ROOTS,
        "eks",
        "gke",
        "omni",
        "talos",
        "talos_omni",
    }
)

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
    module_dir: str
    package: str
    build_tags: str = ""
    port: int | None = None


GO_SERVICES = {
    "api-gateway": GoService(
        name="api-gateway",
        binary="api-gateway",
        module_dir="distributed-backend/src/api-gateway",
        package="./cmd/api-gateway",
        port=8080,
    ),
    "market": GoService(
        name="market",
        binary="market",
        module_dir="distributed-backend/src/market",
        package="./cmd/market",
        port=8081,
    ),
    "settlement-worker": GoService(
        name="settlement-worker",
        binary="settlement-worker",
        module_dir="distributed-backend/src/settlement-worker",
        package="./cmd/settlement-worker",
        port=8082,
    ),
}


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value else default


def cloud_provider(explicit: str | None = None) -> str:
    value = (
        explicit
        or env("EVE_TRADE_CLOUD_PROVIDER")
        or env("CLOUD_PROVIDER")
        or "aws"
    ).lower()
    aliases = {
        "eks": "aws",
        "gke": "gcp",
        "omni": "talos-omni",
        "talos": "talos-omni",
        "talos_omni": "talos-omni",
    }
    value = aliases.get(value, value)
    if value not in TERRAFORM_ROOTS:
        raise ValueError(
            "deployment target must be one of: "
            + ", ".join(sorted(TERRAFORM_ROOTS))
        )
    return value


def provider_image_registry(provider: str) -> str:
    if provider == "aws":
        return env("AWS_ECR_IMAGE_REGISTRY") or env("ECR_IMAGE_REGISTRY")
    if provider == "gcp":
        return env("GCP_ARTIFACT_REGISTRY_IMAGE") or env("GAR_IMAGE_REGISTRY")
    if provider == "talos-omni":
        return env("TALOS_OMNI_IMAGE_REGISTRY") or env("OMNI_IMAGE_REGISTRY")
    return ""


def image_registry(explicit: str | None = None, provider: str = "aws") -> str:
    return (
        explicit
        or env("IMAGE_REGISTRY")
        or provider_image_registry(provider)
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


def deployment_names_shell() -> str:
    return " ".join(DEPLOYMENT_NAMES)


def statefulset_names_shell() -> str:
    return " ".join(STATEFULSET_NAMES)


def registry_auth(provider: str, registry: str) -> tuple[str, str, str]:
    registry_host = registry.split("/")[0]
    if provider == "aws":
        username = (
            env("AWS_ECR_REGISTRY_USER")
            or env("ECR_REGISTRY_USER")
            or env("REGISTRY_USER")
            or env("CI_REGISTRY_USER")
        )
        password = (
            env("AWS_ECR_REGISTRY_PASSWORD")
            or env("ECR_REGISTRY_PASSWORD")
            or env("REGISTRY_PASSWORD")
            or env("CI_REGISTRY_PASSWORD")
        )
    elif provider == "gcp":
        username = (
            env("GCP_ARTIFACT_REGISTRY_USER")
            or env("GAR_REGISTRY_USER")
            or env("REGISTRY_USER")
            or env("CI_REGISTRY_USER")
        )
        password = (
            env("GCP_ARTIFACT_REGISTRY_PASSWORD")
            or env("GAR_REGISTRY_PASSWORD")
            or env("REGISTRY_PASSWORD")
            or env("CI_REGISTRY_PASSWORD")
        )
    elif provider == "talos-omni":
        username = (
            env("TALOS_OMNI_REGISTRY_USER")
            or env("OMNI_REGISTRY_USER")
            or env("REGISTRY_USER")
            or env("CI_REGISTRY_USER")
        )
        password = (
            env("TALOS_OMNI_REGISTRY_PASSWORD")
            or env("OMNI_REGISTRY_PASSWORD")
            or env("REGISTRY_PASSWORD")
            or env("CI_REGISTRY_PASSWORD")
        )
    else:
        raise ValueError(f"unknown deployment target: {provider}")

    if not username or not password:
        raise RuntimeError(
            "registry credentials are required to publish. Set provider-specific "
            "registry credentials, REGISTRY_USER/REGISTRY_PASSWORD, or GitLab "
            "CI_REGISTRY_USER/CI_REGISTRY_PASSWORD."
        )
    return registry_host, username, password


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
unformatted="$(gofmt -l distributed-backend/src/api-gateway distributed-backend/src/market distributed-backend/src/messaging distributed-backend/src/settlement-worker distributed-backend/proto/gen)"
if [ -n "$unformatted" ]; then
  echo "Go files need gofmt:"
  echo "$unformatted"
  exit 1
fi
for module in distributed-backend/proto distributed-backend/src/observability distributed-backend/src/messaging distributed-backend/src/market distributed-backend/src/settlement-worker distributed-backend/src/api-gateway; do
  (cd "$module" && go test ./...)
done
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

    async def python_static_tests(self) -> None:
        await self.run_container(
            "Python e2e collection tests",
            self.python_e2e_base().with_exec(
                [
                    "python",
                    "-m",
                    "pytest",
                    "distributed-backend/tests/e2e",
                    "--collect-only",
                    "-q",
                ]
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
        build_command = ["go", "build"]
        if spec.build_tags:
            build_command.extend(["-tags", spec.build_tags])
        build_command.extend(
            [
                "-trimpath",
                "-ldflags=-s -w",
                "-o",
                f"/out/{spec.binary}",
                spec.package,
            ]
        )
        build = (
            self.go_base()
            .with_workdir(f"/workspace/{spec.module_dir}")
            .with_exec(build_command)
        )
        container = (
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
            .with_entrypoint([f"/app/{spec.binary}"])
            .with_label("org.opencontainers.image.title", spec.name)
            .with_label("org.opencontainers.image.source", source_url())
            .with_label("org.opencontainers.image.revision", revision())
        )
        if spec.port is not None:
            container = container.with_exposed_port(spec.port)
        return container

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

    async def publish_images(self, registry: str, tag: str, provider: str) -> None:
        registry_host, username, password = registry_auth(provider, registry)
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

    def terraform_base(self) -> dagger.Container:
        return (
            self.client.container()
            .from_(TERRAFORM_IMAGE)
            .with_entrypoint([])
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
        )

    async def terraform_checks(self, providers: tuple[str, ...]) -> None:
        await self.run_container(
            "Terraform format",
            self.terraform_base().with_exec(
                [
                    "sh",
                    "-c",
                    "terraform fmt -check -recursive distributed-backend/terraform",
                ]
            ),
        )
        for provider in providers:
            root = TERRAFORM_ROOTS[provider]
            script = (
                f"terraform -chdir={root} init -backend=false && "
                f"terraform -chdir={root} validate"
            )
            await self.run_container(
                f"Terraform validate {provider}",
                self.terraform_base().with_exec(["sh", "-c", script]),
            )

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
cd /tmp/kubernetes/overlay/prod
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

    def chaos_render_file(self) -> dagger.File:
        script = r"""
set -eu
rm -rf /tmp/kubernetes
mkdir -p /out
cp -R distributed-backend/orchestration/kubernetes /tmp/kubernetes
cd /tmp/kubernetes/chaos/litmus/overlays/prod
kustomize build . > /out/chaos-litmus.yaml
"""
        return (
            self.client.container()
            .from_(KUSTOMIZE_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_exec(["sh", "-c", script])
            .file("/out/chaos-litmus.yaml")
        )

    async def render_chaos(self, output: str) -> None:
        rendered = self.chaos_render_file()
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        await rendered.export(output, allow_parent_dir_path=True)
        print(f"rendered Litmus chaos manifests to {output}", flush=True)

    async def validate_chaos_render(self) -> None:
        rendered = self.chaos_render_file()
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
        deployments = deployment_names_shell()
        statefulsets = statefulset_names_shell()
        script = f"""
set -eu
rm -rf /tmp/kubernetes
cp -R distributed-backend/orchestration/kubernetes /tmp/kubernetes
printf '%s' "$KUBE_CONFIG_B64" | base64 -d > /tmp/kubeconfig
chmod 600 /tmp/kubeconfig
KUBECTL="kubectl --kubeconfig /tmp/kubeconfig"

$KUBECTL create namespace {KUBERNETES_NAMESPACE} --dry-run=client -o yaml | $KUBECTL apply -f -

cd /tmp/kubernetes/overlay/prod
{image_commands}
$KUBECTL apply -k .

for statefulset in {statefulsets}; do
  $KUBECTL -n {KUBERNETES_NAMESPACE} rollout status "statefulset/$statefulset" --timeout=240s
done
for deploy in {deployments}; do
  $KUBECTL -n {KUBERNETES_NAMESPACE} rollout status "deployment/$deploy" --timeout=240s
done
"""
        container = (
            self.client.container()
            .from_(KUSTOMIZE_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_secret_variable("KUBE_CONFIG_B64", kubeconfig_secret)
        )
        await self.run_container(
            "deploy Kubernetes manifests",
            container.with_exec(["sh", "-c", script]),
        )

    async def chaos(
        self,
        namespace: str,
        selector: str,
        timeout_seconds: int,
        cleanup: bool,
    ) -> None:
        kubeconfig = env("KUBE_CONFIG_B64")
        if not kubeconfig:
            raise RuntimeError("KUBE_CONFIG_B64 is required for chaos")
        if timeout_seconds < 300:
            raise ValueError("chaos timeout must be at least 300 seconds")

        kubeconfig_secret = self.client.set_secret("kubeconfig-b64", kubeconfig)
        cleanup_value = "true" if cleanup else "false"
        script = r"""
set -eu

KUBECONFIG_PATH=/tmp/kubeconfig
printf '%s' "$KUBE_CONFIG_B64" | base64 -d > "$KUBECONFIG_PATH"
chmod 600 "$KUBECONFIG_PATH"
KUBECTL="kubectl --kubeconfig $KUBECONFIG_PATH"

rm -rf /tmp/kubernetes
cp -R distributed-backend/orchestration/kubernetes /tmp/kubernetes

echo "Checking Kubernetes namespace and Litmus CRDs"
$KUBECTL get namespace "$CHAOS_NAMESPACE" >/dev/null
for crd in chaosengines.litmuschaos.io chaosresults.litmuschaos.io chaosexperiments.litmuschaos.io; do
  $KUBECTL get crd "$crd" >/dev/null
done

echo "Checking steady state before chaos"
for deploy in $DEPLOYMENT_NAMES; do
  $KUBECTL -n "$CHAOS_NAMESPACE" rollout status "deployment/$deploy" --timeout=180s
done

cd /tmp/kubernetes/chaos/litmus/overlays/prod

echo "Resetting previously managed ChaosEngines selected by: $CHAOS_SELECTOR"
$KUBECTL -n "$CHAOS_NAMESPACE" delete chaosengine -l "$CHAOS_SELECTOR" --ignore-not-found --wait=true
$KUBECTL apply -k .

engines="$($KUBECTL -n "$CHAOS_NAMESPACE" get chaosengine -l "$CHAOS_SELECTOR" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"
if [ -z "$engines" ]; then
  echo "No ChaosEngines matched selector: $CHAOS_SELECTOR" >&2
  exit 1
fi

: > /tmp/chaos-results
for engine in $engines; do
  experiments="$($KUBECTL -n "$CHAOS_NAMESPACE" get chaosengine "$engine" -o jsonpath='{range .spec.experiments[*]}{.name}{"\n"}{end}')"
  for experiment in $experiments; do
    echo "$engine $experiment ${engine}-${experiment}" >> /tmp/chaos-results
  done
done

if [ ! -s /tmp/chaos-results ]; then
  echo "Selected ChaosEngines do not contain experiments" >&2
  exit 1
fi

echo "Checking installed Litmus ChaosExperiment resources"
awk '{print $2}' /tmp/chaos-results | sort -u | while read -r experiment; do
  $KUBECTL -n "$CHAOS_NAMESPACE" get chaosexperiment "$experiment" >/dev/null
done

echo "Removing stale ChaosResults for the selected run"
while read -r engine experiment result; do
  $KUBECTL -n "$CHAOS_NAMESPACE" delete chaosresult "$result" --ignore-not-found
done < /tmp/chaos-results

stop_engines() {
  for engine in $engines; do
    $KUBECTL -n "$CHAOS_NAMESPACE" patch chaosengine "$engine" --type merge -p '{"spec":{"engineState":"stop"}}' >/dev/null 2>&1 || true
  done
}

print_results() {
  while read -r engine experiment result; do
    echo "--- ChaosResult: $result"
    $KUBECTL -n "$CHAOS_NAMESPACE" get chaosresult "$result" -o yaml || true
  done < /tmp/chaos-results
}

trap 'echo "Chaos run interrupted; stopping engines"; stop_engines' INT TERM

echo "Activating selected ChaosEngines"
for engine in $engines; do
  $KUBECTL -n "$CHAOS_NAMESPACE" patch chaosengine "$engine" --type merge -p '{"spec":{"engineState":"active"}}' >/dev/null
done

deadline=$(( $(date +%s) + CHAOS_TIMEOUT_SECONDS ))
while :; do
  total=0
  passed=0
  failed=0

  while read -r engine experiment result; do
    total=$(( total + 1 ))
    phase="$($KUBECTL -n "$CHAOS_NAMESPACE" get chaosresult "$result" -o jsonpath='{.status.experimentStatus.phase}' 2>/dev/null || true)"
    verdict="$($KUBECTL -n "$CHAOS_NAMESPACE" get chaosresult "$result" -o jsonpath='{.status.experimentStatus.verdict}' 2>/dev/null || true)"
    probes="$($KUBECTL -n "$CHAOS_NAMESPACE" get chaosresult "$result" -o jsonpath='{.status.experimentStatus.probeSuccessPercentage}' 2>/dev/null || true)"
    echo "$result phase=${phase:-Pending} verdict=${verdict:-Awaited} probes=${probes:-n/a}"

    case "$verdict" in
      Pass)
        passed=$(( passed + 1 ))
        ;;
      Fail|Stopped)
        failed=$(( failed + 1 ))
        ;;
    esac
  done < /tmp/chaos-results

  if [ "$failed" -gt 0 ]; then
    echo "At least one Litmus experiment failed; stopping chaos engines" >&2
    stop_engines
    print_results
    exit 1
  fi

  if [ "$total" -gt 0 ] && [ "$passed" -eq "$total" ]; then
    break
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "Timed out waiting for Litmus ChaosResults after ${CHAOS_TIMEOUT_SECONDS}s" >&2
    stop_engines
    print_results
    exit 1
  fi

  sleep 15
done

echo "Litmus chaos verdicts passed; stopping engines and checking recovery"
stop_engines
for deploy in $DEPLOYMENT_NAMES; do
  $KUBECTL -n "$CHAOS_NAMESPACE" rollout status "deployment/$deploy" --timeout=240s
done
print_results

if [ "$CHAOS_CLEANUP" = "true" ]; then
  echo "Cleaning up selected Litmus chaos resources"
  $KUBECTL -n "$CHAOS_NAMESPACE" delete chaosengine -l "$CHAOS_SELECTOR" --ignore-not-found --wait=true
  while read -r engine experiment result; do
    $KUBECTL -n "$CHAOS_NAMESPACE" delete chaosresult "$result" --ignore-not-found
  done < /tmp/chaos-results
fi
"""
        await self.run_container(
            "run Litmus chaos suite",
            self.client.container()
            .from_(KUSTOMIZE_IMAGE)
            .with_directory("/workspace", self.source)
            .with_workdir("/workspace")
            .with_secret_variable("KUBE_CONFIG_B64", kubeconfig_secret)
            .with_env_variable("CHAOS_NAMESPACE", namespace)
            .with_env_variable("CHAOS_SELECTOR", selector)
            .with_env_variable("CHAOS_TIMEOUT_SECONDS", str(timeout_seconds))
            .with_env_variable("CHAOS_CLEANUP", cleanup_value)
            .with_env_variable("DEPLOYMENT_NAMES", deployment_names_shell())
            .with_exec(["sh", "-c", script]),
        )

    async def integration(self) -> None:
        postgres = (
            self.client.container()
            .from_(POSTGRES_IMAGE)
            .with_env_variable("POSTGRES_USER", "postgres")
            .with_env_variable("POSTGRES_PASSWORD", "postgres")
            .with_env_variable("POSTGRES_DB", "eve_trade_e2e")
            .with_exposed_port(5432)
            .as_service()
        )

        migrate_script = r"""
set -euo pipefail
until pg_isready -h postgres -U postgres -d eve_trade_e2e; do sleep 1; done
psql -h postgres -U postgres -d eve_trade_e2e -v ON_ERROR_STOP=1 \
  -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'
psql -h postgres -U postgres -d eve_trade_e2e -v ON_ERROR_STOP=1 \
  -f distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql
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

        rabbitmq = (
            self.client.container()
            .from_(RABBITMQ_IMAGE)
            .with_env_variable("RABBITMQ_DEFAULT_USER", "eve_trade")
            .with_env_variable("RABBITMQ_DEFAULT_PASS", "eve_trade")
            .with_env_variable("RABBITMQ_DEFAULT_VHOST", "/")
            .with_exposed_port(5672)
            .as_service()
        )
        await self.run_container(
            "wait for RabbitMQ",
            self.client.container()
            .from_(PYTHON_IMAGE)
            .with_service_binding("rabbitmq", rabbitmq)
            .with_exec(
                [
                    "python",
                    "-c",
                    "import socket,time; deadline=time.time()+90\n"
                    "while True:\n"
                    "  try:\n"
                    "    socket.create_connection(('rabbitmq',5672),timeout=2).close(); break\n"
                    "  except OSError:\n"
                    "    assert time.time() < deadline\n"
                    "    time.sleep(1)\n",
                ]
            ),
        )

        settlement = (
            self.trade_settlement_image()
            .with_service_binding("postgres", postgres)
            .with_env_variable("SUMMER_ENV", "prod")
            .with_env_variable(
                "DATABASE_URL",
                "postgres://postgres:postgres@postgres:5432/eve_trade_e2e",
            )
            .as_service()
        )
        settlement_worker = (
            self.go_runtime_image(GO_SERVICES["settlement-worker"])
            .with_service_binding("rabbitmq", rabbitmq)
            .with_service_binding("trade-settlement", settlement)
            .with_env_variable("SETTLEMENT_WORKER_HEALTH_HTTP_ADDR", ":8082")
            .with_env_variable("TRADE_SETTLEMENT_URL", "http://trade-settlement:9092")
            .with_env_variable("RABBITMQ_URL", "amqp://eve_trade:eve_trade@rabbitmq:5672/")
            .with_env_variable("OTEL_SDK_DISABLED", "true")
            .as_service()
        )
        market = (
            self.go_runtime_image(GO_SERVICES["market"])
            .with_service_binding("rabbitmq", rabbitmq)
            .with_service_binding("settlement-worker", settlement_worker)
            .with_service_binding("trade-settlement", settlement)
            .with_env_variable("MARKET_HTTP_ADDR", ":8081")
            .with_env_variable("SETTLEMENT_TRANSPORT", "rabbitmq")
            .with_env_variable("TRADE_SETTLEMENT_URL", "http://trade-settlement:9092")
            .with_env_variable("RABBITMQ_URL", "amqp://eve_trade:eve_trade@rabbitmq:5672/")
            .with_env_variable(
                "DATABASE_URL",
                "postgres://postgres:postgres@postgres:5432/eve_trade_e2e",
            )
            .as_service()
        )
        gateway = (
            self.go_runtime_image(GO_SERVICES["api-gateway"])
            .with_service_binding("market", market)
            .with_env_variable("API_GATEWAY_HTTP_ADDR", ":8080")
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
    ("rabbitmq", 5672),
    ("trade-settlement", 9092),
    ("settlement-worker", 8082),
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
python - <<'PY'
import re
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "pytest", "distributed-backend/tests/e2e", "-q", "-rA"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
print(result.stdout, end="")
if re.search(r"(?m)^[0-9]+ skipped in ", result.stdout):
    print("all e2e tests were skipped", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(result.returncode)
PY
"""
        tests = (
            self.python_e2e_base()
            .with_service_binding("postgres", postgres)
            .with_service_binding("rabbitmq", rabbitmq)
            .with_service_binding("trade-settlement", settlement)
            .with_service_binding("settlement-worker", settlement_worker)
            .with_service_binding("market", market)
            .with_service_binding("api-gateway", gateway)
            .with_env_variable(
                "EVE_TRADE_DATABASE_URL",
                "postgres://postgres:postgres@postgres:5432/eve_trade_e2e",
            )
            .with_env_variable("EVE_TRADE_SETTLEMENT_GRPC", "trade-settlement:9092")
            .with_env_variable("EVE_TRADE_MARKET_GRPC", "market:8081")
            .with_env_variable("EVE_TRADE_GATEWAY_GRPC", "api-gateway:8080")
            .with_env_variable("EVE_TRADE_API_GATEWAY_URL", "http://api-gateway:8080")
            .with_env_variable("EVE_TRADE_E2E_PRODUCTION_GATE", "true")
            .with_env_variable(
                "EVE_TRADE_E2E_ARTIFACT_DIR",
                "/workspace/ci-cd/out/e2e-artifacts",
            )
            .with_exec(["sh", "-c", wait_and_test])
        )
        await self.run_container("live Python e2e tests", tests)

    async def check(self) -> None:
        await self.proto_lint()
        await self.proto_generate_check()
        await self.validate_kubernetes_render()
        await self.validate_chaos_render()
        await self.terraform_checks(tuple(TERRAFORM_ROOTS))
        await self.security_scan()

    async def test(self) -> None:
        await self.go_checks()
        await self.rust_checks()
        await self.python_static_tests()


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
    shared.add_argument(
        "--cloud-provider",
        "--deployment-target",
        dest="cloud_provider",
        default=None,
        choices=DEPLOYMENT_TARGET_CHOICES,
        help="Deployment target. Use aws, gcp, or talos-omni. Defaults to EVE_TRADE_CLOUD_PROVIDER, CLOUD_PROVIDER, or aws.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check", parents=[shared])
    subcommands.add_parser("test", parents=[shared])
    subcommands.add_parser("security", parents=[shared])
    subcommands.add_parser("build", parents=[shared])
    subcommands.add_parser("publish", parents=[shared])
    subcommands.add_parser("integration", parents=[shared])

    terraform = subcommands.add_parser("terraform", parents=[shared])
    terraform.add_argument(
        "--all-clouds",
        "--all-targets",
        dest="all_clouds",
        action="store_true",
        help="Validate all Terraform roots instead of only the selected deployment target.",
    )

    render = subcommands.add_parser("render-kubernetes", parents=[shared])
    render.add_argument(
        "--output",
        default="ci-cd/out/kubernetes.yaml",
        help="Path to write rendered Kubernetes YAML.",
    )

    render_chaos = subcommands.add_parser("render-chaos", parents=[shared])
    render_chaos.add_argument(
        "--output",
        default="ci-cd/out/chaos-litmus.yaml",
        help="Path to write rendered Litmus chaos YAML.",
    )

    chaos = subcommands.add_parser("chaos", parents=[shared])
    chaos.add_argument(
        "--namespace",
        default=env("CHAOS_NAMESPACE", "eve-trade"),
        help="Kubernetes namespace containing Eve Trade and the Litmus chaos experiments.",
    )
    chaos.add_argument(
        "--selector",
        default=env("CHAOS_SELECTOR", "chaos.eve-trade.io/suite=pod-resilience"),
        help="Label selector identifying the ChaosEngines to activate.",
    )
    chaos.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(env("CHAOS_TIMEOUT_SECONDS", "900")),
        help="Maximum time to wait for all selected ChaosResults to pass.",
    )
    chaos.add_argument(
        "--cleanup",
        action="store_true",
        default=env("CHAOS_CLEANUP", "").lower() in {"1", "true", "yes"},
        help="Delete selected ChaosEngines and ChaosResults after a successful run.",
    )

    subcommands.add_parser("deploy", parents=[shared])
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    config = dagger.Config(log_output=sys.stderr)
    async with dagger.Connection(config) as client:
        pipeline = EveTradePipeline(client)
        selected_provider = cloud_provider(args.cloud_provider)
        registry = image_registry(args.registry, selected_provider)
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
            await pipeline.publish_images(registry, tag, selected_provider)
        elif args.command == "integration":
            await pipeline.integration()
        elif args.command == "terraform":
            providers = (
                tuple(TERRAFORM_ROOTS)
                if args.all_clouds
                else (selected_provider,)
            )
            await pipeline.terraform_checks(providers)
        elif args.command == "render-kubernetes":
            await pipeline.render_kubernetes(registry, tag, args.output)
        elif args.command == "deploy":
            await pipeline.deploy(registry, tag)
        elif args.command == "render-chaos":
            await pipeline.render_chaos(args.output)
        elif args.command == "chaos":
            await pipeline.chaos(
                namespace=args.namespace,
                selector=args.selector,
                timeout_seconds=args.timeout_seconds,
                cleanup=args.cleanup,
            )
        else:
            raise ValueError(f"unknown command: {args.command}")


def main() -> None:
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
