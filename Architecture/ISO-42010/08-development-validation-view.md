# Development and Validation View

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical current state |
| Last reviewed | 2026-06-25 |
| Governing viewpoints | VP-07 Development and Validation, VP-11 Contract Compatibility and Evolution |
| Evidence baseline | v6 architecture cleanup; starting commit recorded in `changes/v6/changes.md` |

Governed by: [VP-07 Development And Validation Viewpoint](./02-viewpoints.md#vp-07-development-and-validation-viewpoint)

## Source Module Model

| Area | Location | Responsibility |
| --- | --- | --- |
| Protobuf source | `distributed-backend/proto/eve` | Market GUI submission contract and trade-settlement settlement operation contract. |
| Generated Go protobuf code | `distributed-backend/proto/gen` | Go clients and servers for protobuf contracts. Generated API Gateway service code is deleted. |
| API Gateway service | `distributed-backend/src/api-gateway` | UDP edge, transport safety, HMAC/replay/rate/queue controls, raw-payload Market forwarding, health/readiness. |
| Market service | `distributed-backend/src/market` | GUI payload interpretation, trade mechanics, validation, repository reads, idempotency/replay reads, settlement command publishing. |
| Messaging library | `distributed-backend/src/messaging/rabbitmqsettlement` | RabbitMQ topology, publish, consume, reply, and dead-letter behavior. |
| settlement-worker service | `distributed-backend/src/settlement-worker` | RabbitMQ consumer and trade-settlement client. |
| trade-settlement crate | `distributed-backend/src/trade-settlement` | Rust settlement service, SQL operations, migrations, configuration, and seed data. |
| Simulator | `simulator/trade_gui` | Local game-frontend simulator that emits authenticated packets conforming to the versioned repository protocol. |
| E2E tests | `distributed-backend/tests/e2e` | Simulator-driven end-to-end trade lifecycle tests. |
| Compose runtime | `compose.yaml`, `docker-compose.integration.yml` | Local and CI runtime topology including simulator, Quilkin, API Gateway UDP, Market, settlement-worker, trade-settlement, RabbitMQ, and PostgreSQL. |
| Kubernetes manifests | `distributed-backend/orchestration/kubernetes` | Local and production overlays, Quilkin/API Gateway UDP routing, network policy, mesh policy, observability, and platform resources. |
| Terraform | `distributed-backend/terraform/eks`, `distributed-backend/terraform/gke`, `distributed-backend/terraform/talos-omni` | AWS/EKS, GCP/GKE, and Omni-managed Talos infrastructure or runtime prerequisite provisioning. |
| CI | `.github/workflows/verify.yaml`, `scripts/verify_architecture_boundaries.py` | Static validation, tests, security scans, manifest validation, and compose e2e pipeline definitions. |

## Change Impact Matrix

| Change type | Likely affected artifacts | Required validation focus |
| --- | --- | --- |
| Game GUI packet field or envelope | Simulator packet builder/signer, API Gateway UDP parser, Market GUI parser, packet leak test, docs | Production-identical packet shape, HMAC compatibility, forbidden identity leakage. |
| Market GUI action mapping | Market handler/private helpers, Market tests, e2e tests, docs | Market-owned interpretation, no gateway business parsing, settlement operation correctness. |
| API Gateway edge behavior | UDP server, config, real-socket/queue/auth/replay/fuzz tests, Compose/Kubernetes config, observability docs | Bounded concurrency, queue overflow, principal rate limit, HMAC request/response authentication, replay, timeout, compact responses. |
| Settlement operation kind | trade-settlement protobuf, generated Go/Rust code, Market settlement planner, worker, Rust operations, SQL invariants | Operation semantics, atomicity, idempotency, and game-mechanic agnosticism. |
| Database schema | SQL migrations, Market repository reads, Rust operations, seed data, Kubernetes migration manifests | Migration order, compatibility, constraints, and local seed validity. |
| RabbitMQ topology | Messaging library, Market config, settlement-worker config, Compose, Kubernetes ConfigMaps/Secrets | Publish/consume/reply behavior and dead-letter behavior. |
| Deployment ports or paths | Service config, Compose, Kubernetes manifests, network policies, probes, Quilkin config, Istio resources | Runtime reachability, production/local separation, and manifest validation. |
| Security policy | API Gateway edge auth, NetworkPolicy, Istio security resources, Secrets, docs | Trust boundary preservation, replay/idempotency, and identity limitations. |

## Required CI Gates

| Gate | CI evidence |
| --- | --- |
| Protobuf lint/build/generation freshness | `proto` job runs buf build, buf lint, buf format, buf generate, and generated-code diff. |
| Boundary drift guard | `architecture-boundaries` job runs `scripts/verify_architecture_boundaries.py`. |
| Go formatting/tests/vet/race/staticcheck/govulncheck | `go` matrix job runs module tidy, `gofmt`, `go test`, `go vet`, `go test -race`, `staticcheck`, `govulncheck`, and service builds. |
| Rust formatting/clippy/tests/audit | `rust-trade-settlement` job runs `cargo fmt`, `cargo check --all-targets --all-features`, `cargo test --all-features`, `cargo clippy -D warnings`, and `cargo audit`. |
| Python simulator packet test and dependency checks | `python-simulator` job installs simulator requirements, runs `pip check`, `compileall`, `python manage.py test trade_gui`, and `pip-audit`. |
| Kubernetes/IaC validation | `kubernetes` job renders platform, local, and production overlays and validates them with kubeconform; `terraform` matrix runs fmt/init/validate. |
| Docker/Compose e2e | `e2e` job runs the real simulator -> Quilkin -> API Gateway UDP -> Market -> settlement-worker -> trade-settlement -> PostgreSQL path and verifies duplicate/replay behavior through tests. |

## Architecture Boundary Guard Rules

`scripts/verify_architecture_boundaries.py` fails CI when:

- production protos expose removed direct issue, accept, or cancel trade RPCs;
- Market's GUI submission request contains gateway source metadata;
- API Gateway source code contains forbidden Market metadata fields;
- the simulator packet boundary test is missing required forbidden-key checks;
- current architecture docs document the removed production RPC path;
- docs omit the canonical path:
  `game frontend -> Quilkin UDP -> API gateway UDP edge -> Market GUI interaction -> settlement operations -> trade-settlement`;
- production Kubernetes overlays include local simulator resources.

## Contract Compatibility Rules

| Artifact type | Compatibility rule | Validation gate |
| --- | --- | --- |
| Market GUI submission RPC | The production request remains `bytes raw_payload = 1`; gateway source metadata must not be added. | Buf lint/generate, boundary guard, gateway tests. |
| Removed command-shaped RPCs | Direct production issue, accept, and cancel RPCs must not return in API Gateway or Market protos. | Boundary guard. |
| GUI packet shape | New fields must be real game frontend protocol data. Simulator/test/framework/source identity is forbidden in outbound UDP payloads. | Simulator packet test and boundary guard. |
| API Gateway behavior | Gateway changes must remain transport-level only and must not parse game mechanics. | Code review, gateway tests, architecture docs. |
| Settlement operation variant | New variants require Rust command conversion, operation handler, SQL effect analysis, and Market planning changes where used. | Rust tests, generated code, invariant matrix update. |
| SQL migration | Migrations must be ordered, repeatable in fresh environments, and compatible with current service startup. | Compose e2e, Kubernetes render, settlement tests. |
| Kubernetes production overlay | Production must include Quilkin/API Gateway path and exclude local simulator/dev-only resources. | kustomize render, kubeconform, boundary guard. |
| Terraform infrastructure change | Runtime assumptions, secrets, networking, and cost/capacity notes must be updated. | Terraform validation and platform review. |

## Current Validation Limitations

| Limitation | Handling |
| --- | --- |
| Buf breaking against the pre-v6 main branch would flag intentional RPC deletion. | Not enabled for this refactor; generated freshness and boundary guards are enforced. |
| Python formatting/linting does not use a project ruff/black config. | CI runs compile, tests, `pip check`, and `pip-audit`; adding a style config is future work. |
| Production image digests/secrets are placeholders in source. | CI validates renderability; release injection and secret provisioning remain deployment responsibilities. |
| Full local e2e depends on Docker availability. | CI runs compose e2e; local command results are recorded in `changes/v6/changes.md`. |

## Development View Assertions

| Assertion | Enforcement tag | Evidence |
| --- | --- | --- |
| API contracts, generated code, service implementation, tests, docs, and CI must change together. | Enforced by CI | `verify.yaml` and boundary guard. |
| Gateway is not a business service. | Enforced by tests/guard | Gateway has no production command service and forwards raw payload only. |
| Simulator packet shape conforms to the versioned repository contract. | Enforced by cross-language test | Python validates the actual socket payload against JSON Schema and a golden packet; Go consumes the same golden packet. External-client identity is not claimed. |
| trade-settlement remains game-mechanic agnostic. | Enforced by contract review | Settlement proto exposes low-level operation batches only. |
| A change is not architecture-consistent until its runtime path and validation path are both known. | Governance rule | Change Impact Matrix and CI gates. |
