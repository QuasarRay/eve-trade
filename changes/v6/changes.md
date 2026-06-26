# v6 architecture cleanup and hardening

## Starting point

- Current HEAD before work: `13baa27824010bd1fc3b4d17409a0dfe086d425c`
- Branch inspected before work: `main`.
- Working tree inspected before work: clean.

## Architectural problem

The repository had drifted away from the intended production boundary. API Gateway and Market exposed public command-shaped trade RPCs for issue, accept, and cancel flows, while the simulator and e2e path could bypass the production-shaped UDP GUI packet boundary. That made the gateway capable of looking like a business service and let test/framework identity leak into packets that should be observationally equivalent to real game frontend traffic.

The intended production path after this change is:

```text
game frontend -> Quilkin UDP -> API gateway UDP edge -> Market GUI interaction -> settlement operations -> trade-settlement
```

Before changing code, the gateway-to-market-to-settlement path was identified in:

- `distributed-backend/proto/eve/api_gateway/v1/api_gateway.proto`
- `distributed-backend/proto/eve/market/v1/market.proto`
- `distributed-backend/proto/eve/trade_settlement/v1/trade_settlement.proto`
- `distributed-backend/src/api-gateway/distributed-backend/handler.go`
- `distributed-backend/src/api-gateway/distributed-backend/market_client.go`
- `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp.go`
- `distributed-backend/src/api-gateway/distributed-backend/server.go`
- `distributed-backend/src/market/distributed-backend/handler.go`
- `distributed-backend/src/market/distributed-backend/settlement_client.go`
- `distributed-backend/src/market/game-trade/*`
- `distributed-backend/src/trade-settlement/src/*`
- `simulator/trade_gui/views.py`
- `simulator/trade_gui/udp_client.py`
- `distributed-backend/tests/e2e/*`
- `compose.yaml`
- `docker-compose.integration.yml`
- `distributed-backend/orchestration/kubernetes/**`
- `Architecture/ISO-42010/*.md`

## Files changed

Changed or added:

- `.github/workflows/verify.yaml`
- `README.md`
- `changes/v6/changes.md`
- `scripts/verify_architecture_boundaries.py`
- `compose.yaml`
- `docker-compose.integration.yml`
- `distributed-backend/proto/eve/market/v1/market.proto`
- `distributed-backend/proto/gen/eve/market/v1/market.pb.go`
- `distributed-backend/proto/gen/eve/market/v1/marketv1connect/market.connect.go`
- `distributed-backend/src/api-gateway/cmd/api-gateway/main.go`
- `distributed-backend/src/api-gateway/distributed-backend/config.go`
- `distributed-backend/src/api-gateway/distributed-backend/market_client.go`
- `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp.go`
- `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp_test.go`
- `distributed-backend/src/api-gateway/distributed-backend/server.go`
- `distributed-backend/src/api-gateway/go.mod`
- `distributed-backend/src/market/cmd/market/main.go`
- `distributed-backend/src/market/distributed-backend/config.go`
- `distributed-backend/src/market/distributed-backend/handler.go`
- `distributed-backend/src/market/distributed-backend/handler_test.go`
- `distributed-backend/src/market/distributed-backend/server.go`
- `distributed-backend/src/trade-settlement/Cargo.lock`
- `distributed-backend/tests/e2e/conftest.py`
- `distributed-backend/tests/e2e/helpers.py`
- `distributed-backend/tests/e2e/test_trade_lifecycle.py`
- `distributed-backend/orchestration/kubernetes/base/api-gateway.yaml`
- `distributed-backend/orchestration/kubernetes/base/configmaps.yaml`
- `distributed-backend/orchestration/kubernetes/base/rabbitmq.yaml`
- `distributed-backend/orchestration/kubernetes/overlay/local/secrets.yaml`
- `distributed-backend/orchestration/kubernetes/overlay/local/simulator.yaml`
- `distributed-backend/orchestration/kubernetes/overlay/prod/README.md`
- `distributed-backend/orchestration/kubernetes/overlay/prod/istio-security.yaml`
- `distributed-backend/orchestration/kubernetes/overlay/prod/kustomization.yaml`
- `distributed-backend/orchestration/kubernetes/overlay/prod/networkpolicies.yaml`
- `distributed-backend/orchestration/kubernetes/overlay/prod/quilkin.yaml`
- `simulator/eve_trade_simulator/settings.py`
- `simulator/trade_gui/management/commands/seed_trade_gui.py`
- `simulator/trade_gui/tests.py`
- `simulator/trade_gui/udp_client.py`
- `simulator/trade_gui/views.py`
- `Architecture/ISO-42010/00-architecture-description.md`
- `Architecture/ISO-42010/01-stakeholders-concerns-perspectives.md`
- `Architecture/ISO-42010/02-viewpoints.md`
- `Architecture/ISO-42010/03-context-view.md`
- `Architecture/ISO-42010/04-functional-runtime-view.md`
- `Architecture/ISO-42010/05-information-data-integrity-view.md`
- `Architecture/ISO-42010/06-deployment-operations-view.md`
- `Architecture/ISO-42010/07-security-trust-view.md`
- `Architecture/ISO-42010/08-development-validation-view.md`
- `Architecture/ISO-42010/09-correspondences-rationale.md`
- `Architecture/ISO-42010/12-resilience-recovery-view.md`
- `Architecture/ISO-42010/14-threat-model-view.md`
- `Architecture/ISO-42010/15-risk-register.md`
- `Architecture/ISO-42010/16-glossary.md`
- `Architecture/ISO-42010/18-evidence-manifest.md`
- `Architecture/ISO-42010/19-architecture-facts.md`

