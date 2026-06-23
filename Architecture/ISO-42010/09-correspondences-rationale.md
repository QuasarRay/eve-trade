# Correspondences and Rationale

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical |
| Last reviewed | 2026-06-23 |
| Governing framework | eve-trade Architecture Description Framework |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

## Purpose

This document records architecture correspondences between views and the
rationale for material architecture decisions.

## Correspondence Matrix: Public API To Service Implementation

| Public API method | Gateway handler/client | Market method | Market responsibility | Verification |
| --- | --- | --- | --- | --- |
| `GameTradeGatewayService.IssueTradeInstance` | API Gateway handler forwards to Market client. | `MarketService.IssueTradeInstance` | Validate seller/item stack, build issue settlement batch, publish settlement. | Gateway and Market tests exist; last run not recorded in this doc update. |
| `GameTradeGatewayService.AcceptTradeInstance` | API Gateway handler forwards to Market client. | `MarketService.AcceptTradeInstance` | Validate trade, buyer wallet, seller wallet, destination stack, price/quantity, publish settlement. | Gateway and Market tests exist; last run not recorded in this doc update. |
| `GameTradeGatewayService.CancelTradeInstance` | API Gateway handler forwards to Market client. | `MarketService.CancelTradeInstance` | Validate actor permission and open trade, build cancellation settlement batch, publish settlement. | Gateway and Market tests exist; last run not recorded in this doc update. |

## Correspondence Matrix: Settlement Operation To Data Effects

| Settlement operation | Runtime owner | Primary data effects | Integrity view reference |
| --- | --- | --- | --- |
| `CreateNewTradeInstanceRow` | trade-settlement | `trade_instance`, settlement step output | INV-09, operation semantics table |
| `ModifyTradeInstanceState` | trade-settlement | `trade_instance`, `trade_state_change` | INV-09 |
| `CreateNewEmptyItemStack` | trade-settlement | `item_stack` | INV-08 |
| `TransferQuantityFromItemStackToItemStackEscrow` | trade-settlement | `item_stack`, `item_stack_escrow`, `item_stack_ledger` | INV-04, INV-07, INV-08 |
| `TransferQuantityFromItemStackEscrowToItemStackWithNewOwner` | trade-settlement | `item_stack_escrow`, destination `item_stack`, `item_stack_ledger` | INV-07, INV-08 |
| `TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner` | trade-settlement | `item_stack_escrow`, seller `item_stack`, `item_stack_ledger` | INV-07, INV-11 |
| `MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner` | trade-settlement | source/destination `item_stack`, `item_stack_ledger` | INV-08 |
| `CreateNewEmptyWalletEscrow` | trade-settlement | `wallet_escrow` | INV-10 |
| `TransferIskAmountFromWalletToWalletEscrow` | trade-settlement | `wallet`, `wallet_escrow`, `wallet_ledger` | INV-07, INV-10 |
| `TransferIskAmountFromWalletEscrowToWalletWithNewOwner` | trade-settlement | `wallet_escrow`, destination `wallet`, `wallet_ledger` | INV-07, INV-10 |
| `TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner` | trade-settlement | `wallet_escrow`, previous owner `wallet`, `wallet_ledger` | INV-07 |

## Correspondence Matrix: Deployment Flow To Policy

| Runtime flow | Config or port evidence | Kubernetes policy evidence | Gap |
| --- | --- | --- | --- |
| Gateway namespace to API Gateway | API Gateway `:8080` | API Gateway ingress policy from gateway namespace label. | Depends on correct namespace label. |
| API Gateway to Market | `MARKET_URL=http://market:8081` | API Gateway egress and Market ingress policies on `8081`. | None documented. |
| Market to RabbitMQ | `RABBITMQ_SETTLEMENT_*`, AMQP `5672` | Market egress to RabbitMQ and RabbitMQ ingress from Market. | Broker-level per-service authorization not documented. |
| Market direct to trade-settlement | `SETTLEMENT_TRANSPORT=connect` or equivalent and `TRADE_SETTLEMENT_URL` | Not allowed by the checked-in production network policy because Market has no trade-settlement egress and trade-settlement ingress allows settlement-worker only. | Implemented by code but not the Compose/Kubernetes path. |
| Market to PostgreSQL | `DATABASE_URL` | Broad TCP `5432` egress. | Destination is not selected by pod/namespace policy. |
| settlement-worker to RabbitMQ | Worker RabbitMQ config, AMQP `5672` | Worker egress to RabbitMQ and RabbitMQ ingress from worker. | Broker-level per-service authorization not documented. |
| settlement-worker to trade-settlement | `TRADE_SETTLEMENT_URL=http://trade-settlement:9092` | Worker egress and trade-settlement ingress on `9092`. | Depends on mesh/service account policy in production. |
| trade-settlement to PostgreSQL | `DATABASE_URL` | Broad TCP `5432` egress. | Destination is not selected by pod/namespace policy. |
| app pods to observability | OTLP `4317`/`4318` | Telemetry egress to collector namespace. | Alert/dashboard definitions not complete. |

