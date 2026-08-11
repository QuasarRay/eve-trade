"""Isolated Terraform verification for distributed-backend/terraform/eks."""
from _common import stage
from _terraform_root import verify

async def run(dag):
    return await verify(dag, 'distributed-backend/terraform/eks')

if __name__ == "__main__":
    stage('terraform-eks', run)