Deleted:

- `distributed-backend/proto/eve/api_gateway/v1/api_gateway.proto`
- `distributed-backend/proto/gen/eve/api_gateway/v1/api_gateway.pb.go`
- `distributed-backend/proto/gen/eve/api_gateway/v1/api_gatewayv1connect/api_gateway.connect.go`
- `distributed-backend/src/api-gateway/distributed-backend/handler.go`
- `distributed-backend/src/api-gateway/distributed-backend/handler_test.go`
- `distributed-backend/orchestration/kubernetes/overlay/prod/httproute.yaml`

## Protos removed or changed

- Deleted the API Gateway production proto package and generated Go package. API Gateway no longer exposes `IssueTradeInstance`, `AcceptTradeInstance`, or `CancelTradeInstance`.
- Removed Market public `IssueTradeInstance`, `AcceptTradeInstance`, and `CancelTradeInstance` RPCs and generated code.
- Kept one Market production RPC: `SubmitTradeGuiInteraction`.
- Made `SubmitTradeGuiInteractionRequest` business-boundary clean:

```proto
message SubmitTradeGuiInteractionRequest {
  bytes raw_payload = 1;
}
```

- Trade-settlement still exposes only `ExecuteSettlementBatch` with low-level settlement operations plus idempotency/audit metadata. It does not expose game trade issue/accept/cancel RPCs.

## Simulator packet changes

- `simulator/trade_gui/views.py` now builds only production-shaped game GUI payloads:

```json
{
  "schema_version": "eve-trade-gui.v1",
  "interaction_id": "<unique client interaction id>",
  "ui": {
    "window": "<game ui window>",
    "control_id": "<stable game action>",
    "action": "<stable game action>"
  },
  "input": {
    "...": "player-provided game trade inputs only"
  }
}
```

- Removed simulator/test/framework/source metadata from the outbound game packet, including old `source` and simulator-only timestamp metadata.
- `idempotency_key` and `external_request_id` can still exist in the local simulator HTTP/UI boundary for developer convenience, but they are stripped from the outbound game packet `input`; the outbound `interaction_id` is the game packet identity.
- `simulator/trade_gui/udp_client.py` wraps the game payload in a signed edge envelope with schema `eve-trade-edge.v1` and HMAC-SHA256 authentication. The envelope does not include Django/browser/simulator identity.
- `simulator/trade_gui/tests.py` submits through the real Django view and inspects the actual UDP payload emitted by simulator code. It fails on forbidden terms: `django`, `rest`, `framework`, `simulator`, `test`, `debug`, `environment`, `browser`, `source`, `source_transport`, and `source_address`.

