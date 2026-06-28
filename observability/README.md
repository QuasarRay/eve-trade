# Eve Trade observability control plane

This directory adds a vendor-neutral debugging layer around the existing distributed trade platform. It correlates CI commands, pytest failures, Docker/Kubernetes state, PostgreSQL snapshots, Go/Rust service telemetry, Honeycomb investigations, and concise Sentry issues under one `observability.run_id`.

## Architecture

```text
Go / Rust services ─┐
Python observed run ├─ OTLP ─ OpenTelemetry Collector ─ Honeycomb
Kubernetes metadata ┘                              └─ BubbleUp investigation

Observed command failures ─ Sentry SDK ─ Sentry issue ─ optional Seer/Autofix
                       └─ .o11y/runs/<run_id>/ ─ GitHub Actions artifact / optional S3
```

The control plane never requires external credentials. Missing or unavailable Honeycomb, Sentry, Kubernetes, Docker, database, and S3 integrations are recorded as local evidence gaps. They fail the run only with `--strict` or `OBSERVABILITY_STRICT=true`.

## Quick start without credentials

```powershell
python observability/ci/observed_run.py check
python observability/ci/observed_run.py test
python observability/ci/observed_run.py collect-only
```

The command prints its generated directory, for example:

```text
.o11y/runs/local-20260628T010203Z-a1b2c3d4-91af20
```

Every run starts with `run-context.json`, `git.json`, `tool-versions.json`, `env-redacted.json`, `hashes.json`, and `telemetry/local-spans.jsonl`.

Run the CI-shaped Compose E2E path:

```powershell
python -m pip install -r observability/requirements.txt
python observability/ci/observed_run.py e2e --maxfail 1
```

`--clean` deliberately executes `docker compose down -v --remove-orphans` before E2E. Use it only when local PostgreSQL and RabbitMQ data may be deleted.

## Retry and failure policy

- Compose build and idempotent start commands retry recognized network, TLS, timeout, and temporary service-unavailability failures up to three times with bounded backoff.
- Every attempt has its own command log and metadata. `retry-summary.json` records whether the pipeline recovered.
- Compiler errors, invalid configuration, migration failures, test assertions, and invariant failures are not retried.
- Docker dependency stages use shared BuildKit caches and bounded package-manager retries so a recovered download continues from cached work.
- GUI UDP retries reuse the original signed packet and `interaction_id`. The API Gateway returns a cached terminal response for an identical retry, rejects the same ID with a different payload, and never forwards a completed request twice.
- Once the bounded retry budget is exhausted, the command fails and orchestration remains responsible for restarting the failed workload.

## Local OpenTelemetry Collector

```powershell
$env:OTEL_SDK_DISABLED="false"
$env:OBSERVABILITY_RUN_ID="manual-local"
docker compose --profile observability up -d otel-collector
docker compose up -d
```

The credential-free collector writes bounded JSON telemetry under `.o11y/collector/` for manual Compose runs and emits debug summaries. Observed runs override the mount to `telemetry/collector-live/` inside their run bundle so GitHub artifact uploads preserve the same evidence. The Honeycomb config also keeps bounded local copies for CI parity. To use Honeycomb, select `otel-collector.honeycomb.yaml` and configure the variables from `examples/.env.observability.example`.

The Compose image is pinned to OpenTelemetry Collector Contrib `0.153.0`. The application services already use OTLP HTTP and W3C trace-context propagation; local OTEL remains disabled by default to preserve normal startup when the optional collector profile is absent.

## Honeycomb and BubbleUp

Set `HONEYCOMB_API_KEY`, `HONEYCOMB_DATASET`, and an OTLP endpoint. The Python runner emits high-cardinality command spans and always journals equivalent local metadata. Failure reports include filters and instructions for selecting the failed subset and running BubbleUp; no private or unstable BubbleUp API is assumed.

Optional UI-only deep links require `HONEYCOMB_TEAM_SLUG` and `HONEYCOMB_ENVIRONMENT_SLUG`. If those are absent, the report still supplies exact filters and fields.

References: [Honeycomb Collector export](https://docs.honeycomb.io/send-data/opentelemetry/collector), [query template links](https://docs.honeycomb.io/investigate/collaborate/share-query), and [BubbleUp results](https://docs.honeycomb.io/reference/honeycomb-ui/query/query-results).

An optional `HONEYCOMB_CONFIGURATION_KEY` enables best-effort creation of the `Eve Trade CI/CD Failure Triage` board. This is intentionally separate from the ingest key and never required for telemetry export.

## Sentry, Seer, and Autofix

Set `SENTRY_DSN` for one concise issue per failed observed run. `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, and `SENTRY_PROJECT` additionally enable best-effort `sentry-cli` release metadata. Full Docker logs and database dumps are never attached to Sentry.

Seer/Autofix availability depends on the Sentry organization and project. The report links the issue and explains how to invoke it manually. An operator may set `SENTRY_AUTOFIX_COMMAND` to an installed, account-specific command; the runner appends the event ID and records the hook result without assuming a universal API.

Sentry documents an authenticated [Start Seer Issue Fix API](https://docs.sentry.io/api/seer/start-seer-issue-fix/), but it operates on an issue ID and account scopes. The runner therefore leaves API invocation opt-in through the operator hook instead of converting an event to an issue or requesting write scopes implicitly.

## Parity comparison

```powershell
python observability/ci/compare_runs.py --local .o11y/runs/<local> --ci .o11y/runs/<downloaded-ci> --output .o11y/parity
```

This generates `parity-diff.html`, `.md`, and `.json` with version, Git, environment-presence, Compose, image, schema, migration, protobuf, pytest, service URL/readiness, and command-sequence differences.

## Files

- `ci/observed_run.py` — orchestration and safe degradation.
- `ci/run_command.py` — command logs, metadata, spans, and Sentry breadcrumbs.
- `ci/collect_*.py` — environment, Docker, PostgreSQL, pytest, and Kubernetes evidence.
- `ci/classify_failure.py` — transparent Eve Trade failure-family rules.
- `ci/generate_failure_report.py` — portable HTML/Markdown/JSON triage report.
- `ci/compare_runs.py` — local-vs-CI parity diff.
- `collector/` — local and Honeycomb Collector configurations.
- `docs/` — design, triage, and parity runbooks.

See [Observability-Control-Plane.md](docs/Observability-Control-Plane.md) for field contracts and [Failure-Triage-Runbook.md](docs/Failure-Triage-Runbook.md) for operational use.
