# v7 observability and failure-analysis control plane

## Starting point and scope

- Repository: `eve-trade`.
- Starting HEAD: `9e14916a304c97d0a7b1954a9615b61265fe3f50`.
- Branch inspected: `main`.
- The worktree already contained unrelated GUI simulator/demo work and a staged deletion of `dagger/pipeline.py`. Those changes were preserved and are not part of this v7 implementation.
- This change is additive observability work. It does not change trade, wallet, item, escrow, idempotency, or settlement business rules.

The objective was to turn local and GitHub Actions runs into portable debugging bundles with a shared run identity, OpenTelemetry spans, structured logs, Docker/PostgreSQL/Kubernetes evidence, Honeycomb BubbleUp investigation metadata, concise Sentry issues, source links, failure classification, and local-vs-CI parity reports.

## Implemented architecture

```text
Go services -------------------+
Rust trade-settlement ---------+--> OTLP --> OpenTelemetry Collector --> Honeycomb
Python observed CI runner -----+                    |
                                                    +--> local bounded telemetry files

Failed observed command --> concise Sentry event --> optional Seer/Autofix operator hook
                        |
                        +--> .o11y/runs/<run_id>/
                              |- command logs and metadata
                              |- pytest/JUnit evidence
                              |- Docker and per-service logs
                              |- PostgreSQL snapshots and schema hash
                              |- Kubernetes evidence when available
                              |- failure-report.html/.md/.json
                              `- GitHub Actions artifact / optional S3 bundle
