# v9 Changes

## Comparison Baseline

- Compared the current `experimental` working tree against fetched `origin/main`
  at `8ec73d600be7bbb5382d96d1f015848d3712c60a`.
- Current branch head during this update was
  `8143b4ec2478caee844d24430971f14de6dd5205`; the working tree also contains
  uncommitted refactor and documentation changes.
- Merge base with `origin/main` was
  `7b5d7e07f10d5d347542baaa2abbce8b59777a46`.

## Runtime Architecture

- Replaced the older multi-module Go backend with a root Encore application:
  `encore.app`, `gateway`, `market`, `settlement`, `settlementworker`,
  `internal/gametrade`, and `internal/observability`.
- Removed the old standalone Go service modules under
  `distributed-backend/src/api-gateway`, `distributed-backend/src/market`,
  `distributed-backend/src/settlement-worker`, `distributed-backend/src/messaging`,
  and `distributed-backend/src/observability`.
- Removed the root Go workspace files and moved to a single root Go module.
- Kept the Rust `distributed-backend/src/trade-settlement` service as the
  non-Encore settlement executor behind a standard gRPC boundary.
- Replaced RabbitMQ settlement messaging with Encore Pub/Sub settlement work and
  result topics in the root `settlement` package.
- Preserved the production path:
  `game frontend -> Quilkin UDP -> Encore gateway UDP edge -> Market GUI interaction -> settlement operations -> settlement worker -> trade-settlement -> PostgreSQL`.

## Gateway And Market

- Split the gateway UDP implementation into focused files for listener
  lifecycle, packet orchestration, Market client calls, HMAC authentication,
  signed responses, telemetry, rate limiting, and replay cache behavior.
- Kept the custom UDP adapter because Encore does not own UDP listeners.
- Kept gateway business behavior narrow: packet safety, authentication,
  replay/rate/queue controls, and raw-payload forwarding only.
- Restored API gateway and Market protobuf service contracts at
  `proto/eve/api_gateway/v1/api_gateway.proto` and
  `proto/eve/market/v1/market.proto` so typed gRPC contracts exist again while
  the UDP runtime path continues to forward raw GUI payloads to Market.
- Split Market behavior into focused files for GUI dispatch, issue, accept,
  cancel, settlement publication, idempotency replay, request DTOs, repository
  access, and current-state precondition checks.
- Replaced the large GUI action switch with an action alias map while keeping
  explicit issue/accept/cancel orchestration.
- Removed duplicate Market owner/self-purchase business-rule checks now owned by
  the proto-backed `internal/gametrade` validators; retained database snapshot
  ownership/state/matching checks that depend on current rows.
- Removed the stale root `/market` ignore rule so the Encore Market source
  package can be tracked instead of being treated as a local Go binary artifact.

## Proto And Validation

- Moved canonical proto sources from `distributed-backend/proto` to root
  `proto`.
- Added Buf BSR dependency declaration for
  `buf.build/bufbuild/protovalidate` in `buf.yaml`.
- Kept a local `proto/buf/validate/validate.proto` fallback for local builds
  when the Buf Schema Registry is unavailable; generation excludes this local
  include and generated Go code imports the canonical BSR Go module.
- Added reusable predefined validation rules in
  `proto/eve/validation/v1/validation_rules.proto`.
- Added reusable GUI schema/action and UDP edge HMAC/schema predefined rules.
- Added protovalidate annotations to restored Market RPC messages, Market GUI
  payload messages, API gateway UDP envelope/config/actor-binding messages, and
  gateway downstream response identity messages.
- Replaced verbose Go GUI/UDP request-shape checks with proto validation
  adapters in `market/proto_validation.go` and `gateway/proto_validation.go`;
  Go keeps JSON decoding, HMAC comparison, replay/rate limiting, DB row
  preconditions, and dispatch logic.
- Added `market/proto_service.go` as the small adapter from restored Market
  proto request/response types to the existing Market domain handler.
- Added `proto/eve/trade/v1/trade.proto` for game-trade issue, accept, and
  cancel input validation messages.
- Annotated `proto/eve/trade_settlement/v1/trade_settlement.proto` with
  protovalidate envelope, oneof, UUID, non-blank, positive integer, timestamp,
  state/kind, and merge distinctness rules.
- Replaced duplicated Go request-shape checks in `internal/gametrade` with
  protovalidate calls against generated `eve.trade.v1` messages.
- Replaced settlement worker manual request-shape checks with protovalidate
  against generated `eve.trade_settlement.v1` messages.
- Added Rust `prost-protovalidate` and descriptor generation so
  `trade-settlement` validates incoming `ExecuteSettlementBatchRequest` values
  from the shared proto rules before Rust command conversion.
