# Eve Trade CI/CD

The GitLab pipeline is driven by a Python Dagger program. GitLab only handles
job scheduling and credentials; build, test, image, integration, and deployment
logic lives in `ci-cd/pipeline.py`.

## Local Usage

Install the Dagger CLI and Python dependencies, then run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r ci-cd\requirements.txt
python ci-cd\pipeline.py check
python ci-cd\pipeline.py test
python ci-cd\pipeline.py build
```

Run the full live stack test with:

```powershell
python ci-cd\pipeline.py integration
```

Render Kubernetes manifests with release image tags:

```powershell
python ci-cd\pipeline.py render-kubernetes --registry registry.example.com/eve-trade --tag sha-1234 --output ci-cd\out\kubernetes.yaml
```

The deployment cloud is selected with `--cloud-provider aws` or
`--cloud-provider gcp`, or by setting `EVE_TRADE_CLOUD_PROVIDER`. The default is
`aws`. The selected provider controls provider-specific registry defaults and
publish credentials; the rendered Kubernetes manifests remain cloud-agnostic.

Validate Terraform locally with:

```powershell
python ci-cd\pipeline.py terraform --all-clouds
python ci-cd\pipeline.py terraform --cloud-provider gcp
```

The renderer targets
`distributed-backend/orchestration/kubernetes/overlay/prod`, not the base
kustomization.

Render the production Litmus chaos suite with:

```powershell
python ci-cd\pipeline.py render-chaos --output ci-cd\out\chaos-litmus.yaml
```

Run the Litmus suite against a cluster with:

```powershell
python ci-cd\pipeline.py chaos --namespace eve-trade --selector "chaos.eve-trade.io/suite=pod-resilience" --timeout-seconds 900
```

Chaos runs require the Litmus Chaos Operator CRDs, the `pod-delete`
`ChaosExperiment` in the target namespace, and `KUBE_CONFIG_B64`.

## GitLab Variables

GitLab provides `CI_REGISTRY`, `CI_REGISTRY_IMAGE`, `CI_REGISTRY_USER`,
`CI_REGISTRY_PASSWORD`, commit SHA, branch, and tag variables automatically when
the container registry is enabled.

Optional variables:

- `DAGGER_CLOUD_TOKEN`: enables Dagger Cloud traces.
- `EVE_TRADE_CLOUD_PROVIDER`: deployment provider, either `aws` or `gcp`;
  default `aws`.
- `IMAGE_REGISTRY`: overrides `CI_REGISTRY_IMAGE`.
- `AWS_ECR_IMAGE_REGISTRY` or `ECR_IMAGE_REGISTRY`: AWS/ECR image registry
  prefix used when `EVE_TRADE_CLOUD_PROVIDER=aws`.
- `GCP_ARTIFACT_REGISTRY_IMAGE` or `GAR_IMAGE_REGISTRY`: GCP Artifact Registry
  image registry prefix used when `EVE_TRADE_CLOUD_PROVIDER=gcp`.
- `REGISTRY_USER` and `REGISTRY_PASSWORD`: provider-neutral registry
  credentials. Provider-specific `AWS_ECR_*`, `ECR_*`, `GCP_ARTIFACT_*`, or
  `GAR_*` credential variables override them when present.
- `KUBE_CONFIG_B64`: base64-encoded kubeconfig for the manual deploy job.
- `DEPLOY_ENVIRONMENT`: GitLab environment name, default `production`.
- `CHAOS_NAMESPACE`: target namespace for production chaos, default `eve-trade`.
- `CHAOS_SELECTOR`: label selector for Litmus `ChaosEngine` resources, default
  `chaos.eve-trade.io/suite=pod-resilience`.
- `CHAOS_TIMEOUT_SECONDS`: maximum wait for Litmus `ChaosResult` verdicts,
  default `900`.
- `CHAOS_CLEANUP`: set to `true` to delete selected `ChaosEngine` and
  `ChaosResult` resources after a successful run.
- `RUN_PRODUCTION_CHAOS`: set to `true` on scheduled default-branch pipelines to
  run the production chaos job automatically.

## Pipeline Gates

- `check` validates protobuf contracts, generated proto drift, Kubernetes
  rendering, both Terraform roots, and secret/filesystem scanning.
- `test` runs Go, Rust, and Python contract tests.
- `integration` starts PostgreSQL, RabbitMQ, trade-settlement,
  settlement-worker, Market, and API Gateway inside Dagger and runs the Python
  e2e suite through the message-driven settlement path.
- `terraform` validates one selected Terraform root or all cloud roots.
- `publish` publishes service images to the selected provider registry or the
  explicit `--registry` value.
- `deploy` applies the rendered kustomize tree after image tags are injected and
  waits for the public service deployments to roll out.
- `chaos` applies stopped Litmus engines, activates the selected suite, waits for
  all `ChaosResult` verdicts to pass, stops engines on failure or timeout, and
  verifies deployments recover.