## UDP gateway resilience changes

- Replaced unbounded goroutine-per-packet handling with a bounded worker pool and bounded queue in `quilkin_udp.go`.
- Added environment-backed configuration for worker count, queue depth, max packet size, downstream timeout, per-remote rate limit, replay TTL, HMAC requirement, HMAC secret, and HMAC key ID.
- Added max packet size enforcement and empty packet rejection.
- Added signed edge envelope validation with HMAC-SHA256.
- Added safe protocol-level extraction of `interaction_id` without interpreting game trade mechanics.
- Added process-local replay rejection by `interaction_id` before forwarding to Market.
- Added per-remote token-bucket rate limiting.
- Added downstream Market call timeout and context cancellation.
- Added structured logs for received packets, rejected packets, queue full, rate limited, downstream failure, and downstream success.
- Added OTel counters/histograms for UDP packet outcomes, packet size, and downstream latency.
- Added compact JSON UDP responses with stable error codes such as `packet_too_large`, `missing_signature`, `invalid_signature`, `rate_limited`, `queue_full`, `replay`, and `downstream_timeout`.
- API Gateway now forwards only `RawPayload` to Market and never sends `source_transport` or `source_address`.

## Quilkin and API Gateway hardening

- `docker-compose.integration.yml` now runs the live integration path through `simulator -> Quilkin UDP -> API Gateway UDP -> Market -> trade-settlement -> PostgreSQL`.
- `compose.yaml` and `docker-compose.integration.yml` configure shared local HMAC settings for simulator and API Gateway.
- Kubernetes base config now exposes API Gateway UDP hardening settings.
- Kubernetes local overlay includes a clearly local-only HMAC secret for simulator/API Gateway.
- Kubernetes production overlay adds a Quilkin UDP Deployment/Service and removes the old API Gateway HTTPRoute that exposed deleted command RPCs.
- Production Istio policy no longer documents or permits API Gateway command RPCs; Market policy allows `SubmitTradeGuiInteraction`.
- Production NetworkPolicy allows UDP from Quilkin to API Gateway and removes simulator assumptions from prod.
- Local and production overlays render separately.
- Docker Compose integration no longer publishes internal dependency ports to the host; the e2e test path uses Docker networking, matching CI and avoiding local host-port collisions.
- The Compose Quilkin command now resolves the API Gateway service endpoint inside the container and execs Quilkin with a concrete UDP endpoint instead of exiting after shell variable setup.
- The Compose Postgres healthcheck now checks TCP readiness, and the migration job retries a TCP `SELECT 1` before resetting and migrating the schema.
- The Compose integration Market and API Gateway services disable the local OTel SDK so e2e health does not depend on a collector during the workflow path.
- Market startup now retries Postgres and settlement transport initialization for a bounded window using `MARKET_STARTUP_DEPENDENCY_TIMEOUT` and `MARKET_STARTUP_RETRY_INTERVAL`, so Kubernetes startup order does not require crash restarts during normal dependency bring-up.
- Market startup retry now keeps the process context for successful long-lived dependencies, avoiding a readiness failure where the RabbitMQ settlement client inherited a canceled startup deadline context.
- RabbitMQ Kubernetes readiness/liveness probes now use a longer timeout, and liveness allows more failures before restart, to avoid false-positive restarts during local cluster load while still detecting real process failure.

## CI/CD gates added

