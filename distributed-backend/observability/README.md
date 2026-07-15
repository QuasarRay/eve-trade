# Eve Trade observability control plane

This directory records CI command evidence, pytest failures, Kubernetes state, PostgreSQL snapshots, Go/Rust telemetry, Honeycomb investigations, and concise Sentry issues under one `observability.run_id`.

## Quick Start

```powershell
python distributed-backend/observability/ci/observed_run.py check
python distributed-backend/observability/ci/observed_run.py test
python distributed-backend/observability/ci/observed_run.py collect-only
```

Run E2E against an already started Encore/Kubernetes environment:

```powershell
$env:EVE_TRADE_ENCORE_URL="http://127.0.0.1:4000"
$env:EVE_TRADE_SIMULATOR_URL="http://127.0.0.1:8000"
$env:EVE_TRADE_DATABASE_URL="postgres://postgres:postgres@127.0.0.1:5432/eve_trade"
python -m pip install -r distributed-backend/observability/requirements.txt
python distributed-backend/observability/ci/observed_run.py e2e --maxfail 1
```

The command prints its generated run directory under `.o11y/runs/`. Every run starts with `run-context.json`, `provenance.json`, `run-status.json`, `git.json`, `tool-versions.json`, `env-redacted.json`, `hashes.json`, and local span JSON.

Runs are immutable evidence bundles. A run starts as `IN_PROGRESS`, records exact git provenance, and is promoted in `.o11y/index.json` only when finalization succeeds. The optional `.o11y/runs/latest-local.txt` pointer stores only a run ID and means "newest completed local run"; it does not mean "valid for current HEAD".

Freshness is always computed against the current repository state:

- `EXACT`: the run SHA and dirty-worktree fingerprint match the current repository.
- `ANCESTOR`: the run is historical evidence from an ancestor commit.
- `DIVERGED`: the run came from another lineage or branch state.
- `DIRTY_WORKTREE_MISMATCH`: the same commit was used with different uncommitted source changes.
- `UNKNOWN`: provenance is missing or cannot be verified.

When no `EXACT` run exists, treat the newest report as historical context only and run the observed command again before diagnosing the current revision.

`--clean` is accepted for old command compatibility but does not delete resources. Reset databases and runtime environments explicitly.

## Telemetry

The local collector config accepts OTLP on `4317` and `4318` and writes bounded local JSON output. Run it through Kubernetes, the collector binary, or another local supervisor. The Honeycomb config keeps the same local copy behavior for CI parity.

The Encore backend and Rust `trade-settlement` use OTLP and W3C trace context. Domain spans preserve UDP ingress, Market validation and settlement planning, settlement worker completion/failure, and Rust transaction/rollback details.

## Diagnosis Reports

The canonical diagnosis data is structured JSON:

- raw command metadata and logs under `commands/<stage>/<command>/`;
- `diagnosis.json` for observations, derived facts, inferences, causal events, confidence, false-green/false-red risk, and evidence-grounded recommendations;
- `run-report.json`, `run-report.md`, and `run-report.html` rendered from the structured diagnosis;
- compatibility `failure-summary.json` and `failure-report.*` when a command fails.

The classifier is stage and command aware. It identifies the earliest supported causal failure, separates root cause from symptoms and downstream consequences, and can abstain with `UNKNOWN` rather than turning weak evidence into a confident claim.

Local latest/index files and new local run directories are ignored by git. Commit historical evidence only when it is intentionally curated; do not commit volatile files that imply current repository health.

## Failure Reports

Set `HONEYCOMB_API_KEY`, `HONEYCOMB_DATASET`, and `SENTRY_DSN` when external telemetry is desired. Missing Honeycomb, Sentry, Kubernetes, database, and S3 integrations are recorded as evidence gaps and fail only in strict mode.

See [Observability-Control-Plane.md](docs/Observability-Control-Plane.md), [Failure-Triage-Runbook.md](docs/Failure-Triage-Runbook.md), and [Local-vs-CI-Parity.md](docs/Local-vs-CI-Parity.md).
