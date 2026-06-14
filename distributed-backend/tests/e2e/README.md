# E2E Tests

These tests exercise the project through the same boundaries production uses:
protobuf contracts, gRPC streams, PostgreSQL state, and the trade instance
lifecycle.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r distributed-backend\tests\e2e\requirements.txt
```

## Run

Start PostgreSQL and the services first, then run:

```powershell
pytest distributed-backend\tests\e2e
```

The test harness generates Python protobuf stubs into `.pytest_cache` at runtime,
so generated files are not committed.

For the production-style disposable integration environment, use the Dagger
entrypoint:

```powershell
python ci-cd\pipeline.py integration
```

That entrypoint sets `EVE_TRADE_E2E_PRODUCTION_GATE=true`. In production-gate
mode pytest fails if the suite collects zero live tests, omits gateway/market/
settlement live coverage, skips a live test, or enables local skip mode.

## Environment

The defaults match the local integration stack:

```powershell
$env:EVE_TRADE_DATABASE_URL = "postgres://postgres:postgres@localhost:5432/eve_trade_e2e"
$env:EVE_TRADE_SETTLEMENT_GRPC = "localhost:9092"
$env:EVE_TRADE_MARKET_GRPC = "localhost:8081"
$env:EVE_TRADE_GATEWAY_GRPC = "localhost:8080"
```

Live tests fail when PostgreSQL or a required service endpoint is missing. For
local exploratory runs only, set `EVE_TRADE_E2E_ALLOW_SKIPS=true` to skip
unavailable live dependencies.

The harness does not reset the database per test. Test data uses unique row IDs
so the suite can run repeatedly and in parallel against a disposable database.
To clear mutable tables once at session startup, set
`EVE_TRADE_RESET_DATABASE=true`. Destructive reset is refused unless the
database name contains `e2e`, `test`, `testing`, or `ci`, or
`EVE_TRADE_ALLOW_DESTRUCTIVE_DB_RESET=true` is explicitly set.

Failed live tests write JSON diagnostics under
`.pytest_cache/eve_trade_e2e_artifacts` by default, or
`EVE_TRADE_E2E_ARTIFACT_DIR` when set. The Dagger integration gate writes them
to `ci-cd/out/e2e-artifacts`.

## Governance

- `docs/coverage_matrix.md` maps the canonical lifecycle `@` steps to tests.
- `docs/taxonomy.md` defines what belongs in e2e, contract, integration, and
  chaos coverage.
- Contract tests fail if the matrix no longer mentions every canonical
  lifecycle step or if the CI integration path stops enforcing production-gate
  mode.
