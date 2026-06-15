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
- `IMAGE_REGISTRY`: overrides `CI_REGISTRY_IMAGE`.
- `KUBE_CONFIG_B64`: base64-encoded kubeconfig for the manual deploy job.
- `DEPLOY_ENVIRONMENT`: GitLab environment name, default `production`.
- `RABBITMQ_DEFAULT_PASS` or `RABBITMQ_PASSWORD`: when set, the deploy job
  creates or updates the production `rabbitmq` Secret before applying manifests.
- `RABBITMQ_DEFAULT_USER` or `RABBITMQ_USERNAME`: optional RabbitMQ user for the
  deploy-managed `rabbitmq` Secret, default `eve_trade`.
- `RABBITMQ_URL`: optional AMQP URL for the deploy-managed `rabbitmq` Secret. If
  omitted, the deploy job builds an in-cluster URL for the `rabbitmq` Service.
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
  rendering, and secret/filesystem scanning.
- `test` runs Go, Rust, and Python contract tests.
- `integration` starts PostgreSQL, RabbitMQ, the settlement worker, and the
  public services inside Dagger and runs the Python e2e suite through the AMQP
  settlement path.
- `publish` publishes service images to the GitLab registry.
- `deploy` optionally applies the RabbitMQ Secret from CI variables, applies the
  rendered kustomize tree after image tags are injected, and waits for RabbitMQ,
  the settlement worker, and public service deployments to roll out.
- `chaos` applies stopped Litmus engines, activates the selected suite, waits for
  all `ChaosResult` verdicts to pass, stops engines on failure or timeout, and
  verifies deployments recover.
