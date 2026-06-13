mod db;
mod error;
mod generated;
mod service;

use summer::App;
use summer_grpc::GrpcPlugin;
use summer_sqlx::SqlxPlugin;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://postgres:postgres@localhost:5432/eve_trade".to_string());

    let sqlx_config = format!(
        "[sqlx]\nuri = \"{}\"\n",
        toml_basic_string_escape(&database_url)
    );

    let mut app = App::new();
    app.use_config_file("./config/default.toml");
    app.merge_config_str(&sqlx_config)?;
    app.add_plugin(SqlxPlugin)
        .add_plugin(GrpcPlugin)
        .run()
        .await;

    Ok(())
}

fn toml_basic_string_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}
