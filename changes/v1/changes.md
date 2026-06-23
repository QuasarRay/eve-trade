# Changes

Date: 2026-06-23

## ISO Schema Documentation

- Expanded `Architecture/ISO-42010/05-information-data-integrity-view.md` with
  the full current persistent schema details from the conceptual schema and the
  SQL migration.
- Documented table columns, keys, foreign keys, state values, indexes, ledger
  append-only triggers, settlement step output storage, item stack merge states,
  and closed-trade escrow invariants.
- Updated ISO facts, deployment, context, risk, and evidence records for the
  current single-migration and readiness behavior.

## Migration Consolidation

- Consolidated settlement database migrations into
  `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql`.
- Removed `0002_merge_item_stack_constraints.sql` from the source migration set
  and Kubernetes migration copy.
- Preserved the item stack merge behavior in `0001`: `MERGED` stack state plus
  `MERGE_IN` and `MERGE_OUT` item ledger kinds.
- Updated Compose, integration Compose, Kubernetes migration manifests, and the
  migration-copy test to use only `0001_settlement_schema.sql`.

## Implementation Fixes

- Persisted trade-settlement operation outputs in `settlement_step.step_output`
  and replay them on completed idempotency requests.
- Added Market request fingerprints to settlement requests and completed replay
  matching so replay compares the original Market protobuf request for new
  Market-originated records.
- Rejected inactive source and destination item stacks in Market before
  settlement planning.
- Mapped Market replay-load repository failures to `Unavailable`.
- Added SQL closed-trade wallet escrow invariants.
- Changed Market's default settlement transport to `rabbitmq`.
- Added RabbitMQ client-session readiness to Market readiness and changed
  Compose healthchecks for Market/API Gateway to `/readyz`.

## Audit Files

- Rewrote root `flaws.md` to separate fixed flaws from remaining open
  architecture/design and implementation flaws.
- Added this root `changes.md` to record the change set and validation.

## Validation

- `go test ./...`: passed.
- `go test ./...` in `distributed-backend/src/market`: passed.
- `go vet ./...`: passed at the repository root.
- `go vet ./...` in `distributed-backend/src/market`: passed.
- `go vet ./...` in `distributed-backend/src/messaging`: passed.
- `go vet ./...` in `distributed-backend/src/settlement-worker`: passed.
- `cargo test --locked`: passed in `distributed-backend/src/trade-settlement`.
- `cargo fmt --check`: passed in `distributed-backend/src/trade-settlement`.
- `cargo clippy --locked -- -D warnings`: passed in
  `distributed-backend/src/trade-settlement`.
- `docker compose config --quiet`: passed.
- `docker compose -f docker-compose.integration.yml config --quiet`: passed.
- `kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod > $null`: passed.
- `python -m pytest distributed-backend/tests/e2e -q` with
  `EVE_TRADE_E2E_ALLOW_ALL_SKIPPED=true`: passed with 109 skipped because live
  service/database URLs were not configured.