- Removed duplicate Rust command-level scalar/state/quantity validation; Rust now
  retains only protobuf-to-domain parsing, arithmetic safety, current-row
  preconditions, transaction behavior, and SQL invariant enforcement.
- Added Go and Rust dependencies plus vendored Go modules for protovalidate,
  CEL, and generated BSR validation packages.

## Deployment, Infrastructure, And Runtime Support

- Removed Docker Compose runtime files and old local Dockerfiles for the deleted
  standalone Go services.
- Added Kubernetes resources for the Encore backend and NSQ support while
  removing old RabbitMQ and separate Go service manifests.
- Updated local and production Kubernetes overlays, network policies, Istio
  policy/traffic resources, service accounts, config maps, and simulator/Quilkin
  manifests to match the Encore backend topology.
- Updated Terraform image/input variables for the current backend image model
  across EKS, GKE, shared image modules, and Talos/Omni roots.
- Updated local scripts (`run-local`, `stop-local`, GUI simulator demo scripts,
  and shell wrappers) for the current Encore/Kubernetes flow.

## CI, Tests, And Observability

- Reworked `.github/workflows/verify.yaml` and `ci-cd/pipeline.py` around the
  root Go module, Buf generation, Rust trade-settlement, Kubernetes render,
  Terraform validation, and simulator/e2e checks.
- Removed compose-specific runtime credential verification and Docker collection
  code; updated observability collection and failure-report helpers for the
  current local runtime posture.
- Updated e2e helpers and tests under `distributed-backend/tests/e2e` for the
  simulator -> Quilkin -> Encore gateway -> Market -> settlement worker ->
  trade-settlement path.
- Updated architecture boundary posture for the root Encore runtime and restored
  internal proto/gRPC contracts.

## Documentation

- Updated ISO-42010 docs to describe the current root Encore backend, retained
  Rust settlement service, Encore Pub/Sub settlement path, protovalidate
  ownership model, BSR dependency, and remaining validation exceptions.
- Restored and updated the Proto Architecture, Trade Request Lifecycle, and
  Trade State Lifecycle historical docs instead of deleting them.
- Updated README, CI/CD docs, observability docs, Kubernetes production notes,
  and local-vs-CI parity docs for the current architecture.
- Kept historical `changes/v1` through `changes/v8` files intact.

## Removed Or Replaced Legacy Artifacts

- Removed old `compose.yaml`, `docker-compose.integration.yml`, and
  `docker-compose.integration.local.yml`.
- Removed old `distributed-backend/proto` sources, generated Connect code, and
  nested proto Go module.
- Removed old standalone Go service code for API gateway, Market,
  settlement-worker, RabbitMQ messaging, and observability.
- Removed vendored packages that were only needed by the old Connect/RabbitMQ
  service split, including Connect, otelconnect, RabbitMQ AMQP, and old OTEL
  semantic convention vendor trees.

## Validation Results And Caveats

- Passed: `buf build`
- Passed: `buf lint`
- Passed: `buf generate`
- Passed:
  `go test ./proto/gen/eve/api_gateway/v1 ./proto/gen/eve/market/v1 ./proto/gen/eve/trade/v1 ./proto/gen/eve/trade_settlement/v1 ./proto/gen/eve/validation/v1`
- Passed: `go test ./internal/observability`
- Passed with `ENCORERUNTIME_NOPANIC=1`: `go test ./internal/gametrade`
- Passed with `ENCORERUNTIME_NOPANIC=1`: `go test ./settlementworker`
- Passed with `ENCORERUNTIME_NOPANIC=1`: `go test ./settlement`
- Passed with `ENCORERUNTIME_NOPANIC=1`: `go test ./gateway`
- Passed:
  `cargo test --manifest-path distributed-backend/src/trade-settlement/Cargo.toml --no-default-features`
- Caveat: `ENCORERUNTIME_NOPANIC=1 go test ./market` fails on existing handler
  expectations for synchronous settlement/replay errors versus the current
  queued async settlement path.
- Caveat: `buf dep update` could not reach `buf.build` from this environment, so
  the BSR dependency is declared but the local fallback include is retained for
  generation.
- Caveat: `encore` CLI is not installed locally. Plain Go tests for packages
  importing Encore Pub/Sub require `ENCORERUNTIME_NOPANIC=1` outside the Encore
  runner.
- Caveat: `protoc-gen-go-grpc` is not available in this environment and network
  installation attempts timed out, so current local generation produced Go
  protobuf message types but not `*_grpc.pb.go` service stubs.
