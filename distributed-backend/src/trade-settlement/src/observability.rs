use std::env;

use summer::app::AppBuilder;
use summer_opentelemetry::{KeyValue, OpenTelemetryPlugin, ResourceConfigurator};

const DEFAULT_SERVICE_NAMESPACE: &str = "eve-trade";
const OTEL_SERVICE_NAME: &str = "OTEL_SERVICE_NAME";
const OTEL_SERVICE_NAMESPACE: &str = "OTEL_SERVICE_NAMESPACE";

pub fn configure(app: &mut AppBuilder) -> &mut AppBuilder {
    app.opentelemetry_attrs([
        KeyValue::new("service.name", service_name()),
        KeyValue::new("service.namespace", service_namespace()),
        KeyValue::new("service.version", env!("CARGO_PKG_VERSION")),
    ])
    .add_plugin(OpenTelemetryPlugin)
}

fn service_name() -> String {
    env::var(OTEL_SERVICE_NAME).unwrap_or_else(|_| env!("CARGO_PKG_NAME").to_string())
}

fn service_namespace() -> String {
    env::var(OTEL_SERVICE_NAMESPACE).unwrap_or_else(|_| DEFAULT_SERVICE_NAMESPACE.to_string())
}