```

`observability.run_id` is the correlation root across the Python pipeline, service resources, local artifacts, reports, Docker Compose, and collector enrichment.

## New observability package

Created `observability/` with the requested modular control-plane implementation:

- `observability/README.md`
- `observability/requirements.txt`
- `observability/__init__.py`
- `observability/ci/__init__.py`
- `observability/ci/observed_run.py`
- `observability/ci/run_context.py`
- `observability/ci/run_command.py`
- `observability/ci/redaction.py`
- `observability/ci/collect_environment.py`
- `observability/ci/collect_docker.py`
- `observability/ci/collect_db.py`
- `observability/ci/collect_kubernetes.py`
- `observability/ci/collect_pytest.py`
- `observability/ci/classify_failure.py`
- `observability/ci/compare_runs.py`
- `observability/ci/generate_failure_report.py`
- `observability/ci/links.py`
- `observability/ci/sentry_reporter.py`
- `observability/ci/honeycomb_tracer.py`
- `observability/ci/storage.py`
- `observability/collector/otel-collector.local.yaml`
- `observability/collector/otel-collector.honeycomb.yaml`
- `observability/sentry/__init__.py`
- `observability/sentry/sentry_config.py`
- `observability/docs/Observability-Control-Plane.md`
- `observability/docs/Failure-Triage-Runbook.md`
- `observability/docs/Local-vs-CI-Parity.md`
- `observability/examples/.env.observability.example`

### Run identity and safe storage

- GitHub Actions run IDs derive from run ID, attempt, SHA, and ref.
- Local run IDs derive from UTC timestamp, Git SHA, and a random suffix.
- Every run creates `.o11y/runs/<run_id>/` and writes:
  - `run-context.json`
  - `git.json`
  - `tool-versions.json`
  - `env-redacted.json`
  - `hashes.json`
  - `telemetry/local-spans.jsonl`
- `latest-local.txt` is always updated. A `latest-local` directory symlink is attempted where the platform permits it.
- Writes are atomic and constrained to the run directory.
- Run bundles can be zipped and optionally uploaded to S3-compatible storage with `OBS_STORAGE_BACKEND=s3`; local storage remains the default.

### Secret redaction

- Environment keys containing `TOKEN`, `SECRET`, `PASSWORD`, `DSN`, `KEY`, `AUTH`, `CREDENTIAL`, or `PRIVATE` retain presence only.
- Captured command output redacts inline assignments, authorization headers, and credentials embedded in URLs.
- Command arguments support `--secret=value` and separate secret-argument redaction.
- Docker Compose configuration and logs are redacted before artifact storage.
- Sentry receives curated metadata only; full Docker logs and database snapshots are never attached.

### Observed command execution

`observability/ci/observed_run.py` supports:

```text
check
test
integration
e2e
collect-only
```

Supported options are `--clean`, `--maxfail`, `--test-path`, `--no-sentry`, `--no-honeycomb`, `--strict`, and `--compare-to`.

Each command execution writes a text log and JSON metadata with start/end timestamps, duration, exit code, redacted argv, stage, artifact path, and trace ID when available. Subprocess decoding is explicitly UTF-8 with replacement semantics so Windows code pages cannot prevent evidence collection.

The integration/E2E path discovers the repository Compose file and services, then performs:

1. Environment, Git, toolchain, migration, protobuf, and Compose hash capture.
2. Optional clean shutdown and volume removal when `--clean` is explicit.
3. Image build.
4. Collector and dependency startup.
5. Migration execution when a migration service exists.
6. Application readiness.
7. Live pytest E2E with verbose short tracebacks and JUnit XML.
8. Docker, per-service log, and PostgreSQL collection.
9. Failure classification, Sentry reporting, and portable failure-report generation when the command fails.

External telemetry failures are evidence gaps by default. They become fatal only with `--strict` or `OBSERVABILITY_STRICT=true`. The original command exit status remains authoritative.

### Pytest evidence and source links

- Parses stdout/stderr and JUnit XML.
- Extracts the first failing node ID, assertion/failure message, source path and line, pass/fail/skip/error counts, duration, collection count, and collected test list.
- Converts pytest JUnit module names into repository source paths when JUnit omits the `file` attribute.
- Creates absolute GitHub source links from GitHub context or the repository remote.
- Saves `pytest-output.txt`, `pytest-summary.json`, and `pytest-junit.xml`.

### Docker, PostgreSQL, and Kubernetes collectors

Docker evidence includes:

- Docker and Compose versions.
- `compose ps`, redacted rendered configuration, bounded combined logs, and bounded per-service logs.
- Service discovery and service URLs.
- Image IDs/digests, including Docker Compose v5 JSON-array output where service names must be derived from container names.
- Compose configuration hash.

PostgreSQL evidence is read-only and best effort:

- Public table list and schema description.
- `db.schema_hash`.
- Migration-table snapshots when present.
- Bounded text, CSV, and JSON snapshots for likely trade, wallet, escrow, request, settlement, and ledger tables.
- Collector failures are recorded without hiding the test result.

Kubernetes evidence includes:

- Current context and namespaces.
- Pods, services, deployments, events, and rendered local manifests.
- Bounded all-container pod logs.
- Sanitized namespace, pod, container, and image metadata without storing raw Pod specs.

### Failure classification and reports

The transparent rule classifier covers:

- `accept-validation`
- `cancel-lifecycle`
- `idempotency`
- `rollback`
- `client-tampering`
- `settlement-invariant`
- `db-invariant`
- `service-readiness`
- `docker-networking`
- `migration/schema-drift`
- `generated-code-drift`
- `environment-parity`
- `dependency/import-error`

It returns confidence, matching evidence, suspected services, likely solution files, and bounded next commands. It does not claim certainty beyond rule evidence.

Failed runs generate:

- `failure-report.html`
- `failure-report.md`
- `failure-summary.json`

Reports contain the run identity, failed command, first failing test and source link, causal chain, classification, service logs, database artifacts, likely solution files, GitHub Actions link, Honeycomb trace/query metadata and BubbleUp instructions, Sentry event details, parity hints, next commands, and missing evidence. Artifact links are relative so they remain usable after downloading a GitHub Actions artifact.

### Local-vs-CI parity

`compare_runs.py` generates HTML, Markdown, and JSON comparisons for:

- Git SHA, branch, and dirty state.
- Python, Go, Rust, Docker, Compose, and OS versions.
- Redacted environment-variable presence.
- Compose hash and service image IDs.
- Database schema and migration hash/file list.
- Generated protobuf hash.
- Collected pytest node IDs and first failure.
- Service URLs and readiness durations.
- Ordered observed command sequence.

The report highlights only actual differences and emits explicit likely-impact hints.

## Honeycomb and BubbleUp integration

- Python pipeline spans use OpenTelemetry OTLP/HTTP and service name `eve-trade-ci`.
- Missing SDK packages, endpoints, or credentials degrade to local span JSON unless strict mode is enabled.
- Honeycomb ingestion supports `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, `HONEYCOMB_API_KEY`, and dataset/service-name variables.
- Query links use Honeycomb query-template JSON rather than an invented BubbleUp API.
- Trace links use the documented direct-trace format and include a run-relative search time window.
- Failure reports explain how to select an error/latency subset and run BubbleUp against a passing baseline.
- An optional Honeycomb Configuration Key can create the `Eve Trade CI/CD Failure Triage` board with run, service, and failure-family preset filters. The ingest key is not treated as a configuration key.

