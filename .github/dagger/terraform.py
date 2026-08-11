"""Compatibility launcher: run all Terraform roots and report every failure.

The GitHub workflow uses separate provider jobs for stronger failure isolation. This
launcher remains useful for local all-provider execution.
"""
from __future__ import annotations
from _common import stage
from _terraform_root import verify

ROOTS = (
    "distributed-backend/terraform/eks",
    "distributed-backend/terraform/gke",
    "distributed-backend/terraform/talos-omni",
)

async def run(dag):
    import anyio
    failures = {}
    async def guarded(root):
        try:
            await verify(dag, root)
        except Exception as exc:
            failures[root] = f"{type(exc).__name__}: {exc}"
    async with anyio.create_task_group() as tg:
        for root in ROOTS:
            tg.start_soon(guarded, root)
    if failures:
        raise RuntimeError("Terraform failures: " + repr(failures))
    return {"terraform_roots": list(ROOTS)}

if __name__ == "__main__":
    stage("terraform", run)
