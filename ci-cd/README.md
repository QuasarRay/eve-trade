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

The deployment target is selected with `--deployment-target aws`,
`--deployment-target gcp`, or `--deployment-target talos-omni`, or by setting
`EVE_TRADE_CLOUD_PROVIDER`. The default is `aws`. The selected target controls
provider-specific registry defaults and publish credentials; the rendered
Kubernetes manifests remain portable across EKS, GKE, and Omni-managed Talos
clusters.

Validate Terraform locally with:

```powershell
python ci-cd\pipeline.py terraform --all-targets
python ci-cd\pipeline.py terraform --deployment-target gcp
python ci-cd\pipeline.py terraform --deployment-target talos-omni
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
- `EVE_TRADE_CLOUD_PROVIDER`: deployment target, either `aws`, `gcp`, or
  `talos-omni`; default `aws`. Aliases `eks`, `gke`, `talos`, `omni`, and
  `talos_omni` are accepted by the Python pipeline.
- `IMAGE_REGISTRY`: overrides `CI_REGISTRY_IMAGE`.
- `AWS_ECR_IMAGE_REGISTRY` or `ECR_IMAGE_REGISTRY`: AWS/ECR image registry
  prefix used when `EVE_TRADE_CLOUD_PROVIDER=aws`.
- `GCP_ARTIFACT_REGISTRY_IMAGE` or `GAR_IMAGE_REGISTRY`: GCP Artifact Registry
  image registry prefix used when `EVE_TRADE_CLOUD_PROVIDER=gcp`.
- `TALOS_OMNI_IMAGE_REGISTRY` or `OMNI_IMAGE_REGISTRY`: provider-neutral image
  registry prefix used when `EVE_TRADE_CLOUD_PROVIDER=talos-omni`.
- `REGISTRY_USER` and `REGISTRY_PASSWORD`: provider-neutral registry
  credentials. Provider-specific `AWS_ECR_*`, `ECR_*`, `GCP_ARTIFACT_*`,
  `GAR_*`, `TALOS_OMNI_*`, or `OMNI_*` credential variables override them when
  present.
- `KUBE_CONFIG_B64`: base64-encoded kubeconfig for the manual deploy job.
- `POST_DEPLOY_SMOKE_URL` and `POST_DEPLOY_SMOKE_BEARER_TOKEN`: required
  external authenticated trade probe endpoint and token for production deploy
  verification.
- `DEPLOY_ENVIRONMENT`: GitLab environment name, default `production`.
- `CHAOS_NAMESPACE`: target namespace for production chaos, default `eve-trade`.
- `CHAOS_SELECTOR`: label selector for Litmus `ChaosEngine` resources, default
  `chaos.eve-trade.io/suite=pod-resilience`.
- `CHAOS_TIMEOUT_SECONDS`: maximum wait for Litmus `ChaosResult` verdicts,
  default `900`.
- `CHAOS_PROBE_URL` and `CHAOS_PROBE_BEARER_TOKEN`: required external
  authenticated trade probe endpoint and token used continuously around the
  chaos window.
- `CHAOS_CLEANUP`: set to `true` to delete selected `ChaosEngine` and
  `ChaosResult` resources after a successful run.
- `RUN_PRODUCTION_CHAOS`: set to `true` on scheduled default-branch pipelines to
  run the production chaos job automatically.

## Pipeline Gates

- `check` validates protobuf contracts, generated proto drift, Kubernetes
  rendering, all Terraform roots, and secret/filesystem scanning.
- `test` runs Go formatting/module drift, coverage, unit/live-RabbitMQ, vet,
  race, fuzz, static analysis and vulnerability checks; Rust format/build,
  clippy, unit/property tests, coverage and audit; and executable Django and
  observability Python suites with coverage and dependency audits. It does not
  count test collection as execution.
- `integration` starts PostgreSQL, RabbitMQ, trade-settlement,
  settlement-worker, Market, API Gateway, Quilkin, and the simulator inside
  Dagger, then runs the Python e2e suite through the authenticated canonical
  UDP and message-driven settlement path. Production-gate mode fails on any
  skip and repeats concurrency/load-sensitive scenarios three times.
- `terraform` validates one selected Terraform root or all deployment roots.
- `publish` publishes service images to the selected provider registry or the
  explicit `--registry` value and records immutable digest references in
  `ci-cd/out/image-digests.json`.
- `deploy` accepts digest references only, applies the rendered kustomize tree,
  waits for rollout, and requires an authenticated post-deploy trade smoke.
  Smoke failure triggers `kubectl rollout undo` for every application
  deployment and makes the job fail.
- `chaos` requires an authenticated external trade probe before, continuously
  during, and after disruption; applies Litmus engines with functional probes;
  requires all `ChaosResult` verdicts to pass; verifies actual recovery; and
  fails when service continuity is lost.
