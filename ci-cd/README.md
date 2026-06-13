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

## GitLab Variables

GitLab provides `CI_REGISTRY`, `CI_REGISTRY_IMAGE`, `CI_REGISTRY_USER`,
`CI_REGISTRY_PASSWORD`, commit SHA, branch, and tag variables automatically when
the container registry is enabled.

Optional variables:

- `DAGGER_CLOUD_TOKEN`: enables Dagger Cloud traces.
- `IMAGE_REGISTRY`: overrides `CI_REGISTRY_IMAGE`.
- `KUBE_CONFIG_B64`: base64-encoded kubeconfig for the manual deploy job.
- `DEPLOY_ENVIRONMENT`: GitLab environment name, default `production`.

## Pipeline Gates

- `check` validates protobuf contracts, generated proto drift, Kubernetes
  rendering, and secret/filesystem scanning.
- `test` runs Go, Rust, and Python contract tests.
- `integration` starts PostgreSQL plus all three services inside Dagger and runs
  the Python e2e suite.
- `publish` publishes service images to the GitLab registry.
- `deploy` applies the rendered kustomize tree after image tags are injected.
