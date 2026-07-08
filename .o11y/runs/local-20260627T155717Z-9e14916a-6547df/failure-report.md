# Eve Trade failure report

## Executive summary

Command observability-unit-tests failed with exit code 1; likely failure family: unclassified (20% rule confidence).

## Run identity

- Run: `local-20260627T155717Z-9e14916a-6547df`
- Environment: `local`
- Git SHA: `9e14916a304c97d0a7b1954a9615b61265fe3f50`

## Failed command

- Stage: `test`
- Command: `C:\Program Files\Python314\python.exe -m unittest discover -s observability/does-not-exist -v`
- Exit code: `1`
- Duration: `284.34 ms`
- [Command log](commands/test/observability-unit-tests/command.log)

## First failing test

- Node ID: `not extracted`
- Failure: No pytest failure was extracted.

## Failure family

- Family: `unclassified`
- Confidence: `20%`
- Suspected services: unknown

- No transparent classification rule matched the available evidence.

## Causal chain

1. Pipeline stage `test` invoked `observability-unit-tests`.
2. The command exited `1` after `284.34 ms`.
3. The first extracted failure was `not a pytest test`.
4. Transparent rules classified the available evidence as `unclassified`.

## Honeycomb and BubbleUp

- Open or build a Honeycomb query filtered by observability.run_id = local-20260627T155717Z-9e14916a-6547df.
- Visualize duration with a heatmap or filter spans where error=true / command.exit_code != 0.
- Select the failed subset and choose BubbleUp / Compare to Baseline.
- Compare against a passing local run or passing CI run using the high-cardinality fields above.

Fields to inspect: `python.version`, `os.name`, `docker.image_digest`, `db.schema_hash`, `db.migration_hash`, `protobuf.generated_hash`, `service.name`, `test.nodeid`, `command.exit_code`, `test.failure_family`, `github.run_id`, `git.dirty`

## Sentry / Seer / Autofix

- No Sentry event was created; configure `SENTRY_DSN` to enable it.
- In the Sentry issue, open Seer/Autofix if the organization plan and project support it.
- Provide Seer the linked command log, failing source line, likely solution files, and Honeycomb trace/query. No guaranteed Seer API is assumed.

## Related service logs

- No per-service log artifacts were collected.

## Database snapshots

- No database snapshot artifacts were collected.

## Likely solution files


## Suggested next commands

- `Open failure-report.html and inspect the first failing command and service logs.`

## Related artifacts

- [commands/test/observability-unit-tests/command.json](commands/test/observability-unit-tests/command.json)
- [commands/test/observability-unit-tests/command.log](commands/test/observability-unit-tests/command.log)
- [env-redacted.json](env-redacted.json)
- [git.json](git.json)
- [hashes.json](hashes.json)
- [pytest/pytest-output.txt](pytest/pytest-output.txt)
- [pytest/pytest-summary.json](pytest/pytest-summary.json)
- [run-context.json](run-context.json)
- [run-summary.json](run-summary.json)
- [telemetry/local-spans.jsonl](telemetry/local-spans.jsonl)
- [tool-versions.json](tool-versions.json)

## Local vs CI parity

- Compare this run with a passing local run using `compare_runs.py`.

## Missing evidence

- None reported by collectors.
