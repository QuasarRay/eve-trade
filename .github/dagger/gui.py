"""GUI contract stage."""
from __future__ import annotations

from _common import sh, stage, with_source
from _images import NODE_IMAGE


async def run(dag) -> None:
    ctr = dag.container().from_(NODE_IMAGE)
    ctr = sh(
        ctr,
        [
            "corepack enable",
            "corepack prepare pnpm@11.7.0 --activate",
        ],
    )
    ctr = with_source(ctr, dag)
    ctr = sh(ctr, ["pnpm install --frozen-lockfile", "pnpm run gui:test"])
    await ctr.sync()


if __name__ == "__main__":
    stage("gui", run)
