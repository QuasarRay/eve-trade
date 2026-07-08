# Architecture Facts Catalog

## Metadata

| Field | Value |
| --- | --- |
| Catalog ID | `FACTCAT-EVE-TRADE-001` |
| Date | 2026-07-08 |
| Status | Canonical current-state fact catalog |
| Evidence baseline | v9 experimental refactor; branch delta recorded in `changes/v9/changes.md` |

## Purpose

This catalog centralizes facts that are repeated across runtime, performance,
resilience, deployment, observability, security, and risk views. Other views
can reference these fact IDs rather than duplicating inconsistent prose.

## Fact Register

| Fact ID | Fact | Current status | Primary evidence anchors |
| --- | --- | --- | --- |
| FACT-001 | The architecture document baseline is content-addressed by file hashes until the architecture set is committed. | Evidence-backed | `18-evidence-manifest.md` |
| FACT-002 | The newest main branch baseline fetched for v9 comparison was `origin/main` commit `8ec73d600be7bbb5382d96d1f015848d3712c60a`. | Evidence-backed | `changes/v9/changes.md` |
| FACT-003 | The production path is `game frontend -> Quilkin UDP -> Encore gateway UDP edge -> Market GUI interaction -> settlement operations -> trade-settlement`. | Evidence-backed | `00-architecture-description.md`, `03-context-view.md`, README |
| FACT-004 | API gateway and Market proto files again define internal typed gRPC contracts for issue, accept, cancel, and GUI submission. The production UDP runtime path still enters through the gateway UDP edge. | Evidence-backed | `proto/eve/api_gateway/v1/api_gateway.proto`, `proto/eve/market/v1/market.proto`, `gateway/udp.go` |
| FACT-005 | Market keeps the current Encore GUI submission API and also has a small proto-service adapter for restored Market protobuf request/response types. | Evidence-backed | `market/api.go`, `market/proto_service.go`, `proto/eve/market/v1/market.proto` |
| FACT-006 | The gateway-to-Market UDP runtime submission request contains only `RawPayload []byte`; source transport/address metadata remains internal to gateway telemetry. | Evidence-backed | `market/api.go`, `gateway/packet.go` |
| FACT-007 | The Django simulator is a local game-frontend simulator and its outbound UDP packet conforms to the versioned repository schema and a golden packet consumed by both Python and Go tests. No claim of identity with an external game client is made. | Evidence-backed | `protocol`, `simulator/trade_gui/tests.py`, `gateway/udp_test.go` |
| FACT-008 | Encore gateway forwards raw GUI payload to Market and does not send source transport/address metadata as Market business data. | Evidence-backed | `quilkin_udp.go`, `quilkin_udp_test.go` |
| FACT-009 | trade-settlement receives low-level settlement operation batches, not game trade mechanics. | Evidence-backed | `proto/eve/trade_settlement/v1/trade_settlement.proto` |
| FACT-010 | gateway downstream timeout defaults to `5s`. | Evidence-backed | `gateway/config.go`, config key `API_GATEWAY_DOWNSTREAM_TIMEOUT` |
| FACT-011 | Market settlement request timeout defaults to `10s`. | Evidence-backed | `market/config.go`, key `Encore Pub/Sub publication and subscription retry settings` |
| FACT-012 | settlement worker request timeout is configured as `10s` in Kubernetes base config. | Evidence-backed | `distributed-backend/orchestration/kubernetes/base/configmaps.yaml`, key `SETTLEMENT_WORKER_REQUEST_TIMEOUT` |
| FACT-013 | Encore Pub/Sub publish timeout is configured as `5s` in Kubernetes base config. | Evidence-backed | `distributed-backend/orchestration/kubernetes/base/configmaps.yaml`, key `Encore Pub/Sub publish behavior` |
| FACT-014 | The current timeout hierarchy is explicit but not a complete external SLO contract. gateway downstream timeout defaults to `5s`; Market settlement wait defaults to `10s`. | Gap recorded | FACT-010, FACT-011, `11-performance-capacity-view.md` |
| FACT-020 | Encore Pub/Sub settlement work topic is `settlement-work`; settlement result topic is `settlement-results`. | Evidence-backed | `settlement/work.go`, symbols `WorkTopic` and `ResultTopic` |
| FACT-021 | Encore Pub/Sub settlement worker subscription name is `trade-settlement-executor`. | Evidence-backed | `settlementworker/service.go` |
| FACT-022 | Settlement subscription retry behavior is configured in code with 30s ack deadline, 2s minimum backoff, 2m maximum backoff, and 12 retries. Explicit DLQ routing is runtime/platform behavior, not a RabbitMQ exchange/queue contract in current code. | Evidence-backed with gap | `settlementworker/service.go`; operations DLQ runbook remains open |
| FACT-023 | Settlement worker subscription concurrency is configured as `8`. | Evidence-backed | `settlementworker/service.go`, constant `settlementSubscriptionConcurrency` |
| FACT-030 | Encore gateway exposes `/gateway/healthz` and `/gateway/readyz`. Runtime trade traffic enters through UDP/Quilkin; restored API gateway proto/gRPC contracts are internal contracts and do not move business decisions into the UDP edge. | Evidence-backed | `gateway/service.go`; `gateway/market_client.go`; `gateway/udp.go`; `proto/eve/api_gateway/v1/api_gateway.proto` |
| FACT-031 | Market exposes `/healthz` and `/readyz`; current readiness checks Market dependency initialization and PostgreSQL reachability. It does not prove a complete settlement worker/trade-settlement reply path. | Evidence-backed with gap | `market/api.go`, `06-deployment-operations-view.md` |
| FACT-032 | trade-settlement Kubernetes probes are TCP socket probes on port `9092`, not database-commit readiness checks. | Evidence-backed with gap | `distributed-backend/orchestration/kubernetes/base/trade-settlement.yaml` |
| FACT-040 | HMAC packet integrity is implemented at the UDP edge, but actor identity binding to authenticated account/capsuleer claims is not implemented end to end in application code. | Gap recorded | `07-security-trust-view.md`, `14-threat-model-view.md`, `15-risk-register.md` |
| FACT-041 | Generic settlement operations are high privilege; current controls are internal topology, NetworkPolicy, mesh placeholders, protovalidate settlement request validation, and row-level operation preconditions. No operation-provenance or operation-allow policy is implemented in trade-settlement. | Gap recorded | `07-security-trust-view.md`, `14-threat-model-view.md`, `15-risk-register.md` |
| FACT-042 | Production overlay contains placeholder host, image digests, and ACME email values; no checked-in gate rejects all placeholders. | Gap recorded | `distributed-backend/orchestration/kubernetes/overlay/prod`, `distributed-backend/orchestration/kubernetes/platform/gateway/prod` |
| FACT-044 | Production overlay includes Quilkin UDP resources and excludes simulator resources. | Evidence-backed | `distributed-backend/orchestration/kubernetes/overlay/prod/quilkin.yaml`, `scripts/verify_architecture_boundaries.py` |
| FACT-043 | Kubernetes database egress is broad TCP `5432` in current manifests without destination selection in NetworkPolicy. | Gap recorded | `06-deployment-operations-view.md`, `09-correspondences-rationale.md` |
| FACT-050 | Settlement metadata tables are the durable diagnostic source for idempotency, attempts, batches, steps, stored step outputs, and completed replay reconstruction. | Evidence-backed | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql` |
| FACT-053 | The current repository has one canonical settlement migration file; local Encore/Kubernetes and Kubernetes apply `0001_settlement_schema.sql` directly. | Evidence-backed | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql`, `Encore local run scripts`, `distributed-backend/orchestration/kubernetes/base/migrate.yaml` |
| FACT-054 | Item-stack ledger history is append-only and hash-chained per stack. Current `item_stack` rows are projections that must match the latest `item_stack_ledger` row, including creation, transfer, and merge effects. | Evidence-backed | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql`, `distributed-backend/src/trade-settlement/src/operations.rs` |
| FACT-051 | Market can load completed idempotency replay state by idempotency key. | Evidence-backed | `market/repository.go`, `LoadCompletedIdempotencyReplay` |
| FACT-052 | The documented recovery path for ambiguous outcomes is same-key retry/replay plus operator queries by idempotency key; there is no separate public outcome-query API. | Structurally represented | `04-functional-runtime-view.md`, `12-resilience-recovery-view.md` |
| FACT-060 | Observability view identifies request IDs, idempotency keys, settlement message IDs, settlement batch IDs, and failure reasons as needed correlation fields. | Structurally represented | `13-observability-view.md` |
| FACT-061 | Dashboards, alert thresholds, and incident queries are not yet verified as implemented artifacts. | Gap recorded | `13-observability-view.md`, `15-risk-register.md` |
| FACT-070 | Data retention, ledger growth, idempotency TTL, escrow cleanup, and archival policy are unresolved production architecture decisions. | Gap recorded | `05-information-data-integrity-view.md`, `15-risk-register.md` |
| FACT-080 | CI is configured to run strict protobuf, architecture boundary, Go, Rust, Python simulator, Terraform, Kubernetes, and local runtime/e2e gates. Local command results for v9 are recorded in `changes/v9/changes.md`. | Evidence-backed | `.github/workflows/verify.yaml`, `changes/v9/changes.md` |
| FACT-081 | Request-shape validation is centralized in protobuf through `buf.validate` annotations and reusable local predefined rules. | Evidence-backed | `proto/eve/validation/v1/validation_rules.proto`, `proto/eve/api_gateway/v1/api_gateway.proto`, `proto/eve/market/v1/market.proto`, `proto/eve/trade/v1/trade.proto`, `proto/eve/trade_settlement/v1/trade_settlement.proto` |
| FACT-082 | Go validation boundaries call `buf.build/go/protovalidate` for gateway UDP envelope/config/actor binding, Market GUI/typed RPC requests, game-trade input, and settlement worker request conversion. | Evidence-backed | `gateway/proto_validation.go`, `market/proto_validation.go`, `internal/gametrade/validation.go`, `settlementworker/convert.go` |
| FACT-083 | Rust trade-settlement validates incoming `ExecuteSettlementBatchRequest` messages with `prost-protovalidate` using the generated descriptor set. | Evidence-backed | `distributed-backend/src/trade-settlement/src/commands.rs`, `distributed-backend/src/trade-settlement/build.rs`, `distributed-backend/src/trade-settlement/src/proto.rs` |
| FACT-090 | The current infrastructure-as-code model has separate AWS/EKS, GCP/GKE, and Talos/Omni Terraform roots that feed the same Kubernetes application manifests. | Evidence-backed | `distributed-backend/terraform/eks`, `distributed-backend/terraform/gke`, `distributed-backend/terraform/talos-omni`, `ci-cd/pipeline.py` |

## Production Readiness Gates

| Gate ID | Gate | Current status | Blocks production readiness? |
| --- | --- | --- | --- |
| GATE-001 | Actor identity fields are bound to authenticated identity claims and tested. | Open | Yes |
| GATE-002 | Settlement API access risk has an accepted risk decision or implemented controls without moving Market-domain policy into trade-settlement. | Open | Yes |
| GATE-003 | Production placeholders are rejected by CI, admission policy, or release script. | Open | Yes |
| GATE-004 | Timeout hierarchy or async outcome-query contract is resolved and tested. | Open | Yes |
| GATE-005 | DLQ alerting, inspection, redrive, and discard runbooks are documented and tested. | Open | Yes |
| GATE-006 | PostgreSQL backup/restore RTO/RPO are defined and tested. | Open | Yes |
| GATE-007 | Runtime validation package passes for local Encore/Kubernetes, e2e, Kubernetes render, and relevant tests. | Enforced in CI; local result must be recorded per change | Yes until CI is green |
| GATE-008 | Stakeholder reviews for security, SRE, data integrity, QA, product, and integration are recorded. | Open | Yes |
| GATE-009 | Distributed edge replay behavior is implemented or explicitly accepted with durable idempotency evidence. | Open | Yes |

## Current Timeout Position

The current timeout values are documented facts, not an approved end-to-end
contract. The table below describes known patterns and their current repository
status; it is not an implemented contract.

| Pattern | Current repository status |
| --- | --- |
| Synchronous completion | Not defined as an external SLO contract in this repository. |
| Asynchronous outcome retrieval | Not implemented as a separate public outcome lookup API. |
| Hybrid | Closest to current documented behavior: fast replies return synchronously; ambiguous outcomes use same-key retry/replay and operator reconciliation. |

Current status: hybrid behavior is described, but the formal outcome-query API
is not implemented as a separate contract. Same-key retry and operator
reconciliation remain the documented recovery path.

## Secret Inventory Facts

| Secret | Consumers | Current source | Documented owner | Rotation status | Current status |
| --- | --- | --- | --- | --- | --- |
| `DATABASE_URL` | Market, trade-settlement, migration job | Kubernetes Secret or local Encore/Kubernetes env | SRE/database owner | Rotation policy not defined in repo | Gap recorded |
| Encore Pub/Sub infrastructure credentials/configuration | Encore Pub/Sub, Market, settlement worker | Kubernetes Secret or local Encore/Kubernetes env | SRE/platform owner | Per-service rotation policy not defined in repo | Gap recorded |
| `GAME_PACKET_HMAC_SECRET` | Encore gateway UDP edge | Production secret manager or Kubernetes Secret | Security/platform owner | Rotation policy not defined in repo | Gap recorded |
| Observability API keys | OTEL/Honeycomb/Sentry components | Out-of-band secret | Observability owner | Rotation policy not defined in repo | Gap recorded |

## Data Classification Facts

| Data class | Examples | Classification | Handling rule |
| --- | --- | --- | --- |
| Actor-linked game identity | Capsuleer IDs, actor fields, ownership fields | Sensitive operational player-linked data | Avoid logging more than required; correlate by IDs only where needed for diagnosis. |
| Financial/inventory state | Wallet balances, item stacks, escrow, ledgers | High-integrity business state | Current repository routes durable mutation through trade-settlement, preserves append-only wallet ledger history, and preserves hash-chained append-only item-stack ledger history. |
| Idempotency and request metadata | Idempotency keys, external request IDs, request fingerprints | Sensitive operational metadata | Scope by actor, retain long enough for retry/diagnosis, and define retention before production. |
| Telemetry | Trace IDs, logs, errors, settlement message IDs | Operational data | Redact secrets and avoid payload logging unless explicitly approved. |
| Secrets | Database, broker, telemetry, identity configuration | Secret | Store in approved secret manager or Kubernetes Secret with controlled access and rotation. |