- Buf build, lint, format check, generation, and stale generated code checks.
- Architecture boundary guard: `scripts/verify_architecture_boundaries.py`.
- Guard fails if production protos reintroduce public `IssueTradeInstance`, `AcceptTradeInstance`, or `CancelTradeInstance` RPCs.
- Guard fails if API Gateway sends `source_transport` or `source_address` toward Market.
- Guard fails if simulator packet tests stop asserting the forbidden identity terms.
- Guard fails if docs document the removed public RPC production path or omit the canonical path.
- Go module checks: `go mod tidy` cleanliness, gofmt, `go test ./...`, `go vet ./...`, `go test -race ./...`, staticcheck, govulncheck, and service builds.
- Root Go workspace `go test ./...` and `go vet ./...`.
- Rust checks: `cargo fmt --all -- --check`, `cargo check --locked --all-targets --all-features`, `cargo test --locked --all-features`, `cargo clippy --locked --all-targets --all-features -- -D warnings`, and `cargo audit --ignore RUSTSEC-2023-0071`.
- Python simulator checks: dependency install, `pip check`, compileall, Django packet tests, and `pip-audit`.
- Docker Compose e2e gate builds and runs simulator, Quilkin, API Gateway UDP, Market, trade-settlement, settlement worker, RabbitMQ, PostgreSQL, migrations, and Python e2e tests.
- Kubernetes gate renders platform, local, production, and observability overlays and validates with kubeconform.
- Terraform gate runs `terraform fmt -check -recursive`, `terraform init -backend=false`, and `terraform validate` for AWS, GCP, and Talos Omni roots.

Buf breaking checks were not added because this change intentionally deletes public RPC contracts and the repository does not currently define a stable pre-v6 Buf breaking baseline. The generated-code freshness gate and boundary guard are enforced instead.

`RUSTSEC-2023-0071` is ignored explicitly in CI because `cargo audit` reports `rsa 0.9.10` from `Cargo.lock` through optional `sqlx` MySQL support even though this service is PostgreSQL-only and `cargo tree -i rsa` is empty. The advisory currently has no fixed upgrade.

## ISO docs updated

The ISO/IEC/IEEE 42010-style docs were updated to describe the implemented current state, not the old direct-RPC design and not aspirational future behavior. They now state:

- eve-trade is a production-ready distributed backend/platform slice for an EVE-like trade flow.
- The Django simulator is a local game-frontend simulator, not part of the backend production platform.
- The simulator outbound UDP packet is production-identical to real game frontend traffic.
- API Gateway is a UDP edge and UDP-to-gRPC forwarder only.
- Market owns GUI interaction interpretation and game-trade decisioning.
- Trade-settlement owns atomic execution of low-level settlement operations only.
- PostgreSQL owns durable state.
- Quilkin owns UDP proxy/routing behavior.
- Direct public issue/accept/cancel RPCs were removed.
- Gateway transport metadata remains internal to gateway logs/traces and is not sent to Market.
- Production overlays exclude simulator resources.
- Current limitations are documented instead of being described as completed features.

## Tests added or updated

- `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp_test.go`
  - verifies raw payload forwarding only.
  - verifies missing signature rejection.
  - verifies invalid signature rejection.
  - verifies replay rejection before a second Market call.
  - verifies per-remote rate limiting.
- `simulator/trade_gui/tests.py`
  - submits through the real Django button press path.
  - captures the actual UDP payload.
  - verifies production-shaped GUI packet contents.
  - verifies HMAC signature.
  - fails on forbidden simulator/framework/testing/source identity terms.
- `distributed-backend/src/market/distributed-backend/handler_test.go`
  - updated for private Market helper functions instead of public command RPCs.
  - verifies GUI interaction submission defaults idempotency from `interaction_id`.
  - verifies duplicate GUI interaction replay does not call settlement twice.
  - tightened replay tests so completed replays must carry current Market request fingerprints.
- `distributed-backend/tests/e2e/helpers.py` and `conftest.py`
  - route e2e issue/accept/cancel flows through the simulator GUI HTTP path, which emits UDP to Quilkin, instead of calling old command RPCs.
