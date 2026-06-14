use std::{collections::HashMap, env};

use opentelemetry::{trace::TracerProvider as _, KeyValue};
use opentelemetry_otlp::{Protocol, SpanExporter, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::{trace::SdkTracerProvider, Resource};
use tracing_subscriber::{filter::EnvFilter, layer::SubscriberExt, util::SubscriberInitExt, Layer};

const HONEYCOMB_TRACES_ENDPOINT: &str = "https://api.honeycomb.io/v1/traces";

pub(crate) struct TelemetryGuard {
    tracer_provider: Option<SdkTracerProvider>,
}

impl TelemetryGuard {
    pub(crate) fn shutdown(mut self) {
        if let Some(provider) = self.tracer_provider.take() {
            if let Err(error) = provider.shutdown() {
                tracing::warn!(%error, "failed to shut down OpenTelemetry provider");
            }
        }
    }
}

impl Drop for TelemetryGuard {
    fn drop(&mut self) {
        if let Some(provider) = self.tracer_provider.take() {
            if let Err(error) = provider.shutdown() {
                tracing::warn!(%error, "failed to shut down OpenTelemetry provider");
            }
        }
    }
}

pub(crate) fn init() -> anyhow::Result<TelemetryGuard> {
    let _ = tracing_log::LogTracer::init();

    let env_filter = EnvFilter::try_from_env("RUST_LOG").unwrap_or_else(|_| {
        EnvFilter::new("info,sqlx=info,tonic=info,h2=warn,hyper=warn,trade_settlement=debug")
    });
    let fmt_layer = build_fmt_layer();

    let Some(exporter_config) = OtlpExporterConfig::from_env() else {
        tracing_subscriber::registry()
            .with(env_filter)
            .with(fmt_layer)
            .try_init()?;
        return Ok(TelemetryGuard {
            tracer_provider: None,
        });
    };

    let exporter = SpanExporter::builder()
        .with_http()
        .with_endpoint(exporter_config.endpoint)
        .with_protocol(Protocol::HttpBinary)
        .with_headers(exporter_config.headers)
        .build()?;

    let service_name = env_value("OTEL_SERVICE_NAME")
        .or_else(|| env_value("SERVICE_NAME"))
        .unwrap_or_else(|| "trade-settlement".to_string());
    let deployment_environment = env_value("DEPLOYMENT_ENVIRONMENT")
        .or_else(|| env_value("ENVIRONMENT"))
        .unwrap_or_else(|| "local".to_string());

    let resource = Resource::builder()
        .with_service_name(service_name)
        .with_attributes([
            KeyValue::new("service.version", env!("CARGO_PKG_VERSION")),
            KeyValue::new("deployment.environment.name", deployment_environment),
        ])
        .build();

    let tracer_provider = SdkTracerProvider::builder()
        .with_batch_exporter(exporter)
        .with_resource(resource)
        .build();
    let tracer = tracer_provider.tracer(env!("CARGO_PKG_NAME"));
    let otel_layer = tracing_opentelemetry::layer().with_tracer(tracer).boxed();

    tracing_subscriber::registry()
        .with(env_filter)
        .with(fmt_layer)
        .with(otel_layer)
        .try_init()?;

    Ok(TelemetryGuard {
        tracer_provider: Some(tracer_provider),
    })
}

fn build_fmt_layer<S>() -> Box<dyn Layer<S> + Send + Sync + 'static>
where
    S: tracing::Subscriber + for<'a> tracing_subscriber::registry::LookupSpan<'a>,
{
    let json = env_value("TRACING_JSON")
        .map(|value| matches!(value.as_str(), "1" | "true" | "TRUE" | "yes" | "YES"))
        .unwrap_or_else(|| env_value("ENVIRONMENT").as_deref() == Some("production"));

    if json {
        tracing_subscriber::fmt::layer()
            .json()
            .flatten_event(true)
            .with_current_span(true)
            .with_span_list(true)
            .boxed()
    } else {
        tracing_subscriber::fmt::layer().compact().boxed()
    }
}

struct OtlpExporterConfig {
    endpoint: String,
    headers: HashMap<String, String>,
}

impl OtlpExporterConfig {
    fn from_env() -> Option<Self> {
        let traces_exporter = env_value("OTEL_TRACES_EXPORTER");
        if traces_exporter
            .as_deref()
            .is_some_and(|value| value.eq_ignore_ascii_case("none"))
        {
            return None;
        }

        let has_honeycomb_key = env_value("HONEYCOMB_API_KEY").is_some();
        let otlp_requested = traces_exporter
            .as_deref()
            .is_some_and(|value| value.eq_ignore_ascii_case("otlp"));
        let endpoint = env_value("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            .or_else(|| env_value("OTEL_EXPORTER_OTLP_ENDPOINT"))
            .or_else(|| has_honeycomb_key.then(|| HONEYCOMB_TRACES_ENDPOINT.to_string()));

        if !otlp_requested && endpoint.is_none() && !has_honeycomb_key {
            return None;
        }

        let mut headers = parse_headers("OTEL_EXPORTER_OTLP_HEADERS");
        headers.extend(parse_headers("OTEL_EXPORTER_OTLP_TRACES_HEADERS"));

        if let Some(api_key) = env_value("HONEYCOMB_API_KEY") {
            headers.insert("x-honeycomb-team".to_string(), api_key);
        }
        if let Some(dataset) = env_value("HONEYCOMB_DATASET") {
            headers.insert("x-honeycomb-dataset".to_string(), dataset);
        }

        Some(Self {
            endpoint: endpoint.unwrap_or_else(|| HONEYCOMB_TRACES_ENDPOINT.to_string()),
            headers,
        })
    }
}

fn parse_headers(name: &str) -> HashMap<String, String> {
    env_value(name)
        .map(|headers| {
            headers
                .split(',')
                .filter_map(|header| header.split_once('='))
                .map(|(key, value)| (key.trim().to_string(), value.trim().to_string()))
                .filter(|(key, value)| !key.is_empty() && !value.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

fn env_value(name: &str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}
