# Eve Trade failure report

## Executive summary

Command compose-build failed with exit code 1; likely failure family: docker-networking (67% rule confidence).

## Run identity

- Run: `local-20260628T070623Z-4bbba968-d7968e`
- Environment: `local`
- Git SHA: `4bbba96894b8e35599bc34c7d4ebf71a68621d77`

## Failed command

- Stage: `build`
- Command: `docker compose -f C:\Users\Astral\Desktop\eve-trade\docker-compose.integration.yml --profile test build`
- Exit code: `1`
- Duration: `35741.969 ms`
- [Command log](commands/build/compose-build/command.log)

## First failing test

- Node ID: `not extracted`
- Failure: No pytest failure was extracted.

## Failure family

- Family: `docker-networking`
- Confidence: `67%`
- Suspected services: docker-compose, api-gateway, market

- matched /connection refused|temporary failure in name resolution|no such host|network.*not found|quilkin/

## Causal chain

1. Pipeline stage `build` invoked `compose-build`.
2. The command exited `1` after `35741.969 ms`.
3. The first extracted failure was `not a pytest test`.
4. Transparent rules classified the available evidence as `docker-networking`.

## Honeycomb and BubbleUp

- Open or build a Honeycomb query filtered by observability.run_id = local-20260628T070623Z-4bbba968-d7968e.
- Visualize duration with a heatmap or filter spans where error=true / command.exit_code != 0.
- Select the failed subset and choose BubbleUp / Compare to Baseline.
- Compare against a passing local run or passing CI run using the high-cardinality fields above.

Fields to inspect: `python.version`, `os.name`, `docker.image_digest`, `db.schema_hash`, `db.migration_hash`, `protobuf.generated_hash`, `service.name`, `test.nodeid`, `command.exit_code`, `test.failure_family`, `github.run_id`, `git.dirty`

## Sentry / Seer / Autofix

- No Sentry event was created; configure `SENTRY_DSN` to enable it.
- In the Sentry issue, open Seer/Autofix if the organization plan and project support it.
- Provide Seer the linked command log, failing source line, likely solution files, and Honeycomb trace/query. No guaranteed Seer API is assumed.

## Related service logs

- [docker/logs/api-gateway.log](docker/logs/api-gateway.log)
- [docker/logs/e2e-tests.log](docker/logs/e2e-tests.log)
- [docker/logs/market.log](docker/logs/market.log)
- [docker/logs/migrate.log](docker/logs/migrate.log)
- [docker/logs/otel-collector.log](docker/logs/otel-collector.log)
- [docker/logs/postgres.log](docker/logs/postgres.log)
- [docker/logs/quilkin.log](docker/logs/quilkin.log)
- [docker/logs/rabbitmq.log](docker/logs/rabbitmq.log)
- [docker/logs/settlement-worker.log](docker/logs/settlement-worker.log)
- [docker/logs/simulator.log](docker/logs/simulator.log)
- [docker/logs/trade-settlement.log](docker/logs/trade-settlement.log)

## Database snapshots

- [db/metadata.json](db/metadata.json)

## Likely solution files

- [compose.yaml](https://github.com/QuasarRay/eve-trade/blob/4bbba96894b8e35599bc34c7d4ebf71a68621d77/compose.yaml)
- [docker-compose.integration.yml](https://github.com/QuasarRay/eve-trade/blob/4bbba96894b8e35599bc34c7d4ebf71a68621d77/docker-compose.integration.yml)
- [distributed-backend/src/api-gateway/distributed-backend/quilkin_udp.go](https://github.com/QuasarRay/eve-trade/blob/4bbba96894b8e35599bc34c7d4ebf71a68621d77/distributed-backend/src/api-gateway/distributed-backend/quilkin_udp.go)

## Suggested next commands

- `docker compose ps`
- `docker compose logs --no-color`

## Related artifacts

- [commands/build/compose-build/command.json](commands/build/compose-build/command.json)
- [commands/build/compose-build/command.log](commands/build/compose-build/command.log)
- [db/metadata.json](db/metadata.json)
- [docker/compose-config.yaml](docker/compose-config.yaml)
- [docker/compose-images.jsonl](docker/compose-images.jsonl)
- [docker/compose-logs.txt](docker/compose-logs.txt)
- [docker/compose-ps.txt](docker/compose-ps.txt)
- [docker/compose-version.txt](docker/compose-version.txt)
- [docker/discovered-services.json](docker/discovered-services.json)
- [docker/docker-version.txt](docker/docker-version.txt)
- [docker/logs/api-gateway.log](docker/logs/api-gateway.log)
- [docker/logs/e2e-tests.log](docker/logs/e2e-tests.log)
- [docker/logs/market.log](docker/logs/market.log)
- [docker/logs/migrate.log](docker/logs/migrate.log)
- [docker/logs/otel-collector.log](docker/logs/otel-collector.log)
- [docker/logs/postgres.log](docker/logs/postgres.log)
- [docker/logs/quilkin.log](docker/logs/quilkin.log)
- [docker/logs/rabbitmq.log](docker/logs/rabbitmq.log)
- [docker/logs/settlement-worker.log](docker/logs/settlement-worker.log)
- [docker/logs/simulator.log](docker/logs/simulator.log)
- [docker/logs/trade-settlement.log](docker/logs/trade-settlement.log)
- [docker/metadata.json](docker/metadata.json)
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