- `distributed-backend/tests/e2e/test_trade_lifecycle.py`
  - updated replay/idempotency assertions for the edge replay behavior: duplicate `interaction_id` packets are rejected by API Gateway with `code = replay` and do not add settlement batches.
  - verifies retry/replay cases through the simulator -> Quilkin -> API Gateway UDP path.

## Kubernetes local cluster smoke

Docker Desktop Kubernetes was started and the local context was verified:

- `kubectl cluster-info`
- `kubectl -n eve-trade get pods,svc,jobs`

The local overlay was applied with:

- `kubectl apply -k distributed-backend/orchestration/kubernetes/overlay/local`

The migration and seed jobs completed:

- `settlement-db-migrate`: `Complete`
- `local-dev-world-seed`: `Complete`

Runtime pods reached `Running`:

- `api-gateway`
- `market`
- `postgres`
- `quilkin`
- `rabbitmq-0`
- `settlement-worker`
- `simulator`
- `trade-settlement`

Smoke tests run through the Kubernetes path:

- Port-forwarded `svc/simulator` to `127.0.0.1:18000`.
- Submitted `market_place_sell_order` through `/api/gui/buttons/1/press/`.
- Verified the packet traveled through simulator -> Quilkin UDP -> API Gateway UDP -> Market -> settlement -> PostgreSQL and returned `status = accepted`.
- Replayed the same `interaction_id` and verified the response was `code = replay`.
- Queried PostgreSQL and verified only one settlement batch existed for the replayed interaction.
- Submitted `market_buy_from_sell_order` through `/api/gui/buttons/2/press/`.
- Verified seller wallet increased, buyer wallet decreased, buyer item stack increased, and the trade remained open with remaining quantity.
- Rebuilt and rolled out Market after adding startup dependency retry.
- Verified the new Market pod started with `RESTARTS = 0`.
- Submitted another post-rollout sell-order interaction through the same simulator path and verified durable PostgreSQL state.
- Fixed the Market startup retry context lifetime after `/readyz` exposed `rabbitmq settlement client context closed: context canceled`.
- Rebuilt and rolled out Market again; the replacement Market pod became Ready with `RESTARTS = 0`.
- Patched RabbitMQ probe timings, rolled the StatefulSet, and verified the replacement `rabbitmq-0` pod became Ready with `RESTARTS = 0`.
- Ran a final post-RabbitMQ-rollout simulator sell-order smoke with interaction `k8s-rabbit-rollout-smoke-864f3061-cdd6-4790-9027-cc5540aa0d3f`; Market accepted it and PostgreSQL showed `4` trades, `6` settlement batches, seller stack quantity `95`, and trade `82051737-4a92-4737-84ff-7e1a0c0e877a|OPEN|1|1|32`.

## Commands run locally

Passed:

- `buf build --error-format text`
- `buf lint --error-format text`
- `buf format --diff --exit-code`
- `buf generate`
- `go test ./...` from repository root
- `go vet ./...` from repository root
- `go test ./...` in `distributed-backend/proto`
- `go vet ./...` in `distributed-backend/proto`
- `go run honnef.co/go/tools/cmd/staticcheck@latest ./...` in `distributed-backend/proto`
- `go mod tidy` in `distributed-backend/src/api-gateway`
- `go test ./...` in `distributed-backend/src/api-gateway`
- `go vet ./...` in `distributed-backend/src/api-gateway`
- `go run honnef.co/go/tools/cmd/staticcheck@latest ./...` in `distributed-backend/src/api-gateway`
- `go mod tidy` in `distributed-backend/src/market`
- `go test ./...` in `distributed-backend/src/market`
- `go vet ./...` in `distributed-backend/src/market`
- `go run honnef.co/go/tools/cmd/staticcheck@latest ./...` in `distributed-backend/src/market`
- `gofmt -w distributed-backend\src\market\cmd\market\main.go distributed-backend\src\market\distributed-backend\config.go`
- `gofmt -l distributed-backend\src\market\cmd\market\main.go distributed-backend\src\market\distributed-backend\config.go`
- `go test ./...` in `distributed-backend/src/observability`
- `go vet ./...` in `distributed-backend/src/observability`
- `go run honnef.co/go/tools/cmd/staticcheck@latest ./...` in `distributed-backend/src/observability`
- `go test ./...` in `distributed-backend/src/messaging`
- `go vet ./...` in `distributed-backend/src/messaging`
- `go run honnef.co/go/tools/cmd/staticcheck@latest ./...` in `distributed-backend/src/messaging`
- `go test ./...` in `distributed-backend/src/settlement-worker`
- `go vet ./...` in `distributed-backend/src/settlement-worker`
- `go run honnef.co/go/tools/cmd/staticcheck@latest ./...` in `distributed-backend/src/settlement-worker`
- `cargo fmt --all -- --check` in `distributed-backend/src/trade-settlement`
- `cargo check --locked --all-targets --all-features` in `distributed-backend/src/trade-settlement`
- `cargo test --locked --all-features` in `distributed-backend/src/trade-settlement`
- `cargo clippy --locked --all-targets --all-features -- -D warnings` in `distributed-backend/src/trade-settlement`
- `cargo update` in `distributed-backend/src/trade-settlement`
- `cargo tree --locked --target all -i rsa` in `distributed-backend/src/trade-settlement`
- `cargo audit --ignore RUSTSEC-2023-0071` in `distributed-backend/src/trade-settlement`
- `python manage.py test trade_gui` in `simulator`
- `python -m compileall simulator\eve_trade_simulator simulator\trade_gui distributed-backend\tests\e2e`
- `python -m pip check` in `simulator`
- `python -m pip install pip-audit`
- `python -m pip_audit -r requirements.txt` in `simulator`
- `python scripts\verify_architecture_boundaries.py`
- `go run github.com/rhysd/actionlint/cmd/actionlint@latest`
- `docker compose -f docker-compose.integration.yml --profile test config --quiet`
- `docker compose --profile test config --quiet`
- `docker compose -f docker-compose.integration.yml --profile test build`
- `docker compose -f docker-compose.integration.yml --profile test build market`
- `docker compose -f docker-compose.integration.yml --profile test down -v --remove-orphans`
- `docker compose -f docker-compose.integration.yml --profile test up -d postgres rabbitmq`
- `docker compose -f docker-compose.integration.yml --profile test up --exit-code-from migrate migrate`
- `docker compose -f docker-compose.integration.yml --profile test up -d trade-settlement settlement-worker market api-gateway quilkin simulator`
- `docker compose -f docker-compose.integration.yml --profile test run --rm --no-deps e2e-tests`
- Final Docker Compose e2e result: `109 passed in 79.40s`.
- `docker compose -f docker-compose.integration.yml --profile test ps`
- `kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/local`
- `kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod`
- `kubectl kustomize distributed-backend/orchestration/kubernetes/platform/istio/prod`
- `kubectl kustomize distributed-backend/orchestration/kubernetes/platform/gateway/prod`
- `kubectl kustomize distributed-backend/orchestration/kubernetes/base/observability`
- `go run github.com/yannh/kubeconform/cmd/kubeconform@v0.6.7 -strict -ignore-missing-schemas ...`
- `docker tag eve-trade-integration-api-gateway:latest eve-trade/api-gateway:dev`
- `docker tag eve-trade-integration-market:latest eve-trade/market:dev`
- `docker tag eve-trade-integration-settlement-worker:latest eve-trade/settlement-worker:dev`
- `docker tag eve-trade-integration-trade-settlement:latest eve-trade/trade-settlement:dev`
- `docker tag eve-trade-integration-simulator:latest eve-trade/simulator:dev`
- `kubectl apply -k distributed-backend/orchestration/kubernetes/overlay/local`
- `kubectl -n eve-trade rollout restart deployment/market`
- `kubectl -n eve-trade rollout status deployment/market --timeout=180s`
- `kubectl -n eve-trade rollout restart statefulset/rabbitmq`
- `kubectl -n eve-trade rollout status statefulset/rabbitmq --timeout=240s`
- `kubectl -n eve-trade get pods,jobs -o wide`
- `git diff --check`
- `rg -n "[ \t]+$" changes\v6 scripts\verify_architecture_boundaries.py simulator\trade_gui\tests.py distributed-backend\src\api-gateway\distributed-backend\quilkin_udp_test.go distributed-backend\orchestration\kubernetes\overlay\prod\quilkin.yaml`

