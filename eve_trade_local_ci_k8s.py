#!/usr/bin/env python3
"""
eve_trade_local_ci_k8s.py

Local CI/CD + local Kubernetes runner for the eve-trade repository.

What this script does:
  1. Runs local CI checks:
     - git sanity
     - Buf checks when buf.yaml exists
     - Go tests for every go.mod found
     - Rust fmt/check/clippy/test for every Cargo.toml workspace/crate found
     - Docker Compose integration e2e using the safe migrate-first flow

  2. Builds local Docker images for known eve-trade services.

  3. Deploys the project to a local Kubernetes cluster:
     - Docker Desktop Kubernetes, current kubectl context, or kind
     - auto-detects common Kubernetes manifest/kustomize paths
     - applies manifests
     - optionally patches deployment images to local tags
     - waits for pods/deployments to become ready
     - prints useful debugging commands on failure

This script is intentionally conservative:
  - It does not silently create cloud resources.
  - It does not assume AWS/EKS.
  - It treats local integration tests as the quality gate before Kubernetes deployment.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


# -----------------------------
# Configuration
# -----------------------------

DEFAULT_NAMESPACE = "eve-trade-local"
DEFAULT_KIND_CLUSTER = "eve-trade-local"

COMPOSE_FILE = "docker-compose.integration.yml"

# Compose service list intentionally excludes migrate.
# Migration must be run separately before the abort-controlled e2e run.
COMPOSE_E2E_SERVICES = [
    "api-gateway",
    "market",
    "trade-settlement",
    "settlement-worker",
    "simulator",
    "quilkin",
    "e2e-tests",
]

# Best-effort image build map. The script skips missing Dockerfiles unless --strict is used.
IMAGE_TARGETS = {
    "api-gateway": {
        "dockerfile_candidates": [
            "distributed-backend/docker/api-gateway.Dockerfile",
            "distributed-backend/src/api-gateway/Dockerfile",
            "api-gateway.Dockerfile",
        ],
        "image": "eve-trade/api-gateway:local",
    },
    "market": {
        "dockerfile_candidates": [
            "distributed-backend/docker/market.Dockerfile",
            "distributed-backend/src/market/Dockerfile",
            "market.Dockerfile",
        ],
        "image": "eve-trade/market:local",
    },
    "trade-settlement": {
        "dockerfile_candidates": [
            "distributed-backend/docker/trade-settlement.Dockerfile",
            "distributed-backend/src/trade-settlement/Dockerfile",
            "trade-settlement.Dockerfile",
        ],
        "image": "eve-trade/trade-settlement:local",
    },
    "settlement-worker": {
        "dockerfile_candidates": [
            "distributed-backend/docker/settlement-worker.Dockerfile",
            "distributed-backend/src/settlement-worker/Dockerfile",
            "settlement-worker.Dockerfile",
        ],
        "image": "eve-trade/settlement-worker:local",
    },
    "simulator": {
        "dockerfile_candidates": [
            "simulator/Dockerfile",
            "distributed-backend/simulator/Dockerfile",
        ],
        "image": "eve-trade/simulator:local",
    },
    "quilkin": {
        "dockerfile_candidates": [
            "distributed-backend/docker/quilkin.Dockerfile",
            "quilkin.Dockerfile",
        ],
        "image": "eve-trade/quilkin:dev",
    },
}

# Likely local/dev kustomize or manifest roots.
K8S_PATH_CANDIDATES = [
    "distributed-backend/k8s/overlays/local",
    "distributed-backend/k8s/overlays/dev",
    "distributed-backend/k8s/local",
    "distributed-backend/k8s/dev",
    "distributed-backend/k8s",
    "k8s/overlays/local",
    "k8s/overlays/dev",
    "k8s/local",
    "k8s/dev",
    "k8s",
    "deploy/k8s/overlays/local",
    "deploy/k8s/overlays/dev",
    "deploy/k8s",
    "infrastructure/kubernetes/overlays/local",
    "infrastructure/kubernetes/overlays/dev",
    "infrastructure/kubernetes",
]


# -----------------------------
# Runtime helpers
# -----------------------------

@dataclass
class RunResult:
    args: list[str]
    cwd: Path
    returncode: int


class Runner:
    def __init__(self, *, repo_root: Path, dry_run: bool, keep_going: bool, verbose: bool):
        self.repo_root = repo_root
        self.dry_run = dry_run
        self.keep_going = keep_going
        self.verbose = verbose
        self.failures: list[RunResult] = []

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
        shell: bool = False,
    ) -> RunResult:
        cwd = cwd or self.repo_root
        rendered = " ".join(str(a) for a in args)
        print(f"\n$ {rendered}")
        print(f"  cwd: {cwd}")

        if self.dry_run:
            return RunResult(list(args), cwd, 0)

        process_env = os.environ.copy()
        if env:
            process_env.update(env)

        completed = subprocess.run(
            list(args),
            cwd=str(cwd),
            env=process_env,
            text=True,
            shell=shell,
        )
        result = RunResult(list(args), cwd, completed.returncode)

        if completed.returncode != 0:
            self.failures.append(result)
            if check and not self.keep_going:
                raise SystemExit(completed.returncode)

        return result

    def require_tool(self, name: str, *, install_hint: str | None = None) -> bool:
        if shutil.which(name):
            return True

        print(f"[missing] Required tool not found on PATH: {name}")
        if install_hint:
            print(f"          {install_hint}")

        if not self.keep_going:
            raise SystemExit(127)
        return False

    def optional_tool(self, name: str) -> bool:
        found = shutil.which(name) is not None
        if not found:
            print(f"[skip] Optional tool not found on PATH: {name}")
        return found


def is_windows() -> bool:
    return platform.system().lower().startswith("win")


def path_exists(repo_root: Path, relative: str) -> bool:
    return (repo_root / relative).exists()


def find_first_existing(repo_root: Path, candidates: Iterable[str]) -> Path | None:
    for candidate in candidates:
        p = repo_root / candidate
        if p.exists():
            return p
    return None


def find_files(repo_root: Path, filename: str, *, ignore_dirs: set[str] | None = None) -> list[Path]:
    ignore_dirs = ignore_dirs or {
        ".git",
        ".idea",
        ".venv",
        "venv",
        "target",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
    }

    found: list[Path] = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        if filename in files:
            found.append(Path(root) / filename)
    return sorted(found)


def has_kustomization(path: Path) -> bool:
    names = {"kustomization.yaml", "kustomization.yml", "Kustomization"}
    return any((path / name).exists() for name in names)


# -----------------------------
# CI phases
# -----------------------------

def phase_preflight(r: Runner, args: argparse.Namespace) -> None:
    print("\n=== Preflight ===")
    print(f"repo_root: {r.repo_root}")
    print(f"platform: {platform.platform()}")
    print(f"python: {sys.version.split()[0]}")

    required = ["git", "docker"]
    if not args.skip_k8s:
        required.append("kubectl")

    for tool in required:
        r.require_tool(tool)

    # `docker compose` is a subcommand, not always a separate binary.
    r.run(["docker", "compose", "version"], check=True)

    if not args.skip_k8s and args.cluster == "kind":
        r.require_tool("kind", install_hint="Install kind or use --cluster current / --cluster docker-desktop.")

    r.run(["git", "rev-parse", "--show-toplevel"], check=False)
    r.run(["git", "branch", "--show-current"], check=False)
    r.run(["git", "rev-parse", "--short", "HEAD"], check=False)
    r.run(["git", "status", "--short"], check=False)


def phase_buf(r: Runner, args: argparse.Namespace) -> None:
    if args.skip_buf:
        print("\n=== Buf checks skipped ===")
        return

    print("\n=== Buf checks ===")
    if not find_first_existing(r.repo_root, ["buf.yaml", "buf.yml", "distributed-backend/proto/buf.yaml"]):
        print("[skip] No buf.yaml found.")
        return

    if not r.optional_tool("buf"):
        if args.strict:
            raise SystemExit("buf.yaml exists but buf is not installed.")
        return

    # Try repo-root first. If the proto directory has its own buf.yaml, also run there.
    candidate_dirs = [r.repo_root]
    proto_buf = r.repo_root / "distributed-backend/proto/buf.yaml"
    if proto_buf.exists():
        candidate_dirs.append(proto_buf.parent)

    seen: set[Path] = set()
    for cwd in candidate_dirs:
        if cwd in seen:
            continue
        seen.add(cwd)
        if not ((cwd / "buf.yaml").exists() or (cwd / "buf.yml").exists()):
            continue

        r.run(["buf", "format", "--diff", "--exit-code"], cwd=cwd)
        r.run(["buf", "lint"], cwd=cwd)
        r.run(["buf", "build"], cwd=cwd)
        if args.buf_generate:
            r.run(["buf", "generate"], cwd=cwd)


def phase_go(r: Runner, args: argparse.Namespace) -> None:
    if args.skip_go:
        print("\n=== Go checks skipped ===")
        return

    print("\n=== Go checks ===")
    go_mods = find_files(r.repo_root, "go.mod")
    if not go_mods:
        print("[skip] No go.mod files found.")
        return

    if not r.optional_tool("go"):
        if args.strict:
            raise SystemExit("go.mod files exist but Go is not installed.")
        return

    for go_mod in go_mods:
        cwd = go_mod.parent
        rel = cwd.relative_to(r.repo_root)
        print(f"\n--- Go module: {rel} ---")
        r.run(["go", "mod", "download"], cwd=cwd)
        r.run(["go", "test", "./..."], cwd=cwd)
        if args.go_vet:
            r.run(["go", "vet", "./..."], cwd=cwd)


def phase_rust(r: Runner, args: argparse.Namespace) -> None:
    if args.skip_rust:
        print("\n=== Rust checks skipped ===")
        return

    print("\n=== Rust checks ===")
    cargo_tomls = find_files(r.repo_root, "Cargo.toml")
    if not cargo_tomls:
        print("[skip] No Cargo.toml files found.")
        return

    if not r.optional_tool("cargo"):
        if args.strict:
            raise SystemExit("Cargo.toml files exist but cargo is not installed.")
        return

    # Avoid double-running nested crates when a workspace root contains them.
    # This simple approach runs only Cargo.toml files that are not under another Cargo.toml directory.
    roots: list[Path] = []
    for cargo_toml in cargo_tomls:
        cwd = cargo_toml.parent
        if any(parent in cwd.parents for parent in roots):
            continue
        roots.append(cwd)

    for cwd in roots:
        rel = cwd.relative_to(r.repo_root)
        print(f"\n--- Rust crate/workspace: {rel} ---")
        r.run(["cargo", "fmt", "--all", "--", "--check"], cwd=cwd, check=not args.allow_missing_rustfmt)
        r.run(["cargo", "check", "--locked", "--all-targets"], cwd=cwd)
        r.run(["cargo", "test", "--locked"], cwd=cwd)
        if args.clippy:
            r.run(["cargo", "clippy", "--locked", "--all-targets", "--", "-D", "warnings"], cwd=cwd)


def phase_compose_integration(r: Runner, args: argparse.Namespace) -> None:
    if args.skip_compose:
        print("\n=== Docker Compose integration skipped ===")
        return

    print("\n=== Docker Compose integration e2e ===")
    compose_path = r.repo_root / COMPOSE_FILE
    if not compose_path.exists():
        message = f"{COMPOSE_FILE} not found."
        if args.strict:
            raise SystemExit(message)
        print(f"[skip] {message}")
        return

    # Clean previous runs first.
    if not args.no_compose_cleanup:
        r.run([
            "docker", "compose",
            "-f", COMPOSE_FILE,
            "--profile", "test",
            "down", "-v", "--remove-orphans",
        ], check=False)

    # Start durable dependencies.
    r.run([
        "docker", "compose",
        "-f", COMPOSE_FILE,
        "--profile", "test",
        "up", "--build", "-d", "postgres", "rabbitmq",
    ])

    try:
        # Run one-shot migration outside the abort-controlled test run.
        r.run([
            "docker", "compose",
            "-f", COMPOSE_FILE,
            "run", "--rm", "migrate",
        ])

        # Run services and tests. e2e-tests controls the exit code.
        r.run([
            "docker", "compose",
            "-f", COMPOSE_FILE,
            "--profile", "test",
            "up", "--build",
            "--abort-on-container-exit",
            "--exit-code-from", "e2e-tests",
            *COMPOSE_E2E_SERVICES,
        ])
    finally:
        if not args.no_compose_cleanup:
            r.run([
                "docker", "compose",
                "-f", COMPOSE_FILE,
                "--profile", "test",
                "down", "-v", "--remove-orphans",
            ], check=False)


# -----------------------------
# Docker image + Kubernetes phases
# -----------------------------

def phase_build_images(r: Runner, args: argparse.Namespace) -> dict[str, str]:
    if args.skip_image_build:
        print("\n=== Docker image build skipped ===")
        return {service: cfg["image"] for service, cfg in IMAGE_TARGETS.items()}

    print("\n=== Build local Docker images ===")
    built: dict[str, str] = {}

    for service, cfg in IMAGE_TARGETS.items():
        dockerfile = find_first_existing(r.repo_root, cfg["dockerfile_candidates"])
        image = cfg["image"]

        if dockerfile is None:
            message = f"No Dockerfile found for {service}. Tried: {cfg['dockerfile_candidates']}"
            if args.strict:
                raise SystemExit(message)
            print(f"[skip] {message}")
            continue

        rel_dockerfile = str(dockerfile.relative_to(r.repo_root)).replace("\\", "/")
        r.run([
            "docker", "build",
            "-f", rel_dockerfile,
            "-t", image,
            ".",
        ])
        built[service] = image

    return built


def kubectl_base(args: argparse.Namespace) -> list[str]:
    base = ["kubectl"]
    if args.k8s_context:
        base += ["--context", args.k8s_context]
    return base


def ensure_kubernetes_context(r: Runner, args: argparse.Namespace) -> None:
    print("\n=== Kubernetes context ===")

    if args.cluster == "docker-desktop":
        args.k8s_context = args.k8s_context or "docker-desktop"
        r.run(kubectl_base(args) + ["cluster-info"])

    elif args.cluster == "kind":
        r.run(["kind", "get", "clusters"], check=False)
        # Create if absent. This is intentionally idempotent-ish.
        result = subprocess.run(
            ["kind", "get", "clusters"],
            cwd=str(r.repo_root),
            capture_output=True,
            text=True,
        )
        existing_clusters = set(result.stdout.split())
        if args.kind_cluster not in existing_clusters:
            r.run(["kind", "create", "cluster", "--name", args.kind_cluster])
        args.k8s_context = args.k8s_context or f"kind-{args.kind_cluster}"
        r.run(kubectl_base(args) + ["cluster-info"])

    elif args.cluster == "current":
        r.run(kubectl_base(args) + ["config", "current-context"])
        r.run(kubectl_base(args) + ["cluster-info"])

    else:
        raise SystemExit(f"Unsupported cluster mode: {args.cluster}")


def load_images_into_kind(r: Runner, args: argparse.Namespace, built_images: dict[str, str]) -> None:
    if args.cluster != "kind":
        return

    print("\n=== Load local images into kind ===")
    for image in built_images.values():
        r.run(["kind", "load", "docker-image", image, "--name", args.kind_cluster])


def detect_k8s_path(repo_root: Path, explicit: str | None) -> Path | None:
    if explicit:
        p = repo_root / explicit
        return p if p.exists() else None
    return find_first_existing(repo_root, K8S_PATH_CANDIDATES)


def phase_kubernetes_deploy(r: Runner, args: argparse.Namespace, built_images: dict[str, str]) -> None:
    if args.skip_k8s:
        print("\n=== Kubernetes deploy skipped ===")
        return

    print("\n=== Kubernetes deploy ===")
    ensure_kubernetes_context(r, args)
    load_images_into_kind(r, args, built_images)

    k8s_path = detect_k8s_path(r.repo_root, args.k8s_path)
    if k8s_path is None:
        tried = "\n  - ".join(K8S_PATH_CANDIDATES)
        raise SystemExit(
            "Could not find a Kubernetes manifest/kustomize path.\n"
            "Pass one explicitly with --k8s-path.\n"
            f"Tried:\n  - {tried}"
        )

    print(f"Using Kubernetes path: {k8s_path.relative_to(r.repo_root)}")

    r.run(kubectl_base(args) + ["create", "namespace", args.namespace], check=False)

    if has_kustomization(k8s_path):
        r.run(kubectl_base(args) + ["apply", "-k", str(k8s_path)])
    elif k8s_path.is_dir():
        r.run(kubectl_base(args) + ["apply", "-f", str(k8s_path)])
    else:
        r.run(kubectl_base(args) + ["apply", "-f", str(k8s_path)])

    if args.patch_images:
        patch_deployment_images(r, args, built_images)

    wait_for_k8s(r, args)


def patch_deployment_images(r: Runner, args: argparse.Namespace, built_images: dict[str, str]) -> None:
    print("\n=== Patch Kubernetes deployment images ===")
    print("This is best-effort. Missing deployments are skipped unless --strict is used.")

    for service, image in built_images.items():
        deployment_names = [
            service,
            service.replace("_", "-"),
            f"eve-trade-{service}",
        ]

        patched = False
        for deployment in deployment_names:
            result = r.run(
                kubectl_base(args) + [
                    "-n", args.namespace,
                    "set", "image",
                    f"deployment/{deployment}",
                    f"{service}={image}",
                ],
                check=False,
            )
            if result.returncode == 0:
                patched = True
                break

        if not patched:
            message = f"Could not patch deployment image for service '{service}'."
            if args.strict:
                raise SystemExit(message)
            print(f"[skip] {message}")


def wait_for_k8s(r: Runner, args: argparse.Namespace) -> None:
    print("\n=== Wait for Kubernetes readiness ===")

    r.run(kubectl_base(args) + ["-n", args.namespace, "get", "pods", "-o", "wide"], check=False)
    r.run(kubectl_base(args) + ["-n", args.namespace, "get", "svc"], check=False)

    # Wait all pods. If there are completed one-shot Jobs, this can be imperfect, so we keep diagnostics useful.
    r.run(
        kubectl_base(args) + [
            "-n", args.namespace,
            "wait",
            "--for=condition=ready",
            "pod",
            "--all",
            f"--timeout={args.k8s_timeout}s",
        ],
        check=False,
    )

    deployments = subprocess.run(
        kubectl_base(args) + [
            "-n", args.namespace,
            "get", "deployments",
            "-o", "jsonpath={.items[*].metadata.name}",
        ],
        cwd=str(r.repo_root),
        capture_output=True,
        text=True,
    )

    if deployments.returncode == 0 and deployments.stdout.strip():
        for deployment in deployments.stdout.split():
            r.run(
                kubectl_base(args) + [
                    "-n", args.namespace,
                    "rollout",
                    "status",
                    f"deployment/{deployment}",
                    f"--timeout={args.k8s_timeout}s",
                ],
                check=False,
            )

    r.run(kubectl_base(args) + ["-n", args.namespace, "get", "all"], check=False)

    print("\nUseful debugging commands:")
    print(f"  kubectl -n {args.namespace} get pods -o wide")
    print(f"  kubectl -n {args.namespace} describe pod <pod-name>")
    print(f"  kubectl -n {args.namespace} logs deploy/api-gateway")
    print(f"  kubectl -n {args.namespace} logs deploy/market")
    print(f"  kubectl -n {args.namespace} logs deploy/trade-settlement")
    print(f"  kubectl -n {args.namespace} logs deploy/settlement-worker")


def phase_act(r: Runner, args: argparse.Namespace) -> None:
    if not args.run_act:
        return

    print("\n=== GitHub Actions via act ===")
    workflows = r.repo_root / ".github" / "workflows"
    if not workflows.exists():
        print("[skip] No .github/workflows directory found.")
        return

    if not r.optional_tool("act"):
        raise SystemExit("act was requested with --run-act, but it is not installed.")

    # This can be expensive. It is opt-in only.
    r.run(["act", "--list"])
    r.run(["act"])


# -----------------------------
# Main
# -----------------------------

def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run eve-trade local CI/CD checks and deploy to local Kubernetes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--repo-root", default=".", help="Repository root.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    parser.add_argument("--keep-going", action="store_true", help="Continue after failed commands and summarize failures.")
    parser.add_argument("--strict", action="store_true", help="Fail when optional expected files/tools are missing.")
    parser.add_argument("--verbose", action="store_true", help="Reserved for extra logging.")

    parser.add_argument("--skip-buf", action="store_true")
    parser.add_argument("--skip-go", action="store_true")
    parser.add_argument("--skip-rust", action="store_true")
    parser.add_argument("--skip-compose", action="store_true")
    parser.add_argument("--skip-image-build", action="store_true")
    parser.add_argument("--skip-k8s", action="store_true")

    parser.add_argument("--buf-generate", action="store_true", help="Also run buf generate.")
    parser.add_argument("--go-vet", action="store_true", help="Also run go vet ./... for each module.")
    parser.add_argument("--clippy", action="store_true", default=True, help="Run cargo clippy with -D warnings.")
    parser.add_argument("--no-clippy", action="store_false", dest="clippy", help="Disable cargo clippy.")
    parser.add_argument("--allow-missing-rustfmt", action="store_true", help="Do not hard-fail if cargo fmt/rustfmt is unavailable.")
    parser.add_argument("--no-compose-cleanup", action="store_true", help="Do not compose down -v before/after integration test.")

    parser.add_argument(
        "--cluster",
        choices=["current", "docker-desktop", "kind"],
        default="current",
        help="Local Kubernetes target.",
    )
    parser.add_argument("--k8s-context", default=None, help="kubectl context override.")
    parser.add_argument("--kind-cluster", default=DEFAULT_KIND_CLUSTER, help="kind cluster name.")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Kubernetes namespace.")
    parser.add_argument("--k8s-path", default=None, help="Path to Kubernetes manifests or kustomize overlay.")
    parser.add_argument("--k8s-timeout", type=int, default=180, help="Kubernetes readiness timeout in seconds.")
    parser.add_argument("--patch-images", action="store_true", help="Best-effort patch deployments to locally built images.")

    parser.add_argument("--run-act", action="store_true", help="Optionally run GitHub Actions locally using act.")

    return parser.parse_args(argv)


def summarize_failures(r: Runner) -> None:
    if not r.failures:
        print("\n=== Completed successfully ===")
        return

    print("\n=== Failures ===")
    for failure in r.failures:
        print(f"- exit={failure.returncode} cwd={failure.cwd} cmd={' '.join(failure.args)}")

    raise SystemExit(r.failures[-1].returncode)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()

    if not repo_root.exists():
        print(f"Repo root does not exist: {repo_root}", file=sys.stderr)
        return 2

    r = Runner(
        repo_root=repo_root,
        dry_run=args.dry_run,
        keep_going=args.keep_going,
        verbose=args.verbose,
    )

    try:
        phase_preflight(r, args)
        phase_act(r, args)
        phase_buf(r, args)
        phase_go(r, args)
        phase_rust(r, args)
        phase_compose_integration(r, args)
        built_images = phase_build_images(r, args)
        phase_kubernetes_deploy(r, args, built_images)
        summarize_failures(r)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