Reference documentation used:

- https://docs.honeycomb.io/send-data/opentelemetry/collector
- https://docs.honeycomb.io/investigate/collaborate/share-query
- https://docs.honeycomb.io/investigate/collaborate/share-trace
- https://docs.honeycomb.io/reference/honeycomb-ui/query/query-results
- https://docs.honeycomb.io/api/boards/create-a-board

## Sentry and Seer/Autofix integration

- Sentry initializes only when `SENTRY_DSN` is present.
- A failed observed run creates at most one concise event tagged with run, GitHub, command, stage, family, and failing-test context.
- Event context includes the redacted command, exit code, artifact path, first test, source links, Honeycomb link, and GitHub Actions link.
- The returned event ID is persisted to the run bundle.
- `sentry-cli` release creation, commit association, and finalization are best effort when the CLI and required token/org/project variables exist.
- `SENTRY_AUTOFIX_COMMAND` is an explicit operator hook. The event ID is appended, and the result is captured without assuming a universal account/API workflow.
- Sentry's documented Seer Issue Fix endpoint requires an issue ID and additional scopes, so the runner does not silently convert an event or request that authority.

Reference documentation used:

- https://docs.sentry.io/product/ai-in-sentry/seer
- https://docs.sentry.io/api/seer/start-seer-issue-fix/

## OpenTelemetry Collector and deployment changes

Created two validated Collector configurations:

- `otel-collector.local.yaml`: OTLP gRPC/HTTP receivers, memory limiter, run/resource enrichment, batch processing, debug output, health endpoint, and rotating local trace/log/metric files.
- `otel-collector.honeycomb.yaml`: the same receive/process path plus queued/retried OTLP HTTP export to Honeycomb, debug output, and bounded local file copies for CI artifact parity.

The Collector Contrib image is the official signed GHCR v0.153.0 release pinned to digest:

```text
sha256:93aad750175cbf1a973ae1c5886c3371f4d800f61be25cdd26870b8441ffe9fa
```

Updated:

- `compose.yaml` with an optional `observability` Collector profile, local/Honeycomb config selection, artifact volume, and service OTEL/run variables.
- `docker-compose.integration.yml` with the Collector in the `test` profile, per-run Collector output mounting, and OTEL/run/version/environment variables for Go and Rust services.
- `distributed-backend/orchestration/kubernetes/base/observability/otel-collector.yaml` with Honeycomb OTLP export, run enrichment, the pinned image, and no fabricated Sentry Collector exporter.
- `distributed-backend/orchestration/kubernetes/base/configmaps.yaml` with `OBSERVABILITY_RUN_ID` for application services.

## Go service instrumentation and structured logs

Updated the shared Go adapter and added `distributed-backend/src/observability/domain.go`:

- Resource fields include service name/version/language, deployment environment, and run ID.
- JSON logs include normalized `timestamp`, service identity, run ID, and normalized `error.message`.
- Context-aware logs add trace and span IDs when a valid span context is available.
- Added helpers for domain spans and deterministic actor-ID hashing.
- Replaced the broad process detector because it included command-line arguments and process owner data. Safe PID/executable/runtime detectors avoid secret leakage and work in minimal containers.

Explicit domain spans were added without changing trade behavior:

