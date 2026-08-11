"""Phase-addressable Go/Encore verification for observed command evidence."""
from __future__ import annotations

import os
import sys

from _common import sh, stage, with_docker_socket, with_env, with_source
from _images import DOCKER_CLI_IMAGE, GOLANG_IMAGE, PYTHON_BOOKWORM_IMAGE, TRIVY_IMAGE


ENCORE_VERSION = "1.57.9"
ENCORE_SHA256 = "dfd43dcd456f91414a823315480da921333e6d1e3535ab48c47c09225d022af5"
PHASES = {"tests", "race", "quality", "audit", "image"}


def base(dag, *, encore: bool = False, docker: bool = False):
    if encore:
        # The full pinned Python Bookworm image supplies Python, curl, git, and
        # certificates required by Encore hardening.  Copying the pinned Go
        # toolchain avoids a mutable apt transaction in the build environment.
        go_toolchain = dag.container().from_(GOLANG_IMAGE)
        ctr = dag.container().from_(PYTHON_BOOKWORM_IMAGE).with_directory(
            "/usr/local/go", go_toolchain.directory("/usr/local/go")
        )
    else:
        ctr = dag.container().from_(GOLANG_IMAGE)
    if encore:
        ctr = ctr.with_env_variable("GOPATH", "/go").with_env_variable("GOTOOLCHAIN", "local")
    if docker:
        docker_tools = dag.container().from_(DOCKER_CLI_IMAGE).with_entrypoint([])
        ctr = ctr.with_file("/usr/local/bin/docker", docker_tools.file("/usr/local/bin/docker"))
    ctr = with_source(ctr, dag, include_git=True)
    if docker:
        ctr = with_docker_socket(ctr, dag)
    ctr = with_env(
        ctr,
        {
            "ENCORE_CLI_VERSION": ENCORE_VERSION,
            "ENCORE_CLI_SHA256": ENCORE_SHA256,
            "ENCORE_INSTALL": "/opt/encore",
            "ENCORERUNTIME_NOPANIC": "1",
        },
    )
    if encore:
        ctr = ctr.with_env_variable(
            "PATH",
            "/opt/encore/bin:/go/bin:/usr/local/go/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        )
        ctr = sh(
            ctr,
            [
                'ENCORE_INSTALL="/opt/encore" bash scripts/install_encore_cli.sh',
                'bash scripts/harden_encore_install.sh "/opt/encore"',
            ],
        )
    return ctr


async def run(dag, phase: str):
    if phase == "tests":
        ctr = base(dag, encore=True)
        ctr = sh(
            ctr,
            [
                "encore --help >/dev/null",
                "go version",
                "go mod download",
                "go mod verify",
                "go mod tidy",
                "git diff --exit-code -- go.mod go.sum",
                'files="$(gofmt -l distributed-backend/src/gateway distributed-backend/src/market distributed-backend/src/settlement distributed-backend/src/settlementworker distributed-backend/internal gametrade proto/gen go_modules_test.go)"; test -z "$files" || { printf "%s\\n" "$files"; exit 1; }',
                "GOFLAGS=-mod=mod encore test ./...",
            ],
        )
        await ctr.sync()
    elif phase == "race":
        ctr = sh(base(dag), ["go mod download", "go mod verify", "ENCORERUNTIME_NOPANIC=1 go test -race -p=1 -timeout=5m ./..."])
        await ctr.sync()
    elif phase == "quality":
        ctr = sh(
            base(dag),
            [
                "go mod download",
                "go mod verify",
                "go vet ./...",
                "go install honnef.co/go/tools/cmd/staticcheck@v0.7.0",
                "staticcheck ./...",
                "ENCORERUNTIME_NOPANIC=1 go test -run '^$' -fuzz '^FuzzAuthenticatedPayload' -fuzztime 10s ./distributed-backend/src/gateway",
            ],
        )
        await ctr.sync()
    elif phase == "audit":
        ctr = sh(
            base(dag),
            [
                "go mod download",
                "go mod verify",
                "go install golang.org/x/vuln/cmd/govulncheck@v1.5.0",
                "govulncheck -version",
                "govulncheck ./...",
            ],
        )
        await ctr.sync()
    elif phase == "image":
        sha = os.environ.get("GITHUB_SHA", "local")
        image = f"eve-trade/encore-backend:{sha}"
        ctr = sh(
            base(dag, encore=True, docker=True),
            [f"GOFLAGS=-mod=mod encore build docker --config infra/encore/self-host.nsq.json {image}"],
        )
        await ctr.sync()
        scan = dag.container().from_(TRIVY_IMAGE).with_entrypoint([])
        scan = with_docker_socket(scan, dag)
        scan = scan.with_exec(
            [
                "trivy",
                "image",
                "--scanners",
                "vuln",
                "--severity",
                "HIGH,CRITICAL",
                "--ignore-unfixed",
                "--exit-code",
                "1",
                "--no-progress",
                image,
            ]
        )
        await scan.sync()
    else:  # pragma: no cover - guarded before Dagger starts
        raise ValueError(f"unsupported Go phase: {phase}")
    return {"phase": phase}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in PHASES:
        raise SystemExit(f"usage: {sys.argv[0]} <{'|'.join(sorted(PHASES))}>")
    selected = sys.argv[1]
    stage(f"go-{selected}", lambda dag: run(dag, selected))
