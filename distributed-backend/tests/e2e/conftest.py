import os

import pytest

from helpers import (
    Database,
    AuthenticatedEdgeClient,
    GatewayClient,
    SettlementClient,
    wait_for_database,
    wait_for_gateway,
    wait_for_market,
    wait_for_pubsub,
    wait_for_pubsub_idle,
    wait_for_settlement,
    wait_for_simulator,
)
from production_gate import (
    REQUIRED_PRODUCTION_SETTINGS,
    production_gate_enabled as _production_gate_enabled,
    production_session_failed,
    validate_required_settings,
)


def production_gate_enabled():
    return _production_gate_enabled(os.environ.get("EVE_TRADE_E2E_PRODUCTION_GATE"))


def require_or_skip(condition, message):
    if condition:
        return
    if production_gate_enabled():
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


def pytest_sessionfinish(session, exitstatus):
    production_gate = production_gate_enabled()
    if not production_gate and os.environ.get("EVE_TRADE_E2E_ALLOW_ALL_SKIPPED") == "true":
        return
    if exitstatus != pytest.ExitCode.OK:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = len(reporter.stats.get("skipped", []))
    failed = (
        production_session_failed(
            tests_collected=session.testscollected,
            skipped=skipped,
            pytest_exit_ok=True,
        )
        if production_gate
        else skipped == session.testscollected
    )
    if failed:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(scope="session")
def service_urls():
    encore_url = os.environ.get("EVE_TRADE_ENCORE_URL")
    simulator_url = os.environ.get("EVE_TRADE_SIMULATOR_URL")
    database_url = os.environ.get("EVE_TRADE_DATABASE_URL")
    if production_gate_enabled():
        required = {name: os.environ.get(name) for name in REQUIRED_PRODUCTION_SETTINGS}
        try:
            validate_required_settings(required)
        except ValueError as exc:
            pytest.fail(str(exc), pytrace=False)
    require_or_skip(
        encore_url and simulator_url and database_url,
        "set EVE_TRADE_ENCORE_URL, EVE_TRADE_SIMULATOR_URL, and EVE_TRADE_DATABASE_URL to run e2e tests",
    )
    return encore_url, simulator_url, database_url


@pytest.fixture(scope="session")
def services_ready(service_urls):
    encore_url, simulator_url, database_url = service_urls
    wait_for_database(database_url)
    wait_for_gateway(encore_url)
    wait_for_market(encore_url)
    wait_for_simulator(simulator_url)
    if production_gate_enabled():
        wait_for_settlement(os.environ["EVE_TRADE_SETTLEMENT_GRPC"])
        wait_for_pubsub(os.environ["EVE_TRADE_NSQ_TCP"])


@pytest.fixture
def db(service_urls, services_ready):
    _, _, database_url = service_urls
    database = Database(database_url)
    nsq_http_url = os.environ.get("EVE_TRADE_NSQ_HTTP")
    if nsq_http_url:
        wait_for_pubsub_idle(nsq_http_url)
    database.reset()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def runtime_db(db, services_ready):
    runtime_url = os.environ.get("EVE_TRADE_RUNTIME_DATABASE_URL")
    require_or_skip(runtime_url, "set EVE_TRADE_RUNTIME_DATABASE_URL to run runtime-role tests")
    database = Database(runtime_url)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def market_db(db, services_ready):
    market_url = os.environ.get("EVE_TRADE_MARKET_DATABASE_URL")
    require_or_skip(market_url, "set EVE_TRADE_MARKET_DATABASE_URL to run market-role tests")
    database = Database(market_url)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def gateway(service_urls, services_ready):
    _, simulator_url, _ = service_urls
    client = GatewayClient(simulator_url)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def settlement(service_urls, services_ready):
    endpoint = os.environ.get("EVE_TRADE_SETTLEMENT_GRPC")
    require_or_skip(endpoint, "set EVE_TRADE_SETTLEMENT_GRPC to run settlement contract tests")
    client = SettlementClient(endpoint)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def authenticated_edge(services_ready):
    required = {
        "host": os.environ.get("EVE_TRADE_QUILKIN_UDP_HOST"),
        "response_secret": os.environ.get("EVE_TRADE_EDGE_RESPONSE_SECRET"),
    }
    require_or_skip(all(required.values()), "set authenticated edge test credentials to run principal-binding tests")
    client = AuthenticatedEdgeClient(
        required["host"],
        int(os.environ.get("EVE_TRADE_QUILKIN_UDP_PORT", "26001")),
        required["response_secret"],
        os.environ.get("EVE_TRADE_EDGE_RESPONSE_KEY_ID", "primary"),
    )
    try:
        yield client
    finally:
        client.close()
