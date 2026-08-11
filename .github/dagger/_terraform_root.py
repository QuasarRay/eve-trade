"""Reusable Terraform provider-root verifier."""
from __future__ import annotations
from _common import sh, with_source
from _images import TERRAFORM_IMAGE

async def verify(dag, root: str) -> dict[str, str]:
    ctr = dag.container().from_(TERRAFORM_IMAGE).with_entrypoint([])
    ctr = ctr.with_exec(["sh", "-ec", "apk add --no-cache bash git ca-certificates"])
    ctr = with_source(ctr, dag, include_git=True)
    ctr = sh(ctr, [
        "terraform version",
        "terraform fmt -check -recursive distributed-backend/terraform",
        f"terraform -chdir={root} init -backend=false -lockfile=readonly -no-color",
        f"terraform -chdir={root} providers lock -platform=linux_amd64 -platform=windows_amd64",
        f"git diff --exit-code -- {root}/.terraform.lock.hcl",
        f"terraform -chdir={root} providers",
        f"terraform -chdir={root} validate -no-color",
        f"terraform -chdir={root} test -no-color",
    ])
    await ctr.sync()
    return {"terraform_root": root}