## Correspondence Rules

| ID | Method | Correspondence | Source AD element | Target AD element | Verification status |
| --- | --- | --- | --- | --- | --- |
| COR-01 | Equivalence | API Gateway public methods correspond one-for-one to Market service methods for issue, accept, and cancel. | VC-CTX-03 | MODEL-RUN-01 | Evidence-backed |
| COR-02 | Reuse | API Gateway protobuf methods reuse Market request and response message types. | VC-CTX-03 | MODEL-VAL-01 | Evidence-backed |
| COR-03 | Refinement | Market settlement plans correspond to `SettlementOperation` sequences in the trade-settlement protobuf contract. | MODEL-RUN-01 | MODEL-DATA-03 | Evidence-backed |
| COR-04 | Refinement | `SettlementOperation` kinds correspond to SQL operation handlers and settlement step records. | MODEL-DATA-03 | MODEL-DATA-02 | Structurally represented |
| COR-05 | Dependency | Runtime service calls correspond to Kubernetes network policy allowances. | VC-CTX-01 | VC-DEP-02 | Gap recorded for database egress precision |
| COR-06 | Satisfaction | Health/readiness endpoints correspond to Kubernetes probes and operational readiness concerns. | VC-CTX-03 | VC-DEP-01 | Gap recorded for Market and trade-settlement readiness |
| COR-07 | Satisfaction | Database migrations correspond to the persistent data groups and invariants in the information view. | MODEL-DATA-01 | VC-DEP-01 | Evidence-backed |
| COR-08 | Trace | Idempotency keys and request fingerprints correspond across protobuf requests, RabbitMQ messages, settlement metadata, and Market replay reads. | VC-RUN-02 | MODEL-DATA-02 | Evidence-backed |
| COR-09 | Constraint | Security trust boundaries correspond to deployment network boundaries and service responsibilities. | VC-SEC-01 | VC-DEP-02 | Gap recorded for identity binding and broad DB egress |
| COR-10 | Satisfaction | Validation commands correspond to the source modules and deployment assets they protect. | VC-VAL-01 | All views | Not run |

## Correspondence Method Definitions

| Method | Definition | Failure condition |
| --- | --- | --- |
| Trace | The same architectural element, identifier, or requirement is followed across views. | The element cannot be found in one participating view. |
| Equivalence | Two views describe the same operation or element at comparable abstraction. | The participant counts, names, or semantics diverge without rationale. |
| Reuse | One view reuses a contract, model, or source artifact identified by another view. | The reused artifact is replaced or forked without updating both views. |
| Refinement | A high-level behavior is decomposed into lower-level operations, data effects, or controls. | Lower-level details do not implement the high-level behavior. |
| Dependency | One view depends on another view's component or control. | The dependency is absent, ambiguous, or unsupported by evidence. |
| Satisfaction | A control, validation, or model element satisfies a concern from another view. | The target does not address the source concern or has only aspirational text. |
| Constraint | One view constrains the allowed interpretation of another view. | The constrained view permits behavior the source view forbids. |

## Correspondence Verification Register