## Commands not run locally or blocked

- `go test -race ./...` in service modules was blocked on this Windows host because race builds require cgo and no GCC C compiler is installed. CI runs the race detector on Ubuntu.
- `go run golang.org/x/vuln/cmd/govulncheck@latest ./...` was attempted locally and failed because `https://vuln.go.dev/index/modules.json.gz` returned `403 Forbidden`. CI still installs and runs govulncheck.
- `terraform fmt` and `terraform validate` were not completed locally. Terraform is not installed on this host, and downloading Terraform 1.10.5 from HashiCorp releases was blocked with "This content is not currently available in your region. Please see trade controls." CI installs Terraform with `hashicorp/setup-terraform`.
- Remote GitHub Actions run inspection was not completed because `gh` is not installed on this Windows host. Workflow syntax and workflow-equivalent local gates were validated with `actionlint`, Compose config rendering, Kubernetes rendering/validation, and the full Compose e2e path.

## Remaining limitations and risks

- API Gateway HMAC authenticates packet integrity for the edge path, but it does not yet bind packets to a player account/session identity.
- API Gateway replay protection is process-local. Multi-replica production deployments should use a shared replay/nonce store or rely on Market/settlement durable idempotency for cross-replica duplicate suppression.
- The HMAC key shown in local compose and local Kubernetes overlay is intentionally local-only. Production must provide `api-gateway-edge-auth` from a real secret manager or cluster secret process.
- Buf breaking checks are not configured because v6 intentionally removes old public RPCs and no pre-v6 breaking baseline is defined.
- Terraform, govulncheck, and Go race detector coverage depend on CI for this host because of the local tool/network limitations listed above.
- `cargo audit` requires an explicit `RUSTSEC-2023-0071` ignore until upstream `sqlx`/`rsa` dependencies provide a fixed path or the unused lockfile advisory can be eliminated.
- Kubernetes local smoke validated one-replica behavior on Docker Desktop. Multi-replica replay protection still needs a shared replay store before claiming cross-pod edge replay suppression.

## Evidence that outdated architecture was deleted

- The API Gateway production proto and generated code are deleted rather than retained:
  - `distributed-backend/proto/eve/api_gateway/v1/api_gateway.proto`
  - `distributed-backend/proto/gen/eve/api_gateway/v1/api_gateway.pb.go`
  - `distributed-backend/proto/gen/eve/api_gateway/v1/api_gatewayv1connect/api_gateway.connect.go`
- The API Gateway direct command RPC handler and tests are deleted:
  - `distributed-backend/src/api-gateway/distributed-backend/handler.go`
  - `distributed-backend/src/api-gateway/distributed-backend/handler_test.go`
- The production API Gateway HTTPRoute for the removed command service is deleted:
  - `distributed-backend/orchestration/kubernetes/overlay/prod/httproute.yaml`
- Market production proto now exposes only `SubmitTradeGuiInteraction`.
- API Gateway `MarketClient` now has only `SubmitTradeGuiInteraction`.
- API Gateway forwards only `RawPayload` and contains no `source_transport` or `source_address` business request fields.
- `scripts/verify_architecture_boundaries.py` fails CI if removed public RPCs, gateway-to-Market source metadata, simulator identity leakage tests, or old production docs return.
- `rg` over production code/docs finds removed public RPC names only inside the guard script itself.
