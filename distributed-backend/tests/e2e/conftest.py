import os

import pytest

from helpers import Database, GatewayClient, wait_for_database, wait_for_gateway


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