| Correspondence | Participants | Evidence anchor | Current verification |
| --- | --- | --- | --- |
| COR-01 | `GameTradeGatewayService.*`, `MarketService.*` | `distributed-backend/proto/eve/api_gateway/v1/api_gateway.proto`; `distributed-backend/proto/eve/market/v1/market.proto` | Evidence-backed; tests not run in this update |
| COR-02 | API Gateway request/response messages and Market messages | API Gateway proto imports `eve.market.v1` messages | Evidence-backed |
| COR-03 | Market `SettlementPlan`, `SettlementOperation` protobuf | `distributed-backend/src/market/game-trade/settle_trade_instance.go`; `distributed-backend/proto/eve/trade_settlement/v1/trade_settlement.proto` | Evidence-backed |
| COR-04 | Settlement operation variants, SQL handlers, settlement step records | `distributed-backend/src/trade-settlement/src/commands.rs`; `executor.rs`; migration `settlement_step` table | Structurally represented; exact test mapping still incomplete |
| COR-05 | Context flows and Kubernetes network policies | `distributed-backend/orchestration/kubernetes/overlay/prod/networkpolicies.yaml` | Gap recorded for database egress destination precision |
| COR-06 | `/healthz`, `/readyz`, Kubernetes probes | Service server files and Kubernetes base manifests | Gap recorded for incomplete dependency readiness |
| COR-07 | Migration files and data groups | `distributed-backend/src/trade-settlement/migrations`; Kubernetes migration manifests | Evidence-backed |
| COR-08 | Idempotency key in proto, Market replay, settlement metadata | Market proto fields; `LoadCompletedIdempotencyReplay`; `idempotency_record` migration | Evidence-backed |
| COR-09 | Security boundaries and network boundaries | `07-security-trust-view.md`; `06-deployment-operations-view.md` | Gap recorded for actor binding and settlement privilege |
| COR-10 | Validation commands and protected artifacts | `08-development-validation-view.md` | Not run in this documentation update |

## Consistency Checks

| Check | Expected result |
| --- | --- |
| Every public game-facing command has a Market equivalent. | Issue, accept, and cancel exist in both API Gateway and Market contracts. |
| Every Market command in the checked-in Compose/Kubernetes path reaches settlement through RabbitMQ and settlement-worker. | Market publishes settlement commands when `SETTLEMENT_TRANSPORT=rabbitmq`; Market code also supports direct/connect settlement transport outside that configured path. |
| Every settlement operation family has data integrity coverage. | Trade, item, wallet, escrow, ledger, idempotency, and metadata operations are represented in the information view. |
| Every production service communication path is represented in network policy intent. | API Gateway to Market, Market to DB/RabbitMQ, worker to RabbitMQ/settlement, settlement to DB, migration to DB. |
| Every high-trust API has a trust boundary entry. | Market and trade-settlement are both identified as internal trust boundaries. |
| Every generated contract has a validation owner. | Protobuf, Go generated code, Rust generated code, and CI generation/lint checks are identified. |

## Architecture Rationale

## Decision Record Register

| ADR | Status | Date recorded | Owner | Alternatives considered | Evidence |
| --- | --- | --- | --- | --- | --- |
| ADR-01 | Accepted | 2026-06-22 | Backend maintainers | REST/JSON only; raw gRPC only; hand-written HTTP clients | Protobuf files and generated code paths. |
| ADR-02 | Accepted | 2026-06-22 | Backend maintainers and product owner | Put trade rules in trade-settlement; put rules in API Gateway | Market handlers and `game-trade` package. |
| ADR-03 | Accepted | 2026-06-22 | Settlement/data integrity owner | Let Market write DB; split writes by entity service | Rust settlement executor and migrations. |
| ADR-04 | Accepted with operational risks | 2026-06-22 | Backend maintainers and SRE | Direct Market to settlement RPC only; fire-and-forget async command | RabbitMQ messaging library and worker. |
| ADR-05 | Accepted | 2026-06-22 | Database/migration owner | Per-service databases; event-sourced store only | PostgreSQL migrations and transaction model. |
| ADR-06 | Accepted | 2026-06-22 | SRE/platform operator | Document deployment as non-architecture; Compose only | Compose, Kubernetes, Terraform, observability manifests. |
| ADR-07 | Accepted as risk posture | 2026-06-22 | Security reviewer | Hide gaps until implementation; block all docs until security complete | Security view and risk register. |

Future ADR updates must include status, owner, alternatives, decision drivers,
consequences, and evidence. Missing fields are treated as partial rationale.

### ADR-01 Use Protobuf And Connect For Service Contracts

Decision: Define API Gateway, Market, and trade-settlement services with
protobuf contracts and implement service-to-service calls with Connect-compatible
HTTP.

Rationale:

