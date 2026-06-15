mod db;
mod dbv2;
mod error;
mod generated;
mod service;
mod telemetry;

use summer::App;
use summer_grpc::GrpcPlugin;
use summer_sqlx::SqlxPlugin;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = telemetry::init()?;
    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://postgres:postgres@localhost:5432/eve_trade".to_string());

    let sqlx_config = format!(
        "[sqlx]\nuri = \"{}\"\n",
        toml_basic_string_escape(&database_url)
    );

    tracing::info!(
        service.name = "trade-settlement",
        otel.exporter =
            std::env::var("OTEL_TRACES_EXPORTER").unwrap_or_else(|_| "auto".to_string()),
        "starting trade-settlement service"
    );

    let mut app = App::new();
    app.use_config_file("./config/default.toml");
    app.merge_config_str(&sqlx_config)?;
    app.add_plugin(SqlxPlugin)
        .add_plugin(GrpcPlugin)
        .run()
        .await;

    telemetry.shutdown();
    Ok(())
}

fn toml_basic_string_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}
