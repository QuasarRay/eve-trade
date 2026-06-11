mod db;
mod error;
mod generated;
mod service;

use summer::App;
use summer_grpc::GrpcPlugin;

// This block starts the executable. It initializes the database pool before
// summer-grpc begins accepting requests, because trade-settlement cannot honestly
// return COMPLETED without an available authoritative database.
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let database_url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| "postgres://postgres:postgres@localhost:5432/eve_trade".to_string());

    db::initialize_pool(&database_url).await?;

    App::new().add_plugin(GrpcPlugin).run().await;

    Ok(())
}
