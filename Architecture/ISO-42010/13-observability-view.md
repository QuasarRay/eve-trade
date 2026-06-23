# Observability View

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical |
| Last reviewed | 2026-06-23 |
| Governing viewpoint | VP-10 Observability |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

Governed by: [VP-10 Observability Viewpoint](./02-viewpoints.md#vp-10-observability-viewpoint)

## Concerns Addressed

This view addresses CON-14, CON-25, CON-32, CON-33, CON-34, and CON-35.

## Telemetry Map

Model ID: `MODEL-OBS-01`; view component ID: `VC-OBS-01`.

| Runtime step | Desired trace/log fields | Desired metrics or signals | Current implementation anchor | Current status |
| --- | --- | --- | --- | --- |
| API Gateway receives command | service name, RPC method, external request ID, idempotency key, actor ID where safe | request count, latency, error count | Shared observability module and API Gateway handler/server files | Instrumentation path exists; field completeness not verified |
| API Gateway calls Market | downstream URL, RPC method, timeout, status | downstream latency and errors | API Gateway Market client | Client path exists; dashboard not documented |
| Market validates snapshots | trade ID, item stack ID, wallet ID, validation outcome | validation failures by code, DB read latency | Market handler and repository | Needs explicit metric definitions |
| Market publishes settlement command | exchange, routing key, correlation ID, idempotency key | publish latency, returned/unroutable count, timeout count | RabbitMQ settlement messaging library | Logs/source path exist; metrics/alerts need verification |
| settlement-worker consumes command | queue, delivery tag, correlation ID, message ID | consumer lag, ack/nack count, handler latency | `rabbitmqsettlement/worker.go` and settlement-worker main | Logs/source path exist; metrics/alerts need verification |
| trade-settlement executes batch | settlement batch ID, idempotency key, attempt number, step kind, failure code/message | batch duration, success/failure count, DB transaction latency | Settlement metadata tables and executor | Metadata exists; telemetry coverage needs verification |
| PostgreSQL commits or fails | idempotency key, settlement batch ID when available | DB latency, lock waits, connection pool pressure | PostgreSQL, RDS, or Cloud SQL metrics outside service repo | DB metrics not defined in repo docs |
| Reply returns to Market | correlation ID, reply success, Connect code | reply latency, timeout count | RabbitMQ settlement messaging library | Messaging reply behavior exists; alerting not documented |

## Correlation Keys

| Key | Source | Current/desired propagation |
| --- | --- | --- |
| `external_request_id` | Upstream caller or API Gateway boundary | API Gateway, Market, settlement command, settlement metadata, logs. |
| `idempotency_key` | Upstream caller | API Gateway, Market, RabbitMQ message, trade-settlement, settlement metadata. |
| Request fingerprint | Market/trade-settlement request material | Settlement executor and idempotency metadata. |
| RabbitMQ correlation ID | Messaging client | Market pending reply, worker logs, reply publication. |
| `settlement_batch_id` | trade-settlement | Response, settlement metadata, logs, diagnosis. |
| `settlement_step_id` | trade-settlement | Operation output references, ledgers, failure diagnosis. |
| Trace/span ID | OpenTelemetry instrumentation | All service logs and spans where supported. |

## Alert Signal Table

| Alert | Suggested trigger | Owner | Data source | Status |
| --- | --- | --- | --- | --- |
| API Gateway elevated 5xx rate | Error rate above SLO threshold. | SRE/on-call | API Gateway request metrics/logs | Threshold not defined |
| Market settlement timeout spike | Timeout count above baseline. | SRE with backend owner | Market downstream error metrics/logs | Threshold not defined |
| RabbitMQ command queue depth | Sustained non-zero or growth above capacity threshold. | SRE | RabbitMQ queue metrics | Threshold not defined |
| DLQ non-zero sustained depth | Any sustained DLQ depth. | SRE and settlement owner | RabbitMQ DLQ metrics | Alert rule not defined |
| trade-settlement failed batch rate | Failed batches above baseline. | Settlement owner/on-call | Settlement metadata and service metrics | Threshold not defined |
| PostgreSQL lock or transaction latency | Lock wait/latency above threshold. | Database owner/SRE | PostgreSQL, RDS, or Cloud SQL metrics | Metric source not defined |
| Identity/auth policy failures | JWT or authorization failures spike. | Security/on-call | Istio/auth policy metrics/logs | Dashboard not documented |

## Dashboard And Query Register

| Diagnostic question | Dashboard or query need | Current status |
| --- | --- | --- |
| Is API Gateway timing out before Market returns? | Gateway latency/error dashboard joined with Market request logs by request/trace ID. | Not implemented in docs |
| Are settlement commands stuck? | RabbitMQ command queue depth, consumer lag, publish-return count, and worker readiness dashboard. | Not implemented in docs |
| Did a timed-out request commit? | SQL query by idempotency key against `idempotency_record`, `settlement_batch`, and `settlement_step`. | Query template documented in Recovery view |
| Which settlement operation failed? | Settlement step query by `settlement_batch_id` with `step_kind`, `failure_code`, and `failure_message`. | Query template documented in Recovery view |
| Are identity policies rejecting traffic? | Istio JWT/AuthZ denial dashboard by workload and route. | Not implemented in docs |

## Incident Query Needs

| Question | Data source |
| --- | --- |
| Did a timed-out request commit? | `idempotency_record`, `settlement_batch`, service logs by idempotency key. |
| Why did a settlement fail? | `settlement_step` failure fields, trade-settlement logs, request fingerprint. |
| Was a RabbitMQ command delivered more than once? | RabbitMQ delivery/correlation logs and idempotency metadata. |
| Which actor initiated a mutation? | Authenticated identity claim once implemented, `caused_by_capsuleer_id`, external request ID. |
| Is the system falling behind? | RabbitMQ queue depth, worker consumer lag, settlement latency, DB latency. |

## Observability Assertions

| Assertion | Enforcement tag | Evidence or gap |
| --- | --- | --- |
| Settlement metadata is the authoritative diagnostic source for durable execution outcome. | Enforced by schema | Settlement tables record idempotency, attempts, batches, and steps. |
| Distributed trace coverage across API Gateway, Market, messaging, worker, settlement, and DB effects is not verified. | Partially enforced | OpenTelemetry code exists; end-to-end trace verification not recorded. |
| Alert thresholds and dashboards are not defined in repository docs. | Gap recorded | Alert thresholds and dashboards are not defined in repository docs. |
