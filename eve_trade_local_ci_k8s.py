#!/usr/bin/env python3
"""Local verification helper for the Encore-based eve-trade backend."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
KUSTOMIZE_ROOTS = (
    "distributed-backend/orchestration/kubernetes/base",
    "distributed-backend/orchestration/kubernetes/overlay/local",
    "distributed-backend/orchestration/kubernetes/overlay/prod",
    "distributed-backend/orchestration/kubernetes/chaos/litmus/overlays/prod",
)
TERRAFORM_ROOTS = (
    "distributed-backend/terraform/eks",
    "distributed-backend/terraform/gke",
    "distributed-backend/terraform/talos-omni",
)


def run(argv: list[str], *, cwd: Path = ROOT, check: bool = True) -> int:
    print("$", " ".join(argv))
    result = subprocess.run(argv, cwd=cwd, text=True)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def has(name: str) -> bool:
    found = shutil.which(name) is not None
    if not found:
        print(f"[skip] {name} not found")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local checks for the Encore eve-trade backend")
    parser.add_argument("--skip-go", action="store_true")
    parser.add_argument("--skip-rust", action="store_true")
    parser.add_argument("--skip-k8s", action="store_true")
    parser.add_argument("--skip-terraform", action="store_true")
    parser.add_argument("--build-image", default="", help="Optional image name for encore build docker")
    args = parser.parse_args()

    run(["git", "status", "--short"], check=False)

    if has("buf"):
        run(["buf", "format", "--diff", "--exit-code"])
        run(["buf", "lint"])
        run(["buf", "build"])

    if not args.skip_go and has("go"):
        if has("encore"):
            run(["encore", "test", "./..."])
            if args.build_image:
                run(["encore", "build", "docker", "--config", "infra/encore/self-host.nsq.json", args.build_image])
        else:
            print("[skip] encore not found; plain go test cannot initialize Encore Pub/Sub topics")
        run(["go", "vet", "./..."], check=False)

    if not args.skip_rust and has("cargo"):
        rust = ROOT / "distributed-backend" / "src" / "trade-settlement"
        run(["cargo", "fmt", "--all", "--", "--check"], cwd=rust)
        run(["cargo", "check", "--locked", "--all-targets", "--all-features"], cwd=rust)
        run(["cargo", "test", "--locked", "--all-features"], cwd=rust)
        run(["cargo", "clippy", "--locked", "--all-targets", "--all-features", "--", "-D", "warnings"], cwd=rust)

    if not args.skip_k8s and has("kubectl"):
        for root in KUSTOMIZE_ROOTS:
            run(["kubectl", "kustomize", root])

    if not args.skip_terraform and has("terraform"):
        run(["terraform", "fmt", "-check", "-recursive", "distributed-backend/terraform"])
        for root in TERRAFORM_ROOTS:
            run(["terraform", f"-chdir={root}", "init", "-backend=false"])
            run(["terraform", f"-chdir={root}", "validate"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
