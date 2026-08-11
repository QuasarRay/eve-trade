"""Isolated Terraform verification for distributed-backend/terraform/gke."""
from _common import stage
from _terraform_root import verify

async def run(dag):
    return await verify(dag, 'distributed-backend/terraform/gke')

if __name__ == "__main__":
    stage('terraform-gke', run)
