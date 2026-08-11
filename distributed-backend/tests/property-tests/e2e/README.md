# EVE Trade Property Contracts

This package exposes every exact contract in `../tests-to-implement.md`, but collection is not the same as semantic implementation. The canonical route, implementation status, prerequisite, observable, and blocker for every name are generated in `../infra/test-requirements.json`.

The current boundary is:

```text
Hypothesis case
  -> direct live system, repository oracle, Litmus driver, or external platform driver
  -> raw observations tied to a fresh invocation
  -> repository-owned Python oracle
```

Contracts without an independent named oracle are marked `eve_unimplemented`. Developer mode skips them with their exact blocker; strict mode fails. The suite does not route those names to generic success-shaped evidence.

## Install and structural verification

From the repository root:

```bash
python -m pip install -r distributed-backend/tests/e2e/requirements.txt \
  -r distributed-backend/tests/property-tests/e2e/requirements.txt
python distributed-backend/tests/property-tests/e2e/tools/sync_authoritative_catalog.py --check
python distributed-backend/tests/property-tests/infra/generate_contract_manifests.py --check
python distributed-backend/tests/property-tests/e2e/tools/generate_hypothesis_audit.py --check
python -m pytest -q distributed-backend/tests/property-tests/e2e/tests
python distributed-backend/tests/property-tests/infra/verify_collection.py
```

These commands derive counts from the current Markdown and collected nodes. No README count is authoritative.

## Running contracts

Development mode reports unavailable capabilities as skips:

```bash
python -m pytest -q distributed-backend/tests/property-tests/e2e
```

Strict mode turns missing services, drivers, and unimplemented semantic oracles into failures:

```bash
python -m pytest -q distributed-backend/tests/property-tests/e2e --eve-hypothesis-strict
```

Live tests use the repository's native E2E helpers and require the same PostgreSQL, simulator, gateway, Quilkin, NSQ, settlement, and credential environment. Destructive reset is guarded by a PostgreSQL advisory lock, and cleanup errors fail the test.

Litmus execution is owned by the Dagger pipeline in `../infra/`; do not point the fault driver at an arbitrary cluster. The driver requires an exact per-run namespace label and safety ConfigMap and rejects production/staging/live-looking contexts.

## Generated and hand-authored boundaries

- `eve_trade_hypothesis/generated/`, `catalog.json`, `evidence_specs.json`, and the JSON audit/coverage manifests are generated.
- `registry.py`, `engine.py`, `external.py`, `evidence_integrity.py`, `chaos_oracles.py`, adapters, strategies, and contract runners are hand-authored behavior.
- `HYPOTHESIS_AUDIT.json` is the exhaustive finding record; `HYPOTHESIS_AUDIT.md` is its compact human view.
- `../infra/litmus-contracts.json` is the complete chaos prerequisite model.

## Evidence rule

A matching case hash only binds a label. Protocol v3 also requires a fresh run/invocation/nonce challenge, bounded timestamps, a unique evidence ID, raw observations, exact generated-parameter application, and an independent oracle. Litmus evidence additionally requires a raw ChaosEngine/ChaosResult identity, a fault-family-specific physical target effect, at least two sources, recomputed workload/fault overlap, bounded recovery, and verified cleanup.
