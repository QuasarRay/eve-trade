# Changes V3

Date: 2026-06-24

## Changes In Code And Infrastructure

- Added `distributed-backend/terraform/talos-omni` as a third deployment root alongside `eks` and `gke`.
- The Talos/Omni root prepares an Omni-managed Talos Kubernetes cluster instead of provisioning cloud infrastructure. It creates or prepares Eve Trade runtime prerequisites with `kubectl_manifest` resources.
- Added Talos/Omni runtime support for:
  - Eve Trade namespace labels.
  - `trade-settlement-database` secret from an external PostgreSQL URL.
  - Optional non-production in-cluster PostgreSQL StatefulSet for smoke tests.
  - Provider-neutral service image outputs.
  - Optional OpenTelemetry `Instrumentation` object for clusters that already have the OpenTelemetry Operator CRDs installed.
- Updated CI/CD provider selection so `EVE_TRADE_CLOUD_PROVIDER`, `--cloud-provider`, and `--deployment-target` accept `aws`, `gcp`, or `talos-omni`, with aliases for `eks`, `gke`, `talos`, `omni`, and `talos_omni`.
- Added Talos/Omni Terraform validation to the GitHub Actions Terraform matrix.
- Added provider-neutral registry handling for Talos/Omni through `TALOS_OMNI_IMAGE_REGISTRY`, `OMNI_IMAGE_REGISTRY`, `TALOS_OMNI_REGISTRY_USER`, `OMNI_REGISTRY_USER`, `TALOS_OMNI_REGISTRY_PASSWORD`, and `OMNI_REGISTRY_PASSWORD`.
- Added `--all-targets` as a clearer alias for the existing Terraform validation flag while keeping `--all-clouds` compatible.
- Updated `README.md` and `ci-cd/README.md` so operators can choose AWS/EKS, GCP/GKE, or Omni-managed Talos while deploying the same Kubernetes application manifests.

## Changes In ISO Architecture Docs

- Updated the architecture description to state that production-like deployment now has three supported Terraform roots: AWS/EKS, GCP/GKE, and Talos/Omni.
- Updated context and deployment views so infrastructure is described as target-specific deployment infrastructure rather than AWS/GCP-only cloud infrastructure.
- Updated deployment operations to document Talos/Omni database egress assumptions and PostgreSQL options.
- Updated development and validation records so Terraform validation scope includes EKS, GKE, and Talos/Omni.
- Updated stakeholder concern conflict records to describe portability across platform-specific controls rather than only cloud-specific controls.
- Updated evidence and facts records so `EVID-016` and `FACT-090` include `distributed-backend/terraform/talos-omni`.

## Validation Notes

- `terraform fmt -check -recursive distributed-backend/terraform` passed with Terraform v1.10.5 downloaded to a temp directory.
- `python -m py_compile ci-cd/pipeline.py` passed.
- `python ci-cd/pipeline.py terraform --help` passed in a temporary Python environment and showed `--cloud-provider`, `--deployment-target`, `aws`, `gcp`, `talos-omni`, and alias choices.
- `kubectl kustomize` passed for production, gateway, istio, and observability manifests.
- Local `terraform init -backend=false` and `terraform validate` for the final Talos/Omni root could not be completed because provider initialization was blocked by registry/provider download errors.
- Full GitHub Actions, GitLab, Dagger, and live Talos/Omni cluster applies were not run locally.
