# Project Flaws Audit

Audit date: 2026-06-23

Scope: current repository state under `C:\Users\Astral\Desktop\eve-trade`.
This file records the flaws that remain after the database/ISO/migration repair
pass, plus the flaws from the prior audit that were fixed in this pass.

## Validation Performed

- `go test ./...`: passed.
- `go test ./...` in `distributed-backend/src/market`: passed.
- `go vet ./...`: passed at the repository root.
- `go vet ./...` in `distributed-backend/src/market`: passed.
- `go vet ./...` in `distributed-backend/src/messaging`: passed.
- `go vet ./...` in `distributed-backend/src/settlement-worker`: passed.
- `cargo test --locked` in `distributed-backend/src/trade-settlement`: passed; the crate still has 0 Rust tests.
- `cargo fmt --check`: passed.
- `cargo clippy --locked -- -D warnings`: passed.
- `docker compose config --quiet`: passed.
- `docker compose -f docker-compose.integration.yml config --quiet`: passed.
- `kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod > $null`: passed.
- `python -m pytest distributed-backend/tests/e2e -q` with `EVE_TRADE_E2E_ALLOW_ALL_SKIPPED=true`: passed with 109 skipped because live service and database URLs were not configured.

## Fixed In This Pass

### F-001: ISO data view lacked conceptual schema detail

Status: fixed.

`Architecture/ISO-42010/05-information-data-integrity-view.md` now includes the
table-by-table persistent schema details from the conceptual schema and current
SQL migration: columns, primary keys, foreign keys, state values, indexes,
append-only ledger triggers, escrow invariants, and migration behavior.

### F-002: Settlement migrations drifted from the single-file architecture

Status: fixed for the current repository.

The settlement database migration set is now a single canonical file:
`distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql`.
The removed `0002_merge_item_stack_constraints.sql` behavior was preserved in
that file: `item_stack.stack_state = MERGED`, `MERGE_IN` and `MERGE_OUT` item
ledger kinds, and related compatibility constraint updates.

Compose, integration Compose, Kubernetes migration job/configmap, and the
Kubernetes migration-copy test now refer only to `0001_settlement_schema.sql`.

### F-003: Settlement replay returned empty step outputs

Status: fixed.

`settlement_step.step_output` is now stored as JSONB. The trade-settlement
executor writes operation outputs when completing each step and reloads those
outputs on completed idempotency replay.

### F-004: Market completed replay used partial request matching

Status: fixed for new Market-originated records; compatible fallback remains
for older operation-hash records.

Market now sends a deterministic `market.<request_kind>.sha256:<hash>`
fingerprint of the original Market protobuf request through the existing
settlement `request_fingerprint` field. Completed replay compares that stored
fingerprint before returning a cached response. Tests cover a changed request
field that the old operation-payload checks did not compare.

### F-005: Market replay-load storage errors were reported as business failures

Status: fixed.

`loadReplay` now maps repository errors to `connect.CodeUnavailable`.

### F-006: Market let inactive item stacks reach settlement planning

Status: fixed.

Market now rejects inactive source item stacks on issue and inactive destination
item stacks on accept before planning settlement operations.

### F-007: Closed trades could retain active wallet escrow at the database layer

Status: fixed.

`0001_settlement_schema.sql` now includes deferrable SQL constraint triggers
that prevent `CANCELLED` or `COMPLETED` trades from retaining active wallet
escrow. This complements the existing active item escrow invariant.

### F-008: Market default transport could bypass the RabbitMQ topology

Status: fixed.

Market now defaults `SETTLEMENT_TRANSPORT` to `rabbitmq`. Explicit `connect`,
`grpc`, and `direct` transports still exist for alternate/local deployment
choices.

### F-009: Compose dependency checks used process liveness for Market/API Gateway

Status: fixed.

`compose.yaml` and `docker-compose.integration.yml` now use `/readyz` for Market
and API Gateway healthchecks.

### F-010: Market readiness only checked PostgreSQL

Status: partially fixed.

Market readiness now checks PostgreSQL and, when the RabbitMQ transport is
active, the RabbitMQ client session. It still does not prove that
settlement-worker and trade-settlement can complete a full request/reply cycle.
That residual gap remains open below.

## Open Architecture And Design Flaws

### A-001: Actor identity is caller-supplied, not bound to authenticated claims

Severity: Critical

The game-facing protobuf messages carry `issued_by_capsuleer_id`,
`buyer_capsuleer_id`, and `cancelled_by_capsuleer_id` as request fields. Market
checks those fields against database ownership snapshots, but application code
does not bind them to authenticated identity claims. Production Istio JWT
resources still contain environment-specific placeholders.

Impact: a caller accepted by the edge policy can assert another capsuleer ID
unless an external system performs binding before the request reaches this
service.

### A-002: The settlement API is a privileged generic operation executor

Severity: Critical

`TradeSettlementService.ExecuteSettlementBatch` accepts low-level operations and
executes them atomically after envelope and row-level precondition checks.
trade-settlement does not implement operation provenance, caller-specific
operation allow rules, or Market-intent authorization.

This matches the current design decision that trade-settlement atomically
performs requested transactions and does not own Market domain policy. The
remaining flaw is that the security model depends heavily on Market, RabbitMQ,
settlement-worker, mesh policy, and NetworkPolicy remaining correct.

### A-003: The RabbitMQ path is operationally asynchronous but caller-visible synchronous

Severity: High

Market publishes through RabbitMQ and waits for a settlement reply. There is no
public outcome lookup/status API in Market or API Gateway.

Impact: a timeout, lost reply, or client disconnect can leave the caller with an
ambiguous result while settlement may have committed. Recovery depends on
same-key retry/replay or operator inspection of settlement metadata.

