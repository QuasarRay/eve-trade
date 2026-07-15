# CI/CD

The pipeline keeps GitHub Actions, GitLab CI, and the Python Dagger entrypoint, but the Go backend is now built as one Encore application.

Key commands:

* `encore test ./...`
* `encore build docker --config infra/encore/self-host.nsq.json <image>`
* `cargo test --locked --all-features` in `distributed-backend/src/trade-settlement`
* `kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod`
* `terraform -chdir=<root> validate`

The deployable Go image is `encore-backend`. Remaining non-Go images are `trade-settlement`, `quilkin`, and the simulator image used by browser/e2e tooling.
