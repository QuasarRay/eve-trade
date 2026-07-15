# Observability control plane

## Objectives

The platform answers four questions from one run bundle:

1. What command, test, service, or settlement step failed?
2. What changed between a passing and failing environment?
3. Which source files and data projections are most likely involved?
4. Which Honeycomb trace/BubbleUp subset and Sentry issue should an engineer open next?
5. Whether the report is exact for the current repository or historical evidence only.

## Correlation contract

`observability.run_id` is the root correlation key. GitHub runs derive it from the run ID, attempt, SHA, and ref. Local runs use UTC time, Git SHA, and random suffix. The ID is present in CI spans, local span JSON, service resource attributes, collector enrichment, reports, and artifact paths.

High-cardinality fields include:

- GitHub: `github.run_id`, `github.run_attempt`, `github.workflow`, `github.job`, `github.sha`, `github.ref`.
- Git: `git.dirty`, `git.branch`.
- Pipeline: `pipeline.command`, `pipeline.stage`, `pipeline.step`, `command.argv`, `command.exit_code`, `command.duration_ms`.
- Tests: `test.nodeid`, `test.file`, `test.line`, `test.failure_family`.
- Domain: `interaction_id`, `idempotency_key`, `trade_id`, `settlement_batch_id`, `settlement_step_id`, item/station/quantity and safe actor hashes.
- Runtime: `service.name`, `service.version`, `service.language`, Encore/Kubernetes namespace, pod, and container fields.
- Drift: `db.schema_hash`, `db.migration_hash`, `protobuf.generated_hash`.
- Links: `artifact.path`, `sentry.event_id`, `honeycomb.trace_url`, `source.url`.

## Data paths

The Encore backend initializes OTLP traces, metrics, and logs through `internal/observability`; Encore owns the Go service API and Pub/Sub boundaries. Domain spans cover UDP ingress/forwarding and Market create/accept/cancel/validation/settlement-plan stages.

Rust registers `summer-opentelemetry`; explicit spans cover receive, validation, transaction, step execution, rollback, and failure audit. Structured tracing events carry batch, step, failure, and rollback fields.

The Python runner uses the OTLP HTTP exporter when configured and always writes local span JSONL. Sentry is intentionally a native SDK path for concise issues, rather than forwarding full logs or database data.

Each run records `provenance.json`, `run-status.json`, `diagnosis.json`, and `run-report.*`. Markdown and HTML reports are renderings of structured diagnosis data; they are not the canonical data model. The diagnosis separates observations, derived facts, inferences, recommendations, causal events, confidence, negative evidence, false-green risk, and false-red risk.

## Failure safety

- Sensitive environment keys are represented only as `<redacted:present>` or `<redacted:empty>`.
- Inline credentials, authorization headers, and URL credentials are removed from captured logs.
- PostgreSQL collection is read-only, bounded to 200 rows per selected table, and best-effort.
- External telemetry failures become local error artifacts unless strict mode is enabled.
- The original command exit code remains the primary outcome.
- A partially written run remains `IN_PROGRESS` or `INCOMPLETE` and is not promoted as latest complete evidence.
- A stale report is labeled `ANCESTOR`, `DIVERGED`, `DIRTY_WORKTREE_MISMATCH`, or `UNKNOWN`; it is never treated as current because it is the newest file on disk.

## Collector deployment

The local Collector uses debug and rotating file exporters without credentials. The Honeycomb Collector uses queued/retried OTLP HTTP export. Kubernetes keeps vendor egress in the collector namespace. Sentry issue creation remains in the CI SDK because Seer/Autofix needs a concise issue with curated context rather than raw telemetry fan-out.

There is no fabricated BubbleUp API or guaranteed Seer API. Links are generated only when enough stable URL components are configured; otherwise reports provide manual filter instructions.
