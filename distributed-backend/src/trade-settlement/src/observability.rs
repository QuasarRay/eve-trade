use std::env;

use summer::app::AppBuilder;
use summer_opentelemetry::{KeyValue, OpenTelemetryPlugin, ResourceConfigurator};

const DEFAULT_SERVICE_NAMESPACE: &str = "eve-trade";
const OTEL_SERVICE_NAME: &str = "OTEL_SERVICE_NAME";
const OTEL_SERVICE_NAMESPACE: &str = "OTEL_SERVICE_NAMESPACE";
const OBSERVABILITY_RUN_ID: &str = "OBSERVABILITY_RUN_ID";

pub fn configure(app: &mut AppBuilder) -> &mut AppBuilder {
    app.opentelemetry_attrs([
        KeyValue::new("service.name", service_name()),
        KeyValue::new("service.namespace", service_namespace()),
        KeyValue::new("service.version", env!("CARGO_PKG_VERSION")),
        KeyValue::new("service.language", "rust"),
        KeyValue::new(
            "deployment.environment",
            env::var("DEPLOYMENT_ENVIRONMENT").unwrap_or_else(|_| "development".to_string()),
        ),
        KeyValue::new(
            "observability.run_id",
            env::var(OBSERVABILITY_RUN_ID).unwrap_or_else(|_| "unobserved".to_string()),
        ),
    ])
    .add_plugin(OpenTelemetryPlugin)
}

fn service_name() -> String {
    env::var(OTEL_SERVICE_NAME).unwrap_or_else(|_| env!("CARGO_PKG_NAME").to_string())
}

fn service_namespace() -> String {
    env::var(OTEL_SERVICE_NAMESPACE).unwrap_or_else(|_| DEFAULT_SERVICE_NAMESPACE.to_string())
}
