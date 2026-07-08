# Failure triage runbook

## 1. Open The Report

Download the GitHub Actions artifact and open `failure-report.html`. Start with the failed command, first failing test, classification confidence, and missing-evidence section. The Markdown and JSON forms contain the same portable links.

## 2. Follow The Causal Evidence

1. Open the command log under `commands/<stage>/<command>/command.log`.
2. Open the failing test source link and likely solution files.
3. Inspect Kubernetes pods/events first, then bounded per-pod logs.
4. Inspect `db/metadata.json`, `db/schema.txt`, and only the tables implicated by the failure.

## 3. Honeycomb BubbleUp

1. Query `observability.run_id = <failed-run-id>`.
2. Filter `command.exit_code != 0`, span errors, or the failed `test.nodeid`.
3. Use a duration heatmap and select the failed or slow subset.
4. Run BubbleUp / Compare to Baseline from the Honeycomb UI.
5. Compare to a passing CI/local run. Inspect runtime versions, schema/migration/protobuf hashes, Encore/Kubernetes hashes, service name, test node ID, and failure family.

BubbleUp is an interactive Honeycomb analysis feature. The runner creates investigation metadata and optional deep links, not a separate automation API.

## 4. Sentry Seer/Autofix

Open the linked Sentry event. It contains the run ID, SHA, failed command, first test, artifact path, source links, and Honeycomb link where available. Use Seer/Autofix from the issue UI if enabled for the account.

`SENTRY_AUTOFIX_COMMAND` is an explicit operator hook for an installed organization-specific tool. No default command is assumed.

## 5. Collector Troubleshooting

- **Honeycomb empty:** confirm `OTEL_SDK_DISABLED=false`, endpoint/protocol, collector health on `13133`, and API key/dataset presence.
- **Sentry empty:** confirm `SENTRY_DSN`, outbound network access, and `sentry-*-error.txt`.
- **DB snapshot failed:** verify `EVE_TRADE_DATABASE_URL` or `DATABASE_URL` and local `psql` availability.
- **Kubernetes evidence missing:** verify `kubectl` context and namespace access.
- **Windows:** use normal argument lists rather than shell quoting. `latest-local.txt` is the reliable latest-run pointer when symlink creation is not permitted.
