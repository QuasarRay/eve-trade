"""Protobuf/Buf verification stage."""
from __future__ import annotations

import os
from _common import sh, stage, with_env, with_source
from _images import GOLANG_IMAGE


async def run(dag) -> None:
    ctr = dag.container().from_(GOLANG_IMAGE)
    ctr = sh(
        ctr,
        [
            "apt-get update",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git ca-certificates",
            "rm -rf /var/lib/apt/lists/*",
            "go install github.com/bufbuild/buf/cmd/buf@v1.70.0",
            "go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.11",
        ],
    )
    ctr = with_source(ctr, dag, include_git=True)
    ctr = with_env(ctr)
    commands = [
        "buf build --error-format github-actions",
        "buf lint --error-format github-actions",
        "buf format --diff --exit-code",
    ]
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        commands.append("buf breaking --against '.git#branch=main'")
    commands.extend(["buf generate", "git diff --exit-code -- proto/gen"])
    await sh(ctr, commands).sync()


if __name__ == "__main__":
    stage("proto", run)