- Protobuf gives stable, language-neutral contracts across Go and Rust.
- Connect-compatible HTTP allows generated clients and servers while remaining
  operationally simple for local and Kubernetes environments.
- Shared message types between API Gateway and Market reduce translation drift
  at the game-facing boundary.

Consequences:

- Contract changes require regeneration and validation.
- API compatibility must be managed explicitly if services are deployed
  independently.

### ADR-02 Market Owns Trade Mechanics

Decision: Market performs issue, accept, and cancel validation and constructs
settlement operation batches.

Rationale:

- Trade rules depend on current snapshots of items, wallets, trades, ownership,
  price, remaining quantity, destination stack compatibility, and expiration.
- Keeping trade policy in Market keeps trade-settlement focused on atomic
  execution of requested operation batches and row-level data preconditions.

Consequences:

- Market needs read access to PostgreSQL.
- Settlement still enforces transaction safety, idempotency, and row-level
  preconditions because Market validation is not a database transaction.

### ADR-03 trade-settlement Owns Durable Mutation

Decision: trade-settlement is the only component that applies requested trade,
escrow, wallet, item stack, ledger, idempotency, and settlement metadata writes.

Rationale:

- Centralizing mutation keeps cross-entity invariants in one transactional
  boundary.
- Rust plus SQLx supports explicit operation handlers and compile-time oriented
  query discipline.
- Settlement metadata can be written consistently with business mutation.

Consequences:

- The settlement API is privileged and is exposed only on internal service paths
  in the checked-in manifests.
- Generic operation contracts increase flexibility and make Market/broker/worker
  reachability a high-trust path. The current repository does not implement
  operation-provenance or operation-allow policy in trade-settlement.

### ADR-04 Use RabbitMQ Between Market And Settlement Execution

Decision: The checked-in Compose and Kubernetes deployments configure Market to
publish settlement commands through RabbitMQ, and settlement-worker calls
trade-settlement.

Rationale:

- The broker provides an explicit asynchronous boundary, command/reply behavior,
  and dead-letter handling.
- A worker allows the RabbitMQ-configured settlement execution path to be scaled
  and isolated from Market.
- The architecture can observe and manage failed or delayed settlement messages.

Consequences:

- The caller-facing path still waits for a settlement reply or error, so broker
  availability affects request latency and success.
- Idempotency is mandatory because broker and network failures can obscure final
  settlement state.

### ADR-05 Use PostgreSQL As The Source Of Truth

Decision: Store all authoritative trade, item, wallet, escrow, idempotency, and
settlement metadata in PostgreSQL.

Rationale:

- Trade settlement requires atomic updates across multiple entity types.
- SQL constraints, transactions, locks, and ledgers are a good fit for financial
  and inventory integrity.
- A single source of truth simplifies replay and incident analysis.

Consequences:

- PostgreSQL availability is critical to both validation and settlement.
- Schema migration discipline is central to safe deployment.

### ADR-06 Treat Deployment Configuration As Architecture

Decision: Keep Compose, Kubernetes, Terraform, network policy, and observability
configuration as first-class architecture artifacts.

Rationale:

- The service chain only works if dependencies, ports, probes, credentials, and
  network reachability match the intended architecture.
- The checked-in local and production-like manifests model the same logical
  RabbitMQ settlement topology.

Consequences:

- Architecture updates must include deployment artifacts when communication
  paths or runtime dependencies change.
- Static manifest validation is necessary but does not replace live runtime
  tests.

### ADR-07 Keep Known Security Gaps Explicit

Decision: Record identity, authorization, and settlement trust-boundary gaps in
the architecture description.

Rationale:

- The codebase has partial security controls through topology and network
  policy, but not complete end-to-end identity enforcement.
- Explicit residual risk prevents readers from assuming production readiness
  from deployment manifests alone.

Consequences:

- Current security gaps are explicit in the architecture description.
- The repository does not show a completed end-to-end authentication,
  authorization, transport, and service-to-service access-control validation.

## Rationale Review Triggers

Review this document when:

- a new trade command is added;
- a service starts writing directly to PostgreSQL business tables;
- RabbitMQ is bypassed, replaced, or made optional;
- identity, authorization, or service-to-service security changes;
- protobuf contracts change in an incompatible way;
- migrations introduce new business invariants;
- Kubernetes network policy or Gateway/Istio topology changes.
