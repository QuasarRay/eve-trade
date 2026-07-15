#!/usr/bin/env python3
"""Canonical, fail-closed verification entry point for eve-trade."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUST_ROOT = ROOT / "distributed-backend" / "src" / "trade-settlement"
COMPONENTS = (
    "protobuf",
    "architecture",
    "gui",
    "go",
    "rust",
    "python",
    "terraform",
    "kubernetes",
    "security",
    "e2e",
    "o11y",
)
KUSTOMIZE_ROOTS = (
    "distributed-backend/orchestration/kubernetes/base",
    "distributed-backend/orchestration/kubernetes/overlay/local",
    "distributed-backend/orchestration/kubernetes/overlay/prod",
    "distributed-backend/orchestration/kubernetes/chaos/litmus/overlays/prod",
)
TERRAFORM_ROOTS = (
    ("distributed-backend/terraform/eks", "-lockfile=readonly"),
    ("distributed-backend/terraform/gke", ""),
    ("distributed-backend/terraform/talos-omni", "-lockfile=readonly"),
)


def require(*commands: str) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise RuntimeError(f"required command(s) not found: {', '.join(missing)}")


def run(argv: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("$", " ".join(argv), flush=True)
    subprocess.run(argv, cwd=cwd, env=env, check=True)


def run_shell(command: str, *, cwd: Path = ROOT) -> None:
    require("bash")
    run(["bash", "-lc", command], cwd=cwd)


def protobuf() -> None:
    require("buf", "go", "git")
    run(["go", "install", "google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11"])
    run(["buf", "format", "--diff", "--exit-code"])
    run(["buf", "lint"])
    run(["buf", "build"])
    run(["buf", "generate"])
    run(["buf", "generate"])
    run(["git", "diff", "--exit-code", "--", "proto/gen"])


def architecture() -> None:
    require("python", "git")
    run(["python", "-m", "pip", "install", "PyYAML==6.0.3"])
    run(["python", "scripts/verify_architecture_boundaries.py"])
    run(["python", "scripts/verify_schema_ownership.py"])
    run(["python", "-m", "unittest", "discover", "-s", "scripts/tests", "-v"])


def gui() -> None:
    require("pnpm")
    run(["pnpm", "install", "--frozen-lockfile"])
    run(["pnpm", "run", "gui:test"])


def go() -> None:
    require("encore", "go", "gofmt", "git", "govulncheck", "staticcheck")
    version = subprocess.run(["go", "version"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    print(version)
    if "go1.26.5" not in version:
        raise RuntimeError(f"full verification requires Go 1.26.5, found: {version}")
    encore_binary = shutil.which("encore")
    if encore_binary is None:
        raise RuntimeError("encore executable disappeared after prerequisite validation")
    encore_install = Path(encore_binary).resolve().parent.parent
    run(["bash", "scripts/harden_encore_install.sh", str(encore_install)])
    run(["go", "mod", "download"])
    run(["go", "mod", "verify"])
    run(["go", "mod", "tidy"])
    run(["git", "diff", "--exit-code", "--", "go.mod", "go.sum"])
    gofmt_roots = [
        "distributed-backend/src/gateway",
        "distributed-backend/src/market",
        "distributed-backend/src/settlement",
        "distributed-backend/src/settlementworker",
        "distributed-backend/internal",
        "gametrade",
        "proto/gen",
        "go_modules_test.go",
    ]
    formatted = subprocess.run(["gofmt", "-l", *gofmt_roots], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    if formatted.strip():
        raise RuntimeError(f"gofmt required for:\n{formatted}")
    # Encore delegates to Go's test runner after installing the generated
    # Pub/Sub and structured-error runtime required by this application.
    encore_env = os.environ.copy()
    encore_env["GOFLAGS"] = "-mod=mod"
    run(["encore", "test", "./..."], env=encore_env)
    race_env = os.environ.copy()
    race_env["ENCORERUNTIME_NOPANIC"] = "1"
    run(["go", "test", "-race", "./distributed-backend/src/gateway", "./distributed-backend/src/settlementworker", "./distributed-backend/internal/...", "./gametrade"], env=race_env)
    run(["go", "test", "-race", "./distributed-backend/src/market", "-run", "^TestSettlement", "-skip", "APIErrors"], env=race_env)
    run(["go", "test", "-race", "./distributed-backend/src/market", "-run", "^TestDuplicateSettlementResultProjectionIsHarmless$"], env=race_env)
    run(["go", "vet", "./..."])
    run(["staticcheck", "./..."])
    run(["govulncheck", "-version"])
    run(["govulncheck", "./..."])
    run(["go", "test", "-run", "^$", "-fuzz", "^FuzzAuthenticatedPayload", "-fuzztime", "10s", "./distributed-backend/src/gateway"], env=race_env)
    image = f"eve-trade/encore-backend:{subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}"
    run(["encore", "build", "docker", "--config", "infra/encore/self-host.nsq.json", image], env=encore_env)


def rust() -> None:
    require("cargo")
    run(["cargo", "fmt", "--all", "--", "--check"], cwd=RUST_ROOT)
    run(["cargo", "check", "--locked", "--all-targets", "--all-features"], cwd=RUST_ROOT)
    run(["cargo", "test", "--locked", "--all-features"], cwd=RUST_ROOT)
    run(["cargo", "clippy", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings"], cwd=RUST_ROOT)


def python_checks() -> None:
    require("python")
    run(["python", "-m", "compileall", "simulator/eve_trade_simulator", "simulator/trade_gui", "distributed-backend/tests/e2e", "distributed-backend/observability"])
    run(["python", "-m", "coverage", "run", "--rcfile=.coveragerc", "manage.py", "test", "trade_gui"], cwd=ROOT / "simulator")
    run(["python", "-m", "coverage", "report", "--rcfile=.coveragerc", "--fail-under=80"], cwd=ROOT / "simulator")
    env = os.environ.copy()
    env["PYTHONPATH"] = "distributed-backend"
    run(["python", "-m", "coverage", "run", "--rcfile=distributed-backend/observability/.coveragerc", "-m", "unittest", "discover", "-s", "distributed-backend/observability/tests", "-v"], env=env)
    run(["python", "-m", "coverage", "report", "--rcfile=distributed-backend/observability/.coveragerc", "--fail-under=35"])


def terraform() -> None:
    require("terraform")
    run(["terraform", "version"])
    run(["terraform", "fmt", "-check", "-recursive", "distributed-backend/terraform"])
    for root, lockfile_arg in TERRAFORM_ROOTS:
        init = ["terraform", f"-chdir={root}", "init", "-backend=false"]
        if lockfile_arg:
            init.append(lockfile_arg)
        run([*init, "-no-color"])
        if root == "distributed-backend/terraform/eks":
            run(["terraform", f"-chdir={root}", "providers", "lock", "-platform=linux_amd64", "-platform=windows_amd64"])
            run(["git", "diff", "--exit-code", "--", f"{root}/.terraform.lock.hcl"])
        run(["terraform", f"-chdir={root}", "providers"])
        run(["terraform", f"-chdir={root}", "validate", "-no-color"])
        run(["terraform", f"-chdir={root}", "test", "-no-color"])


def kubernetes() -> None:
    require("kubectl", "python")
    for root in KUSTOMIZE_ROOTS:
        run(["kubectl", "kustomize", root])
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as rendered:
        rendered_path = Path(rendered.name)
    try:
        output = subprocess.run(["kubectl", "kustomize", KUSTOMIZE_ROOTS[2]], cwd=ROOT, check=True, capture_output=True)
        rendered_path.write_bytes(output.stdout)
        run(["python", "scripts/verify_rendered_kubernetes.py", "--expect-unresolved-image-template", str(rendered_path)])
    finally:
        rendered_path.unlink(missing_ok=True)


def security() -> None:
    require("cargo", "cargo-audit", "govulncheck", "pip-audit", "trivy")
    run(["govulncheck", "./..."])
    run(["cargo", "audit", "--deny", "warnings", "--ignore", "RUSTSEC-2023-0071"], cwd=RUST_ROOT)
    for requirement in (
        "simulator/requirements.txt",
        "simulator/requirements-test.txt",
        "distributed-backend/tests/e2e/requirements.txt",
        "distributed-backend/observability/requirements.txt",
        "distributed-backend/observability/requirements-test.txt",
    ):
        run(["pip-audit", "--requirement", requirement])
    image = f"eve-trade/encore-backend:{subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}"
    run(["trivy", "image", "--scanners", "vuln", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", image])
    run(["trivy", "fs", "--scanners", "vuln,secret,misconfig", "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--ignorefile", ".trivyignore.yaml", "--show-suppressed", "--exit-code", "1", "."])


def e2e() -> None:
    require("bash", "docker", "kind", "kubectl", "python")
    run(["bash", "scripts/run_kind_e2e.sh"])


def o11y() -> None:
    require("python")
    env = os.environ.copy()
    env["PYTHONPATH"] = "distributed-backend"
    run(["python", "-m", "unittest", "discover", "-s", "distributed-backend/observability/tests", "-v"], env=env)


RUNNERS = {
    "protobuf": protobuf,
    "architecture": architecture,
    "gui": gui,
    "go": go,
    "rust": rust,
    "python": python_checks,
    "terraform": terraform,
    "kubernetes": kubernetes,
    "security": security,
    "e2e": e2e,
    "o11y": o11y,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", action="append", choices=COMPONENTS, help="Run only this component; repeatable and reported as partial verification")
    args = parser.parse_args()
    selected = tuple(dict.fromkeys(args.component or COMPONENTS))

    try:
        for component in selected:
            print(f"\n== {component} ==", flush=True)
            RUNNERS[component]()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"\nVERIFICATION_RESULT=FAILED\n{error}", file=sys.stderr)
        return 1

    if selected == COMPONENTS:
        print("\nVERIFICATION_RESULT=COMPLETE_PASS")
    else:
        print(f"\nVERIFICATION_RESULT=PARTIAL_PASS components={','.join(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
