# Observability

The Go backend is a single Encore application. Domain spans and structured logs live in `internal/observability`; Encore owns the Go service API boundary and Pub/Sub runtime instrumentation.

Preserved domain telemetry:

* UDP receive, validation, replay, rate-limit, and downstream timing metrics in `gateway`
* Market validation and issue/accept/cancel spans in `market`
* settlement work completion/failure logs in `settlementworker`
* Rust `trade-settlement` tracing and SQL telemetry

Deployment config still exports OpenTelemetry data to the configured collector through `OTEL_EXPORTER_OTLP_ENDPOINT`. Production overlays keep Honeycomb, Sentry, Prometheus, and Istio telemetry resources.

Transport-specific tracing helpers from the previous Go RPC and broker transports were removed with those transports. The remaining external boundary is the standard gRPC call from the settlement worker to Rust `trade-settlement`.
