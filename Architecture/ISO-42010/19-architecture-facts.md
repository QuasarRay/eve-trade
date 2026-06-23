# Architecture Facts Catalog

## Metadata

| Field | Value |
| --- | --- |
| Catalog ID | `FACTCAT-EVE-TRADE-001` |
| Date | 2026-06-23 |
| Status | Canonical central fact catalog |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

## Purpose

This catalog centralizes facts that are repeated across runtime, performance,
resilience, deployment, observability, security, and risk views. Other views
can reference these fact IDs rather than duplicating inconsistent prose.

## Fact Register

| Fact ID | Fact | Current status | Primary evidence anchors |
| --- | --- | --- | --- |
| FACT-001 | The architecture document baseline is content-addressed by file hashes until the architecture set is committed. | Evidence-backed | `18-evidence-manifest.md` |
| FACT-002 | The source commit inspected for implementation evidence is `fe5c6af1dcb68715ccb339a00912729a4febdf2d`. | Evidence-backed | Git `HEAD` at review time |
| FACT-010 | API Gateway downstream timeout defaults to `5s`. | Evidence-backed | `distributed-backend/src/api-gateway/distributed-backend/config.go`, config key `API_GATEWAY_DOWNSTREAM_TIMEOUT` |
| FACT-011 | Market settlement request timeout defaults to `10s`. | Evidence-backed | `distributed-backend/src/market/distributed-backend/config.go`, key `MARKET_SETTLEMENT_REQUEST_TIMEOUT` |
| FACT-012 | settlement-worker request timeout is configured as `10s` in Kubernetes base config. | Evidence-backed | `distributed-backend/orchestration/kubernetes/base/configmaps.yaml`, key `SETTLEMENT_WORKER_REQUEST_TIMEOUT` |
| FACT-013 | RabbitMQ publish timeout is configured as `5s` in Kubernetes base config. | Evidence-backed | `distributed-backend/orchestration/kubernetes/base/configmaps.yaml`, key `RABBITMQ_PUBLISH_TIMEOUT` |
| FACT-014 | The current timeout hierarchy is inconsistent for a synchronous caller contract because API Gateway can time out before Market settlement wait completes. | Gap recorded | FACT-010, FACT-011, `11-performance-capacity-view.md` |
| FACT-020 | RabbitMQ command exchange is `eve.trade.settlement`. | Evidence-backed | `distributed-backend/src/messaging/rabbitmqsettlement/config.go`, `DefaultExchange`; Kubernetes configmaps |
| FACT-021 | RabbitMQ command queue is `eve.trade.settlement.commands`. | Evidence-backed | `distributed-backend/src/messaging/rabbitmqsettlement/config.go`, `DefaultCommandQueue`; Kubernetes configmaps |
| FACT-022 | RabbitMQ DLX is `eve.trade.settlement.dlx`; DLQ is `eve.trade.settlement.dead`; dead-letter routing key is `settlement.dead`. | Evidence-backed | `distributed-backend/src/messaging/rabbitmqsettlement/config.go`; Kubernetes configmaps |
| FACT-023 | Worker prefetch is configured as `8` in Kubernetes base config. | Evidence-backed | `distributed-backend/orchestration/kubernetes/base/configmaps.yaml`, key `RABBITMQ_SETTLEMENT_PREFETCH` |
| FACT-030 | API Gateway exposes `/healthz` and `/readyz`; readiness checks Market. | Evidence-backed | `distributed-backend/src/api-gateway/distributed-backend/server.go`; `market_client.go` |
| FACT-031 | Market exposes `/healthz` and `/readyz`; current readiness checks PostgreSQL and, for RabbitMQ transport, the RabbitMQ client session. It does not prove a complete settlement-worker/trade-settlement reply path. | Evidence-backed with gap | `distributed-backend/src/market/distributed-backend/server.go`, `distributed-backend/src/market/cmd/market/main.go`, `distributed-backend/src/messaging/rabbitmqsettlement/client.go`, `06-deployment-operations-view.md` |
| FACT-032 | trade-settlement Kubernetes probes are TCP socket probes on port `9092`, not database-commit readiness checks. | Evidence-backed with gap | `distributed-backend/orchestration/kubernetes/base/trade-settlement.yaml` |
| FACT-040 | Actor identity binding to authenticated claims is not implemented end to end in application code. | Gap recorded | `07-security-trust-view.md`, `14-threat-model-view.md`, `15-risk-register.md` |
| FACT-041 | Generic settlement operations are high privilege; current controls are internal topology, NetworkPolicy, mesh placeholders, settlement envelope validation, and row-level operation preconditions. No operation-provenance or operation-allow policy is implemented in trade-settlement. | Gap recorded | `07-security-trust-view.md`, `14-threat-model-view.md`, `15-risk-register.md` |
| FACT-042 | Production overlay contains placeholder host, issuer/JWKS/audience, image digests, and ACME email values; no checked-in gate rejects them. | Gap recorded | `distributed-backend/orchestration/kubernetes/overlay/prod`, `distributed-backend/orchestration/kubernetes/platform/gateway/prod` |
| FACT-043 | Kubernetes database egress is broad TCP `5432` in current manifests without destination selection in NetworkPolicy. | Gap recorded | `06-deployment-operations-view.md`, `09-correspondences-rationale.md` |
| FACT-050 | Settlement metadata tables are the durable diagnostic source for idempotency, attempts, batches, steps, stored step outputs, and completed replay reconstruction. | Evidence-backed | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql` |
| FACT-053 | The current repository has one canonical settlement migration file; Compose and Kubernetes apply `0001_settlement_schema.sql` directly. | Evidence-backed | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql`, `compose.yaml`, `distributed-backend/orchestration/kubernetes/base/migrate.yaml` |
| FACT-051 | Market can load completed idempotency replay state by idempotency key. | Evidence-backed | `distributed-backend/src/market/distributed-backend/repository.go`, `LoadCompletedIdempotencyReplay` |
| FACT-052 | The documented recovery path for ambiguous outcomes is same-key retry/replay plus operator queries by idempotency key; there is no separate public outcome-query API. | Structurally represented | `04-functional-runtime-view.md`, `12-resilience-recovery-view.md` |
| FACT-060 | Observability view identifies request IDs, idempotency keys, RabbitMQ correlation IDs, settlement batch IDs, and failure reasons as needed correlation fields. | Structurally represented | `13-observability-view.md` |
| FACT-061 | Dashboards, alert thresholds, and incident queries are not yet verified as implemented artifacts. | Gap recorded | `13-observability-view.md`, `15-risk-register.md` |
| FACT-070 | Data retention, ledger growth, idempotency TTL, escrow cleanup, and archival policy are unresolved production architecture decisions. | Gap recorded | `05-information-data-integrity-view.md`, `15-risk-register.md` |
| FACT-080 | Static/unit/render validation was run for this update; live e2e trade-flow execution was skipped because live service/database URLs were not configured. | Partially verified | `18-evidence-manifest.md`, root `changes.md` |

