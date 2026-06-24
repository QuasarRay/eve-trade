# Changes V4

Date: 2026-06-24

## GitHub Actions Fix

- Investigated failed GitHub Actions run `28066122786` for the `verify` workflow on `main`.
- Confirmed every job passed except `e2e / compose`, which failed in the `Run live Python e2e tests` step.
- Identified a compose startup race in the RabbitMQ settlement path: `settlement-worker` could report `/readyz` as healthy after connecting to RabbitMQ, before proving the Rust `trade-settlement` service was reachable.
- Updated `settlement-worker` readiness so it waits for a downstream settlement-service probe before it marks itself ready.
- Added a `Ping` method to the settlement-worker Connect client. The probe sends an intentionally empty settlement batch and treats the expected `invalid_argument` response as success, because that proves the gRPC endpoint is live without writing business data.
- Added focused unit tests for the readiness wait helper, including executors without a ping method, retry-until-ready behavior, and timeout error reporting.

## Validation Notes

- `go test ./...` passed in `distributed-backend/src/messaging`.
- `go test ./...` passed in `distributed-backend/src/settlement-worker`.
- `go test ./...` passed at the repository root.
- `go test ./...` passed in `distributed-backend/src/market`.
- `go test ./...` passed in `distributed-backend/src/api-gateway`.
- `go test ./...` passed in `distributed-backend/proto`.
- `docker compose -f docker-compose.integration.yml --profile test config --quiet` passed.
- Full local Docker e2e verification could not complete because the local Docker build stalled on `deb.debian.org` package downloads while building runtime image layers.