- API Gateway:
  - `gateway.receive_ui_activity`
  - `gateway.forward_to_market`
- Market:
  - `market.create_trade_offer`
  - `market.accept_trade`
  - `market.cancel_trade`
  - `market.validation`
  - `market.build_settlement_operations`

Attributes include interaction/idempotency/trade identifiers, state, quantity, item/station data, safe actor hashes, validation outcome, and rejection reason where available.

## Rust trade-settlement instrumentation

Extended the existing `summer-opentelemetry` integration with resource fields and explicit tracing spans:

- `settlement.receive_batch`
- `settlement.validate_batch`
- `settlement.execute_transaction`
- `settlement.execute_step`
- `settlement.rollback`
- `settlement.record_failure_audit`

The spans/events include batch ID, step ID/kind/order, idempotency key, operation count, rollback state, failure code, and normalized error message. The existing transaction and rollback behavior was not changed.

## GitHub Actions and repository integration

Updated `.github/workflows/verify.yaml` E2E job to:

- Install Python and `observability/requirements.txt`.
- Run `python observability/ci/observed_run.py integration --clean --maxfail 1`.
- Provide GitHub context plus optional Honeycomb/Sentry secrets without printing them.
- Select the credential-free Collector config when Honeycomb is absent.
- Always upload `.o11y/runs/` with `actions/upload-artifact@v4`, including hidden files.
- Retain the existing final Compose cleanup.

Other repository integration:

- `.gitignore` ignores local `.o11y/` output.
- `.dockerignore` excludes `.o11y/` and observability tests from application build contexts.
- `ci-cd/pipeline.py` excludes `.o11y` from Dagger source contexts.

## Tests added

Created focused unit tests for:

- Secret environment and inline-output redaction.
- Run context, required artifacts, and latest-run pointer.
- Downloaded-run context rebasing so report regeneration uses the artifact's current path.
- Known accept-validation classification.
- Failure report content and links.
- Docker Compose v5 image parsing and service derivation.
- JUnit source-path derivation when pytest omits `file`.
- Windows-safe no-op copy when JUnit already occupies its artifact destination.

## Runtime defects found and corrected during validation

1. Docker Hub TLS handshakes repeatedly timed out while fetching the Collector. The deployment now uses the official signed GHCR release image pinned by digest.
2. Windows cp1252 decoding failed on UTF-8 Docker output and prevented post-test evidence collection. Every subprocess boundary now uses explicit UTF-8 with replacement semantics.
3. Copying JUnit XML to an identical source/destination path caused `WinError 32`. Storage now treats same-file copies as successful no-ops.
4. Go `resource.WithProcess()` attempted process-owner discovery in minimal containers and included command arguments. It was replaced with safe individual process/runtime detectors.
5. Docker Compose v5 emits image metadata as a JSON array without a `Service` property. The collector now derives service identity from `ContainerName` and records image IDs correctly.
6. The observed integration runner now starts `otel-collector` with infrastructure dependencies before application services so OTLP DNS is available during service initialization.

## Validation performed

### Python and local control plane

- `python -m compileall -q observability` — passed.
- `python -m unittest discover -s observability/tests -v` — 9 tests passed.
- `python observability/ci/observed_run.py check` — passed.
- Final no-credentials smoke run:
  - `.o11y/runs/local-20260628T034432Z-9e14916a-9b51ef/`
- Synthetic failed observed run generated all three failure reports and the standalone regeneration CLI succeeded.
- Parity smoke comparison generated HTML, Markdown, and JSON outputs.

### Full observed E2E

Command:

```powershell
python observability/ci/observed_run.py e2e --maxfail 1
```

Result:

- Exit code: `0`.
- Pytest: `109 passed`, `0 failed`.
- PostgreSQL snapshot: available, 16 public tables, no collector errors.
- Docker evidence: 11 services and 11 per-service logs; the corrected image parser records 10 active service images.
- Completed run bundle:
  - `.o11y/runs/local-20260627T161230Z-9e14916a-cc3bea/`

### Service instrumentation

