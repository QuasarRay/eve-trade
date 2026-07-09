# Failure triage runbook

## 1. Open The Report

Open `run-report.md` or `run-report.html` from the run directory. If a command failed, `failure-report.html` is also written for compatibility. Start with the freshness block; if it is not `EXACT`, treat the report as historical evidence only.

## 2. Follow The Causal Evidence

1. Open `diagnosis.json` and confirm the earliest causal event.
2. Open the referenced command log under `commands/<stage>/<command>/command.log`.
3. Check supporting, contradicting, and missing evidence before accepting an inference.
4. Inspect Kubernetes, Docker, or database artifacts only when the diagnosis has direct evidence for that layer.

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
- **Windows:** use normal argument lists rather than shell quoting. `latest-local.txt` stores only a portable run ID when present; freshness still comes from `provenance.json` compared with the current repository.
