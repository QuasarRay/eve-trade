# Changes V2

Date: 2026-06-23

## Infrastructure

- Added `distributed-backend/terraform/gke` as a GCP/GKE Terraform root parallel
  to the existing AWS/EKS root.
- Added reusable GCP Terraform modules for VPC/subnet/private-services access,
  private-node GKE, Artifact Registry image outputs, cert-manager installation,
  optional OpenTelemetry Operator installation, and optional Cloud SQL
  PostgreSQL.
- Removed the unused `kubectl` provider mapping from the EKS blueprints add-ons
  module call so provider initialization validates cleanly.
- Kept the Kubernetes application manifests provider-neutral. The same
  production overlay is used after either `terraform/eks` or `terraform/gke`
  prepares the cloud runtime, registry, and database secret.

## CI/CD

- Added cloud provider selection through `--cloud-provider`,
  `EVE_TRADE_CLOUD_PROVIDER`, or `CLOUD_PROVIDER`.
- Made image registry defaults and publish credentials provider-aware for AWS
  ECR or GCP Artifact Registry while preserving explicit `--registry` support.
- Added Terraform formatting and validation for both AWS/EKS and GCP/GKE roots
  in the Dagger `check` path and GitHub verify workflow.
- Updated GitLab deploy-side jobs to pass the selected cloud provider.

## Documentation

- Updated the README and ISO architecture description to describe the current
  two-provider infrastructure design: AWS/EKS with RDS/ECR or GCP/GKE with
  Cloud SQL/Artifact Registry.
- Added ISO evidence and fact entries for the provider-specific Terraform roots.
