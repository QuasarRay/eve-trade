"""Isolated Terraform verification for distributed-backend/terraform/talos-omni."""
from _common import stage
from _terraform_root import verify

async def run(dag):
    return await verify(dag, 'distributed-backend/terraform/talos-omni')

if __name__ == "__main__":
    stage('terraform-talos-omni', run)