### A-004: Timeout budgets are not end-to-end coherent

Severity: High

API Gateway defaults its downstream timeout to 5 seconds while Market and the
RabbitMQ settlement path default to 10 seconds.

Impact: API Gateway can time out before Market's settlement wait finishes,
creating caller-visible failures while the internal transaction may still
commit.

### A-005: Market is coupled directly to the settlement database schema

Severity: High

Market reads item, wallet, trade, escrow, and idempotency state directly from
PostgreSQL through `PostgresTradeRepository`. Kubernetes gives Market the same
database secret family used by settlement components.

Impact: Market and settlement share a schema and credential boundary. Settlement
schema changes can break Market reads, and least-privilege database roles are
not represented in checked-in manifests.

### A-006: Readiness still does not prove the full trade path is usable

Severity: High

Market now checks PostgreSQL and RabbitMQ client-session readiness, and Compose
uses `/readyz`, but Market readiness does not prove settlement-worker can
consume a command or that trade-settlement can execute a transaction.
trade-settlement Kubernetes readiness remains a TCP socket probe.

Impact: orchestration can still route traffic when the end-to-end
issue/accept/cancel settlement path is unavailable.

### A-007: Production placeholder values are renderable

Severity: High

The production overlay still contains placeholder issuer/JWKS/audience values,
example hostnames, placeholder image digests, and documented manual replacement
requirements. Rendering the overlay succeeds.

Impact: production-looking manifests can be generated without real identity,
image, or hostname values unless an external release gate catches them.

### A-008: Expiration is a request-time check, not a lifecycle process

Severity: Medium

Market rejects accepts for expired trades and the schema indexes open
`expires_at` values. There is no durable `EXPIRED` state, expiration worker,
cleanup process, or public reporting API.

Impact: expired trades remain `OPEN` at rest and continue holding item escrow
until cancelled or otherwise handled.

### A-009: Database egress policies are broad by destination

Severity: Medium

Production NetworkPolicies allow selected workloads to egress to TCP 5432
without a destination selector.

Impact: NetworkPolicy restricts the port, but not the database endpoint.

### A-010: Single-file migration governance has no production history model

Severity: Medium

The current repository intentionally has one canonical settlement migration
file, per the current architecture request. That file is idempotent for the
current schema and local compatibility behavior, but there is still no
production-grade applied-migration history, checksum verification, rollback
model, or multi-version upgrade process.

Impact: the current single-file model is aligned with the docs and repository,
but future production schema evolution will need a governance decision before
claims about safe multi-version migrations are made.

### A-011: Operational runbooks and alert artifacts are not implemented artifacts

Severity: Medium

The repo has OpenTelemetry configuration and observability docs, but verified
dashboards, alert thresholds, DLQ redrive procedures, idempotency replay
procedures, and incident queries remain documented gaps.

Impact: failure recovery still depends on maintainer knowledge and manual
database or broker inspection.

## Open Implementation Flaws

### I-001: Wallet escrow is not tied to the item recipient in settlement operations

Severity: High

`TransferQuantityFromItemStackEscrowToItemStackWithNewOwner` validates that the
destination item stack belongs to a different owner than the escrow owner and
that active wallet escrow for the trade matches price times quantity. It does
not require the active wallet escrow owner to match the destination item owner.

Impact: a privileged generic operation sequence can release items to one
capsuleer using wallet escrow funded by another, outside the normal
Market-generated sequence.

### I-002: Wallet escrow release is not tied to the trade issuer

Severity: High

`TransferIskAmountFromWalletEscrowToWalletWithNewOwner` validates that the
destination wallet owner differs from the escrow owner, but not that the
destination wallet belongs to the trade issuer.

Impact: a privileged generic operation sequence can pay a third party if it is
submitted directly to settlement.

### I-003: trade-settlement Kubernetes readiness is TCP-only

Severity: Medium

The trade-settlement Deployment uses TCP startup/readiness/liveness probes on
the gRPC port. A TCP accept does not prove SQLx can acquire a connection or that
a minimal settlement transaction can commit.

Impact: Kubernetes can send traffic to a trade-settlement pod whose database
path is not usable.

### I-004: Critical Rust settlement logic has no direct tests in the crate

Severity: Medium

`cargo test --locked` reports 0 tests for `lib.rs`, 0 tests for `main.rs`, and 0
doc tests in `distributed-backend/src/trade-settlement`.

Impact: executor savepoint behavior, operation invariants, replay output
behavior, and edge-case operation sequences depend on indirect Go/e2e coverage
or manual validation.

### I-005: The e2e suite is not runnable by default

Severity: Medium

`distributed-backend/tests/e2e/conftest.py` skips unless
`EVE_TRADE_API_GATEWAY_URL` and `EVE_TRADE_DATABASE_URL` are set. This audit run
produced 109 skipped tests.

Impact: the most behavior-rich tests do not validate the project unless a
separate live environment is already running and configured.

### I-006: Production placeholder checks are documented but not enforced by passing checks

Severity: Medium

`kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod`
passes despite placeholder production values. Existing static checks do not
reject example identity values, hostnames, or placeholder digests.

Impact: current static checks can pass while manifests still require production
substitution.

## Non-Findings And Constraints

- The current Go, Rust, Compose, and Kubernetes render checks listed above pass.
- The normal Market-generated issue/accept/cancel flows remain the intended way
  to construct settlement batches.
- The remaining settlement API privilege flaws are documented as current design
  and security risks; fixing them would require a product/security decision
  about whether to keep settlement as a generic atomic executor or add
  operation-level policy.