## Production Readiness Gates

| Gate ID | Gate | Current status | Blocks production readiness? |
| --- | --- | --- | --- |
| GATE-001 | Actor identity fields are bound to authenticated identity claims and tested. | Open | Yes |
| GATE-002 | Settlement API access risk has an accepted risk decision or implemented controls without moving Market-domain policy into trade-settlement. | Open | Yes |
| GATE-003 | Production placeholders are rejected by CI, admission policy, or release script. | Open | Yes |
| GATE-004 | Timeout hierarchy or async outcome-query contract is resolved and tested. | Open | Yes |
| GATE-005 | DLQ alerting, inspection, redrive, and discard runbooks are documented and tested. | Open | Yes |
| GATE-006 | PostgreSQL backup/restore RTO/RPO are defined and tested. | Open | Yes |
| GATE-007 | Runtime validation package passes for Compose, e2e, Kubernetes render, and relevant tests. | Open | Yes |
| GATE-008 | Stakeholder reviews for security, SRE, data integrity, QA, product, and integration are recorded. | Open | Yes |

## Current Timeout Position

The current timeout values are documented facts, not an approved end-to-end
contract. The table below describes known patterns and their current repository
status; it is not an implemented contract.

| Pattern | Current repository status |
| --- | --- |
| Synchronous completion | Not satisfied by current defaults because API Gateway can time out before Market settlement wait completes. |
| Asynchronous outcome retrieval | Not implemented as a separate public outcome lookup API. |
| Hybrid | Closest to current documented behavior: fast replies return synchronously; ambiguous outcomes use same-key retry/replay and operator reconciliation. |

Current status: hybrid behavior is described, but the formal outcome-query API
is not implemented as a separate contract. Same-key retry and operator
reconciliation remain the documented recovery path.

## Secret Inventory Facts

| Secret | Consumers | Current source | Documented owner | Rotation status | Current status |
| --- | --- | --- | --- | --- | --- |
| `DATABASE_URL` | Market, trade-settlement, migration job | Kubernetes Secret or Compose env | SRE/database owner | Rotation policy not defined in repo | Gap recorded |
| RabbitMQ username/password/URL | RabbitMQ, Market, settlement-worker | Kubernetes Secret or Compose env | SRE/platform owner | Per-service rotation policy not defined in repo | Gap recorded |
| JWT issuer/JWKS/audience | Istio RequestAuthentication | Production overlay patch values | Security/platform owner | External identity-provider lifecycle, not defined here | Placeholder gate open |
| Observability API keys | OTEL/Honeycomb/Sentry components | Out-of-band secret | Observability owner | Rotation policy not defined in repo | Gap recorded |

## Data Classification Facts

| Data class | Examples | Classification | Handling rule |
| --- | --- | --- | --- |
| Actor-linked game identity | Capsuleer IDs, actor fields, ownership fields | Sensitive operational player-linked data | Avoid logging more than required; correlate by IDs only where needed for diagnosis. |
| Financial/inventory state | Wallet balances, item stacks, escrow, ledgers | High-integrity business state | Current repository routes durable mutation through trade-settlement and preserves append-only wallet/item ledger history. |
| Idempotency and request metadata | Idempotency keys, external request IDs, request fingerprints | Sensitive operational metadata | Scope by actor, retain long enough for retry/diagnosis, and define retention before production. |
| Telemetry | Trace IDs, logs, errors, RabbitMQ correlation IDs | Operational data | Redact secrets and avoid payload logging unless explicitly approved. |
| Secrets | Database, broker, telemetry, identity configuration | Secret | Store in approved secret manager or Kubernetes Secret with controlled access and rotation. |
