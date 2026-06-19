use summer::App;
use summer_grpc::GrpcPlugin;
use summer_sqlx::SqlxPlugin;

#[tokio::main]
async fn main() {
    std::env::set_current_dir(env!("CARGO_MANIFEST_DIR"))
        .expect("failed to set trade-settlement working directory");
    trade_settlement::service::ensure_linked();

    App::new()
        .add_plugin(SqlxPlugin)
        .add_plugin(GrpcPlugin)
        .run()
        .await;
}