- Shared observability Go module: `go test ./...` — passed.
- Market Go module: `go test ./...` — passed.
- API Gateway Go module: `go test ./...` — passed.
- Settlement worker Go module: `go test ./...` — passed.
- Focused live gateway E2E test — passed.
- Collector verification found one or more spans for every expected focused path:
  - `gateway.receive_ui_activity`
  - `gateway.forward_to_market`
  - `market.validation`
  - `market.create_trade_offer`
  - `market.build_settlement_operations`
- Runtime correlation value verified: `observability.run_id=runtime-verification`.

### Rust

- `cargo fmt --all -- --check` — passed.
- `cargo check --locked --all-targets --all-features` — passed.
- `cargo test --locked --all-features` — passed.
- `cargo clippy --locked --all-targets --all-features -- -D warnings` — passed.

### Collector, Compose, and Kubernetes

- Local Collector config validation — passed.
- Honeycomb Collector config validation with placeholder validation credentials — passed.
- `docker compose ... config --quiet` for normal and integration Compose — passed.
- Kubernetes base observability, local, and production kustomize renders — passed.
- Local Collector file output contains Rust settlement spans and the verified Go gateway/market span chain.

## Operating commands

Credential-free local checks:

```powershell
python observability/ci/observed_run.py check
python observability/ci/observed_run.py test
python observability/ci/observed_run.py collect-only
```

Observed E2E:

```powershell
python -m pip install -r observability/requirements.txt
python observability/ci/observed_run.py e2e --maxfail 1
```

Clean CI-shaped E2E, which deletes Compose volumes:

```powershell
python observability/ci/observed_run.py integration --clean --maxfail 1
```

Local Collector:

```powershell
$env:OTEL_SDK_DISABLED="false"
$env:OBSERVABILITY_RUN_ID="manual-local"
docker compose --profile observability up -d otel-collector
docker compose up -d
```

Parity comparison:

```powershell
python observability/ci/compare_runs.py `
  --local .o11y/runs/<local-run> `
  --ci C:\path\to\downloaded-ci-run `
  --output .o11y/parity
