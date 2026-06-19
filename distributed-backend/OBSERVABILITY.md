# Observability

Services emit vendor-neutral OpenTelemetry signals to the in-cluster collector.
Application packages should not import Honeycomb, Sentry, Prometheus, or raw
OpenTelemetry SDK packages directly. Add service-level instrumentation in the
service observability adapter and keep backend-specific routing in the collector.

## Go Services

`api-gateway` and `market` use the shared Go adapter in:

```text
distributed-backend/src/observability
```

The adapter initializes:

- OTLP HTTP trace, metric, and log exporters.
- Global OpenTelemetry tracer, meter, logger, and propagation providers.
- Connect RPC server/client interceptors through `connectrpc.com/otelconnect`.
- A `slog` tee handler that keeps JSON stdout logs and exports logs to OTLP.
- Go runtime and host metrics for Prometheus collection through the collector.

The service entry points only call `observability.Init` and pass interceptor
options into their existing server and client constructors. Business handlers,
market logic, and generated protobuf packages stay free of observability SDKs.

`api-gateway` uses:

- External server interceptor for inbound gateway RPCs.
- Client interceptor for outbound calls to `market`.

`market` uses:

- Internal server interceptor for inbound RPCs from `api-gateway`.
- Client interceptor for outbound calls to `trade-settlement`.

The Go services are independent modules, so they depend on the adapter with a
local replace:

```text
github.com/astral/eve-trade/observability => ../observability
```

## Rust Service

The Rust service registers `summer-opentelemetry` during startup before the SQLx
and gRPC plugins. The dependency is compiled with the plugin's HTTP OTLP feature
because the Kubernetes manifests send telemetry to the collector on port `4318`.

Runtime enablement is controlled by:

```toml
[opentelemetry]
enable = true
```

The service exports traces, metrics, and logs through standard OTEL environment
variables:

```text
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.eve-trade-observability.svc.cluster.local:4318
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=otlp
OTEL_LOGS_EXPORTER=otlp
OTEL_PROPAGATORS=tracecontext,baggage
OTEL_METRIC_EXPORT_INTERVAL=15000
```

`OTEL_SERVICE_NAME` and `OTEL_SERVICE_NAMESPACE` are turned into OpenTelemetry
resource attributes by the service adapters. Sentry routes traces and logs by
`service.name`, so create Sentry projects whose slugs match the service names or
add explicit routing mappings in the collector config.

## Collector Routing

The static collector manifests live in:

```text
distributed-backend/orchestration/kubernetes/base/observability
```

The collector receives OTLP over HTTP and gRPC, then fans out telemetry:

- Honeycomb receives traces, metrics, and logs through `otlphttp/honeycomb`.
- Sentry receives traces and logs through the collector `sentry` exporter.
- Prometheus scrapes metrics from the collector's Prometheus exporter at
  `otel-collector.eve-trade-observability.svc.cluster.local:9464/metrics`.

The Sentry path is intentionally OTLP traces and logs only. Native Sentry error
events should be added with a Sentry SDK in a separate adapter if the project
later needs first-class error-event capture.

Create or replace the `observability-backends` secret in the
`eve-trade-observability` namespace before production use:

```text
HONEYCOMB_API_KEY
SENTRY_AUTH_TOKEN
SENTRY_ORG_SLUG
SENTRY_URL
```

`SENTRY_URL` defaults to `https://sentry.io` in the template. The Sentry token
must be able to read organization/project metadata. If you enable collector-side
project creation later, it will also need project write permissions.

## Deployment Order

Apply observability before the application manifests so OTLP exports have a
collector endpoint available:

```powershell
kubectl apply -k distributed-backend\orchestration\kubernetes\base\observability
kubectl apply -k distributed-backend\orchestration\kubernetes\overlay\prod
```

The production network policy allows application pods to egress only to the
collector on OTLP HTTP port `4318`; vendor egress happens from the collector.
