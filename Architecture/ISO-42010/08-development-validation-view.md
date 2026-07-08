# Development and Validation View

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical current state |
| Last reviewed | 2026-07-08 |
| Governing viewpoints | VP-07 Development and Validation, VP-11 Contract Compatibility and Evolution |
| Evidence baseline | v9 experimental refactor; branch delta recorded in `changes/v9/changes.md` |

Governed by: [VP-07 Development And Validation Viewpoint](./02-viewpoints.md#vp-07-development-and-validation-viewpoint)

## Source Module Model

| Area | Location | Responsibility |
| --- | --- | --- |
| Protobuf source | `proto/eve`, `proto/buf/validate/validate.proto` | API gateway and Market service contracts, Market GUI payload contract, reusable protovalidate rules, trade input validation messages, and trade-settlement settlement operation contract. `buf.yaml` declares the BSR dependency on `buf.build/bufbuild/protovalidate`; the local `proto/buf/validate/validate.proto` file is a build fallback for local generation. |
| Generated Go protobuf code | `proto/gen` | Go protobuf types for API gateway, Market, trade input validation, settlement commands, and reusable validation-rule extensions. Local gRPC stub generation depends on `protoc-gen-go-grpc`, which was not available during this v9 pass. |
| Encore gateway service | `gateway` | UDP edge, transport safety, HMAC/replay/rate/queue controls, raw-payload Market forwarding, health/readiness. |
| Market service | `market` | GUI payload interpretation, trade mechanics orchestration, current-state preconditions, repository reads, idempotency/replay reads, settlement command publishing. |
| Game-trade domain helpers | `internal/gametrade` | Trade plan construction and protovalidate-backed issue/accept/cancel input validation. |
| Settlement work package | `settlement` | Encore Pub/Sub topic and typed settlement work payloads. |
| settlement worker service | `settlementworker` | Encore Pub/Sub subscription handler, protovalidate-backed settlement request conversion, and trade-settlement client. |
| trade-settlement crate | `distributed-backend/src/trade-settlement` | Rust settlement service, SQL operations, migrations, configuration, and seed data. |
| Simulator | `simulator/trade_gui` | Local game-frontend simulator that emits authenticated packets conforming to the versioned repository protocol. |
| E2E tests | `distributed-backend/tests/e2e` | Simulator-driven end-to-end trade lifecycle tests. |
| local Encore/Kubernetes runtime | `Encore local run scripts`, `Kubernetes/E2E runtime configuration` | Local and CI runtime topology including simulator, Quilkin, Encore gateway UDP, Market, settlement worker, trade-settlement, Encore Pub/Sub, and PostgreSQL. |
| Kubernetes manifests | `distributed-backend/orchestration/kubernetes` | Local and production overlays, Quilkin/Encore gateway UDP routing, network policy, mesh policy, observability, and platform resources. |
| Terraform | `distributed-backend/terraform/eks`, `distributed-backend/terraform/gke`, `distributed-backend/terraform/talos-omni` | AWS/EKS, GCP/GKE, and Omni-managed Talos infrastructure or runtime prerequisite provisioning. |
| CI | `.github/workflows/verify.yaml`, `scripts/verify_architecture_boundaries.py` | Static validation, tests, security scans, manifest validation, and local runtime/e2e pipeline definitions. |

## Change Impact Matrix

| Change type | Likely affected artifacts | Required validation focus |
| --- | --- | --- |
| Game GUI packet field or envelope | Simulator packet builder/signer, Encore gateway UDP parser, Market GUI parser, packet leak test, docs | Production-identical packet shape, HMAC compatibility, forbidden identity leakage. |
| Market GUI action mapping | Market handler/private helpers, Market tests, e2e tests, docs | Market-owned interpretation, no gateway business parsing, settlement operation correctness. |
| Encore gateway edge behavior | UDP server, config, real-socket/queue/auth/replay/fuzz tests, local Encore/Kubernetes/Kubernetes config, observability docs | Bounded concurrency, queue overflow, principal rate limit, HMAC request/response authentication, replay, timeout, compact responses. |
| Settlement operation kind | trade-settlement protobuf, reusable validation rules, generated Go/Rust code, Market settlement planner, worker, Rust operations, SQL invariants | Operation semantics, proto validation, atomicity, idempotency, and game-mechanic agnosticism. |
| Database schema | SQL migrations, Market repository reads, Rust operations, seed data, Kubernetes migration manifests | Migration order, compatibility, constraints, and local seed validity. |
| Encore Pub/Sub topic and subscription topology | `settlement`, Market config, settlement worker config, local Encore/Kubernetes, Kubernetes ConfigMaps/Secrets | Publish/consume behavior, retry behavior, and dead-letter behavior. |
| Deployment ports or paths | Service config, local Encore/Kubernetes, Kubernetes manifests, network policies, probes, Quilkin config, Istio resources | Runtime reachability, production/local separation, and manifest validation. |
| Security policy | Encore gateway edge auth, NetworkPolicy, Istio security resources, Secrets, docs | Trust boundary preservation, replay/idempotency, and identity limitations. |

## Required CI Gates

| Gate | CI evidence |
| --- | --- |
| Protobuf lint/build/generation freshness | `proto` job runs buf build, buf lint, buf format, buf generate, and generated-code diff. |
| Boundary drift guard | `architecture-boundaries` job runs `scripts/verify_architecture_boundaries.py`. |
| Go formatting/tests/vet/race/staticcheck/govulncheck | `go` matrix job runs module tidy, `gofmt`, `go test`, `go vet`, `go test -race`, `staticcheck`, `govulncheck`, and service builds. |
| Rust formatting/clippy/tests/audit | `rust-trade-settlement` job runs `cargo fmt`, `cargo check --all-targets --all-features`, `cargo test --all-features`, `cargo clippy -D warnings`, and `cargo audit`. |
| Python simulator packet test and dependency checks | `python-simulator` job installs simulator requirements, runs `pip check`, `compileall`, `python manage.py test trade_gui`, and `pip-audit`. |
| Kubernetes/IaC validation | `kubernetes` job renders platform, local, and production overlays and validates them with kubeconform; `terraform` matrix runs fmt/init/validate. |
| Local Encore/Kubernetes e2e | `e2e` job runs the simulator -> Quilkin -> Encore gateway UDP -> Market -> settlement worker -> trade-settlement -> PostgreSQL path and verifies duplicate/replay behavior through tests. |

## Architecture Boundary Guard Rules

`scripts/verify_architecture_boundaries.py` fails CI when:

- gateway or Market runtime code exposes public command-shaped trade APIs outside the restored internal proto/gRPC contracts;
- Market's GUI submission request contains gateway source metadata;
- Encore gateway source code contains forbidden Market metadata fields;
- the simulator packet boundary test is missing required forbidden-key checks;
- current architecture docs document the removed production RPC path;
- docs omit the canonical path:
  `game frontend -> Quilkin UDP -> Encore gateway UDP edge -> Market GUI interaction -> settlement operations -> trade-settlement`;
- production Kubernetes overlays include local simulator resources.

## Contract Compatibility Rules

| Artifact type | Compatibility rule | Validation gate |
| --- | --- | --- |
| Market GUI submission RPC | The UDP runtime request remains `RawPayload []byte`; gateway source metadata must not be added. | Buf lint/generate, boundary guard, gateway tests. |
| Restored command-shaped RPCs | Direct issue, accept, and cancel RPCs may exist in the internal API gateway and Market proto/gRPC contracts, but runtime gateway code must keep business decisions in Market and validation in proto. | Buf lint/build/generate, gateway tests, Market focused tests. |
| GUI packet shape | New fields must be real game frontend protocol data. Simulator/test/framework/source identity is forbidden in outbound UDP payloads. | Simulator packet test and boundary guard. |
| Encore gateway behavior | Gateway changes must remain transport-level only and must not parse game mechanics. | Code review, gateway tests, architecture docs. |
| Settlement operation variant | New variants require Rust command conversion, operation handler, SQL effect analysis, and Market planning changes where used. | Rust tests, generated code, invariant matrix update. |
| SQL migration | Migrations must be ordered, repeatable in fresh environments, and compatible with current service startup. | local Encore/Kubernetes e2e, Kubernetes render, settlement tests. |
| Kubernetes production overlay | Production must include Quilkin/Encore gateway path and exclude local simulator/dev-only resources. | kustomize render, kubeconform, boundary guard. |
| Terraform infrastructure change | Runtime assumptions, secrets, networking, and cost/capacity notes must be updated. | Terraform validation and platform review. |

## Proto Validation Governance

| Rule area | Governance rule | Validation gate |
| --- | --- | --- |
| Reusable scalar rules | Common non-blank, UUID, positive integer, and trade state/kind rules belong in `proto/eve/validation/v1/validation_rules.proto` as predefined `buf.validate` extensions. | `buf lint`, `buf build`, `buf generate`, generated Go package tests, Rust trade-settlement tests. |
| API gateway and Market boundary rules | UDP edge envelope/config/actor-binding validation belongs in `proto/eve/api_gateway/v1/api_gateway.proto`; Market typed RPC and GUI payload validation belongs in `proto/eve/market/v1/market.proto`. Go should decode/adapter-map and call protovalidate instead of duplicating verbose field checks. | `go test ./gateway`, generated Go package tests, `go test ./market` when current async expectation drift is resolved. |
| Game-trade input rules | Issue, accept, and cancel request-shape rules belong in `proto/eve/trade/v1/trade.proto`; Go should only adapt existing structs and call protovalidate. | `go test ./internal/gametrade` with Encore runtime panic disabled when running outside Encore. |
| Settlement command rules | Settlement envelope, oneof, operation payload, and cross-field operation rules belong in `proto/eve/trade_settlement/v1/trade_settlement.proto`; Go and Rust boundaries should call protovalidate. | `go test ./settlementworker` with Encore runtime panic disabled; `cargo test --manifest-path distributed-backend/src/trade-settlement/Cargo.toml --no-default-features`. |
| Imperative exceptions | Go/Rust validation is allowed only for transport safety, JSON parsing before a proto exists, type conversion after proto validation, current PostgreSQL row preconditions, arithmetic overflow checks, and SQL/database invariants. | Code review plus focused package tests; new duplicate scalar/request checks should be moved back into proto. |
| Buf BSR dependency | `buf.yaml` must declare `buf.build/bufbuild/protovalidate`; generated Go code should import the canonical BSR Go module. The local checked-in `proto/buf/validate/validate.proto` is retained only as a local generation fallback when BSR access is unavailable. | `buf build`, `buf lint`, `buf generate`; `buf dep update` when registry access is available. |

## Current Validation Limitations

| Limitation | Handling |
| --- | --- |
| Buf breaking against the older main branch would flag intentional RPC deletion and proto relocation in this experimental branch. | Not enabled for this refactor; generated freshness and boundary guards are enforced. |
| Python formatting/linting does not use a project ruff/black config. | CI runs compile, tests, `pip check`, and `pip-audit`; adding a style config is future work. |
| Production image digests/secrets are placeholders in source. | CI validates renderability; release injection and secret provisioning remain deployment responsibilities. |
| Full local e2e depends on runtime dependencies and Encore tooling availability. | CI/runtime scripts own the complete e2e path; local command results for this refactor are recorded in `changes/v9/changes.md`. |

## Development View Assertions

| Assertion | Enforcement tag | Evidence |
| --- | --- | --- |
| API contracts, generated code, service implementation, tests, docs, and CI must change together. | Enforced by CI | `verify.yaml` and boundary guard. |
| Gateway is not a business service. | Enforced by tests/guard | Gateway has no production command service and forwards raw payload only. |
| Simulator packet shape conforms to the versioned repository contract. | Enforced by cross-language test | Python validates the actual socket payload against JSON Schema and a golden packet; Go consumes the same golden packet. External-client identity is not claimed. |
| trade-settlement remains game-mechanic agnostic. | Enforced by contract review | Settlement proto exposes low-level operation batches only. |
| A change is not architecture-consistent until its runtime path and validation path are both known. | Governance rule | Change Impact Matrix and CI gates. |
