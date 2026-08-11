"""Rust trade-settlement verification with an isolated Dagger Postgres service."""
from __future__ import annotations

from _common import sh, stage, with_env, with_source
from _images import POSTGRES_IMAGE, RUST_IMAGE

DB_URL = "postgresql://eve:eve-test@postgres:5432/eve_trade_test?sslmode=disable"


async def run(dag) -> None:
    db = (
        dag.container()
        .from_(POSTGRES_IMAGE)
        .with_env_variable("POSTGRES_USER", "eve")
        .with_env_variable("POSTGRES_PASSWORD", "eve-test")
        .with_env_variable("POSTGRES_DB", "eve_trade_test")
        .with_exposed_port(5432)
        .as_service()
    )
    ctr = dag.container().from_(RUST_IMAGE)
    ctr = sh(
        ctr,
        [
            "apt-get update",
            "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends protobuf-compiler ca-certificates",
            "rm -rf /var/lib/apt/lists/*",
            "rustup component add rustfmt clippy",
        ],
    )
    ctr = with_source(ctr, dag)
    ctr = ctr.with_service_binding("postgres", db).with_workdir(
        "/src/distributed-backend/src/trade-settlement"
    )
    ctr = with_env(ctr, {"EVE_TRADE_TEST_DATABASE_URL": DB_URL, "RUST_BACKTRACE": "1"})
    ctr = sh(
        ctr,
        [
            "cargo fmt --all -- --check",
            "cargo check --locked --all-targets --all-features",
            "cargo test --locked --all-features",
            "cargo clippy --locked --all-targets --all-features -- -D warnings",
        ],
    )
    await ctr.sync()


if __name__ == "__main__":
    stage("rust", run)
