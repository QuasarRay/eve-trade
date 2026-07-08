# Eve Trade observability control plane

This directory records CI command evidence, pytest failures, Kubernetes state, PostgreSQL snapshots, Go/Rust telemetry, Honeycomb investigations, and concise Sentry issues under one `observability.run_id`.

## Quick Start

```powershell
python observability/ci/observed_run.py check
python observability/ci/observed_run.py test
python observability/ci/observed_run.py collect-only
```

Run E2E against an already started Encore/Kubernetes environment:

```powershell
$env:EVE_TRADE_ENCORE_URL="http://127.0.0.1:4000"
$env:EVE_TRADE_SIMULATOR_URL="http://127.0.0.1:8000"
$env:EVE_TRADE_DATABASE_URL="postgres://postgres:postgres@127.0.0.1:5432/eve_trade"
python -m pip install -r observability/requirements.txt
python observability/ci/observed_run.py e2e --maxfail 1
```

The command prints its generated run directory under `.o11y/runs/`. Every run starts with `run-context.json`, `git.json`, `tool-versions.json`, `env-redacted.json`, `hashes.json`, and local span JSON.

`--clean` is accepted for old command compatibility but does not delete resources. Reset databases and runtime environments explicitly.

## Telemetry

The local collector config accepts OTLP on `4317` and `4318` and writes bounded local JSON output. Run it through Kubernetes, the collector binary, or another local supervisor. The Honeycomb config keeps the same local copy behavior for CI parity.

The Encore backend and Rust `trade-settlement` use OTLP and W3C trace context. Domain spans preserve UDP ingress, Market validation and settlement planning, settlement worker completion/failure, and Rust transaction/rollback details.

## Failure Reports

Set `HONEYCOMB_API_KEY`, `HONEYCOMB_DATASET`, and `SENTRY_DSN` when external telemetry is desired. Missing Honeycomb, Sentry, Kubernetes, database, and S3 integrations are recorded as evidence gaps and fail only in strict mode.

See [Observability-Control-Plane.md](docs/Observability-Control-Plane.md), [Failure-Triage-Runbook.md](docs/Failure-Triage-Runbook.md), and [Local-vs-CI-Parity.md](docs/Local-vs-CI-Parity.md).