```

## Remaining manual setup and validation

- Honeycomb ingestion requires `HONEYCOMB_API_KEY`; UI links additionally require team and environment slugs.
- Honeycomb board creation requires a separate `HONEYCOMB_CONFIGURATION_KEY` with board permissions.
- Sentry issue creation requires `SENTRY_DSN`; releases require `sentry-cli`, auth token, org, and project.
- Seer/Autofix remains explicit and account-scoped through `SENTRY_AUTOFIX_COMMAND` or manual use from the generated Sentry issue.
- Optional S3 upload requires `boto3`, bucket/prefix, region, and normal AWS credentials.
- Honeycomb, Sentry, Seer/Autofix, board creation, and S3 upload were not exercised with real credentials in this environment.
- Kubernetes manifests rendered successfully; live cluster evidence collection was not required for the completed local Compose acceptance run.

The credential-free path, local artifacts, reports, parity comparison, Collector file export, service tracing, PostgreSQL snapshots, and full Compose E2E were exercised locally.

## Post-implementation CI resilience correction

GitHub Actions run `28310941084` reported 64 E2E failures at `helpers.py:107`. The failure duration exposed a single repeated transport symptom: 64 simulator UDP waits at the old three-second timeout accounted for 192 of the 223 test seconds. Gateway business errors are raised later in the helper, so this location identified an outer simulator/UDP failure rather than 64 independent trade-rule regressions.

The resilience policy is now explicit:

- Recoverable network, timeout, and downstream-unavailable failures receive bounded retries.
- Retries preserve the original `interaction_id` and signed payload.
- API Gateway caches the exact terminal response for an interaction and returns it for an identical retry without calling Market again.
- Reusing an interaction ID with a different payload remains a terminal replay conflict.
- An in-flight duplicate receives `request_in_progress`; the simulator waits within its outer timeout budget and retries.
- Downstream timeout/unavailable responses release the in-memory reservation so the same idempotent request can be attempted again. Durable settlement idempotency and database transactions remain the final correctness boundary if the first downstream attempt committed before its response was lost.
- Validation, authorization, compiler, migration, startup-configuration, and invariant failures are not treated as transient.
- Exhausting the bounded retry budget remains a failure so Docker/Kubernetes/GitHub Actions orchestration can restart or reschedule cleanly.

### UDP and replay behavior

- Increased the simulator per-attempt UDP timeout from three to six seconds, which is longer than the API Gateway's five-second Market deadline.
- Added configurable `QUILKIN_UDP_MAX_ATTEMPTS` and `QUILKIN_UDP_RETRY_BACKOFF_SECONDS` settings to local Compose, integration Compose, and the local Kubernetes simulator overlay.
- Added transient socket classification for timeout, reset, refused, and unreachable errors.
- Replaced rejection of identical replays with a fingerprinted response cache in API Gateway.
- Cached replies are stored before the UDP write, allowing a lost response to be recovered without repeating the Market or settlement operation.
- Added tests for lost response recovery, identical cached response replay, conflicting payload rejection, and retry after transient downstream unavailability.
- Updated E2E idempotency assertions to require the same successful response and exactly one settlement batch rather than a replay error.

### CI and image-build resilience

- Corrected `go.mod` metadata for direct `go.opentelemetry.io/otel/trace` imports in API Gateway and the shared observability module.
- Added transparent transient-failure classification and bounded retries to observed Compose build/start commands.
- Each retry attempt writes independent logs and metadata plus a portable `retry-summary.json`.
- Added BuildKit module/registry caches and bounded dependency-download retries to Go, Rust, Quilkin, and simulator images.
- Added configurable Go proxy selection through the `GO_MODULE_PROXY` build argument.
- Added bounded pip retries for simulator and E2E dependency installation.

### Verification performed for this correction

- API Gateway `go test ./...`: passed.
- Shared observability `go test ./...`: passed.
- Django simulator `python manage.py test trade_gui`: passed with four tests.
- Observability unit suite: passed with transient and deterministic failure-classification coverage.
- `go mod tidy -diff` for API Gateway and observability: clean after correction.
- A full observed E2E attempt could not reach service startup because the local Docker build environment repeatedly failed TLS handshakes to both `proxy.golang.org` and GitHub. The pipeline retained all bounded attempt logs and stopped before migrations or services, demonstrating safe exhaustion behavior. This is an external dependency-network blocker, not an application test result.

## Follow-up: Quilkin session capacity and replay diagnostics

GitHub Actions run `28319158642` failed on simulator interaction 37 after 23 tests had passed. The request timed out three times before returning HTTP 502, and its interaction ID never appeared in API Gateway logs. An isolated UDP echo reproduction confirmed that Quilkin 0.10 exhausts the container's locked-memory allowance as it creates io_uring-backed resources for active client sessions. Quilkin remains running but silently stops forwarding new sessions when that limit is reached.

The correction:

- Sets the Quilkin `memlock` soft and hard limits to unlimited in both normal and integration Compose definitions.
- Keeps the scope restricted to the non-root Quilkin container; no application or database container receives the elevated limit.
- Preserves exact-response caching for identical retries and conflicting-payload rejection for interaction ID misuse.
- Restores an explicit `replay rejected` phrase in the conflicting-payload response while retaining the stable `replay` error code.
- Adds a gateway unit assertion that the replay diagnostic remains useful to callers.

Verification:

- Isolated Quilkin stress test reproduced deterministic packet loss at constrained memlock limits and passed 120 unique UDP clients at 64 MiB.
- Integration Quilkin container reported `Max locked memory: unlimited` after the Compose change.
- API Gateway `go test ./...`: passed.
- Focused identical-retry/idempotency E2E tests: 9 passed.
- Focused conflicting-payload replay E2E tests: 3 passed.
- Full Docker Compose E2E suite: 109 passed in 27.48 seconds.
- Compose configuration validation passed for normal and integration definitions.

The clean observed build was also attempted. Its three bounded build attempts were stopped by repeated local `proxy.golang.org` `unexpected EOF` responses before service startup. Runtime verification therefore reused existing service images plus a locally compiled current gateway binary; this does not affect GitHub Actions, which builds fresh images and now receives the corrected Quilkin runtime limit.
