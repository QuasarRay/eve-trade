# Failure triage runbook

## 1. Open the report

Download the GitHub Actions artifact and open `failure-report.html`. Start with the failed command, first failing test, classification confidence, and missing-evidence section. The Markdown and JSON forms contain the same portable links.

## 2. Follow the causal evidence

1. Open the command log under `commands/<stage>/<command>/command.log`.
2. Open the failing test source link and likely solution files.
3. Inspect `docker/compose-ps.txt` and categorized `docker/compose-logs.txt`.
4. Inspect `db/metadata.json`, `db/schema.txt`, and only the tables implicated by the failure.
5. For Kubernetes failures, inspect pods/events first, then the bounded per-pod logs.

## 3. Honeycomb BubbleUp

1. Query `observability.run_id = <failed-run-id>`.
2. Filter `command.exit_code != 0`, span errors, or the failed `test.nodeid`.
3. Use a duration heatmap and select the failed/slow subset.
4. Run BubbleUp / Compare to Baseline from the Honeycomb UI.
5. Compare to a passing CI/local run. Inspect runtime versions, image digests, schema/migration/protobuf hashes, service name, test node ID, and failure family.

BubbleUp is an interactive Honeycomb analysis feature. The runner creates investigation metadata and optional deep links, not a non-existent automation API.

## 4. Sentry Seer/Autofix

Open the linked Sentry event. It contains the run ID, SHA, failed command, first test, artifact path, source links, and Honeycomb link where available. Use Seer/Autofix from the issue UI if enabled for the account. Provide the command log and database/trace conclusions as context; do not upload full dumps.

`SENTRY_AUTOFIX_COMMAND` is an explicit operator hook for an installed organization-specific tool. No default command is assumed.

The documented Seer Issue Fix endpoint requires an issue ID plus `event:admin` or `event:write`; the observed runner has a Sentry event ID at capture time and deliberately does not request broader API authority. If your organization has an approved event-to-issue resolver and Seer client, place that command in `SENTRY_AUTOFIX_COMMAND`.

## 5. Collector troubleshooting

- **Honeycomb empty:** confirm `OTEL_SDK_DISABLED=false`, endpoint/protocol, collector health on `13133`, and API key/dataset presence. Inspect collector JSON logs without printing the key.
- **Sentry empty:** confirm `SENTRY_DSN`, outbound network access, and `sentry-*-error.txt`. Release hooks additionally require `sentry-cli`, token, org, and project.
- **Docker collector failed:** run `docker info` and `docker compose config`; artifacts retain the exact exit code.
- **DB snapshot failed:** verify the PostgreSQL service is running and the database name. Collection failure never hides the test failure.
- **Windows:** use normal argument lists rather than shell quoting. `latest-local.txt` is the reliable latest-run pointer when symlink creation is not permitted.
- **Missing secrets:** expected in local mode; artifacts and reports still work.
