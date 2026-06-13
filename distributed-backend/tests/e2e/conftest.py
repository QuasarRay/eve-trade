from __future__ import annotations

import os

import pytest

from helpers.db import TradeDatabase
from helpers.proto import compile_proto_stubs, load_proto_modules
from helpers.services import require_tcp_service, service_target


@pytest.fixture(scope="session")
def proto_modules(pytestconfig):
    generated_dir = pytestconfig.cache.mkdir("eve_trade_proto_py")
    compile_proto_stubs(generated_dir)
    return load_proto_modules(generated_dir)


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "EVE_TRADE_DATABASE_URL",
        "postgres://postgres:postgres@localhost:5432/eve_trade",
    )


@pytest.fixture
def trade_db(database_url):
    db = TradeDatabase.connect_or_skip(database_url)
    db.assert_schema_ready()
    db.reset_mutable_state()
    yield db
    db.close()


@pytest.fixture(scope="session")
def settlement_target() -> str:
    target = service_target("EVE_TRADE_SETTLEMENT_GRPC", "localhost:9092")
    require_tcp_service(target, "trade-settlement")
    return target


@pytest.fixture(scope="session")
def market_target() -> str:
    target = service_target("EVE_TRADE_MARKET_GRPC", "localhost:8081")
    require_tcp_service(target, "market")
    return target


@pytest.fixture(scope="session")
def gateway_target() -> str:
    target = service_target("EVE_TRADE_GATEWAY_GRPC", "localhost:8080")
    require_tcp_service(target, "api-gateway")
    return target
