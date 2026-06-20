import os

import pytest

from helpers import Database, GatewayClient, SettlementClient, wait_for_database, wait_for_gateway


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("EVE_TRADE_E2E_ALLOW_ALL_SKIPPED") == "true":
        return
    if session.testscollected == 0 or exitstatus != pytest.ExitCode.OK:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    skipped = len(reporter.stats.get("skipped", []))
    if skipped == session.testscollected:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture(scope="session")
def service_urls():
    api_gateway_url = os.environ.get("EVE_TRADE_API_GATEWAY_URL")
    database_url = os.environ.get("EVE_TRADE_DATABASE_URL")
    if not api_gateway_url or not database_url:
        pytest.skip(
            "set EVE_TRADE_API_GATEWAY_URL and EVE_TRADE_DATABASE_URL to run e2e tests"
        )
    return api_gateway_url, database_url


@pytest.fixture(scope="session", autouse=True)
def services_ready(service_urls):
    api_gateway_url, database_url = service_urls
    wait_for_database(database_url)
    wait_for_gateway(api_gateway_url)


@pytest.fixture
def db(service_urls, services_ready):
    _, database_url = service_urls
    database = Database(database_url)
    database.reset()
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def gateway(service_urls, services_ready):
    api_gateway_url, _ = service_urls
    return GatewayClient(api_gateway_url)


@pytest.fixture
def settlement(service_urls, services_ready):
    endpoint = os.environ.get("EVE_TRADE_SETTLEMENT_GRPC")
    if not endpoint:
        pytest.skip("set EVE_TRADE_SETTLEMENT_GRPC to run settlement contract tests")
    client = SettlementClient(endpoint)
    try:
        yield client
    finally:
        client.close()
