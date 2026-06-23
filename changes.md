# Changes

Date: 2026-06-24

## Item Stack Ledger Immutability

- Changed the settlement schema so `item_stack_ledger` is not a mergeable audit table. It is now an append-only, per-stack hash chain with `ledger_sequence`, previous hash, payload hash, row hash, before/after stack state, before/after quantity, before/after version, and before/after checksum fields.
- Added SQL hash functions and triggers that reject item-ledger rows that do not extend the latest row for the same stack.
- Added deferred SQL projection triggers requiring every current `item_stack` row to match the latest `item_stack_ledger` row for owner, item type, station, quantity, state, version, and checksum.
- Preserved the existing merge behavior for item stacks by appending separate `MERGE_OUT` and `MERGE_IN` ledger rows. Existing ledger rows are not updated, deleted, combined, or rewritten.
- Added initial `CREATE_STACK` item-ledger writes when a new empty item stack is created.
- Updated local development seed data so seeded item stacks also receive initial `CREATE_STACK` ledger rows in the same transaction.
- Synced the Kubernetes migration copy with the canonical trade-settlement migration.

## Documentation

- Updated the ISO 42010 architecture records to describe the current implementation: append-only hash-chained item ledgers, item-stack projection invariants, create-stack ledger rows, merge ledger behavior, and updated evidence/correspondence facts.

## Validation

- `cargo fmt --all -- --check` passed for `distributed-backend/src/trade-settlement`.
- `cargo check --locked` passed for `distributed-backend/src/trade-settlement`.
- `cargo test --locked` passed for `distributed-backend/src/trade-settlement`; the crate currently has zero tests.
- `kubectl kustomize distributed-backend/orchestration/kubernetes/base` passed.
- `kubectl kustomize distributed-backend/orchestration/kubernetes/overlay/prod` passed.
- `docker compose config --quiet` passed.
- The canonical migration and Kubernetes migration copy were compared and matched.
- Live PostgreSQL migration execution was not run locally because Docker Desktop was not running and `psql`/PostgreSQL server binaries were not installed in the environment.
