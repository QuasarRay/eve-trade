# Eve Trade Test Taxonomy

## E2E

E2E tests run through protobuf/gRPC boundaries and verify PostgreSQL truth for
the lifecycle. They own:

- GameUI activity to gateway response behavior.
- Market lifecycle decision behavior.
- Settlement database mutation and no-mutation invariants.
- Idempotency, concurrency, streaming, schema readiness, and observability
  breadcrumbs that can be proven from public service surfaces.

## Contract

Contract tests run without services. They own:

- Proto descriptor stability.
- Field number and enum value stability.
- Static architecture coverage matrix checks.
- Static CI gate checks.
- Conceptual schema and migration compatibility checks.

## Integration Gate

`python ci-cd/pipeline.py integration` is the production e2e gate. It must run
the full e2e tree with `EVE_TRADE_E2E_PRODUCTION_GATE=true` against a disposable
`eve_trade_e2e` database and all three services.

## Chaos

Chaos tests live under the Litmus/Dagger chaos pipeline. They own restarts,
network partitions, DNS faults, partial outages, and timeout-after-commit cases.
Do not duplicate those as ordinary e2e tests unless the services expose a
deterministic test-only fault injection hook.

## Reviewer Checklist

- Does the test name identify the lifecycle behavior being protected?
- Does the test fail on rejected or unknown outcomes unless that is the behavior
  under test?
- Does every accepted mutation assert final database truth?
- Does every rejected path assert no durable mutation?
- Does the change preserve the coverage matrix and taxonomy rules?
- Does production gate mode still fail if live tests are skipped or deselected?
