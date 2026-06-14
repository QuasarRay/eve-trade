from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from helpers.db import MUTABLE_TABLES, TradeDatabase, env_flag
from helpers.proto import compile_proto_stubs, load_proto_modules
from helpers.services import require_tcp_service, service_target


LIVE_MARKERS = {"gateway", "market", "settlement"}
LIVE_PASSED = 0
LIVE_SKIPPED = 0


def production_gate_enabled() -> bool:
    return env_flag("EVE_TRADE_E2E_PRODUCTION_GATE")


def pytest_configure(config) -> None:
    global LIVE_PASSED, LIVE_SKIPPED
    LIVE_PASSED = 0
    LIVE_SKIPPED = 0


def pytest_collection_modifyitems(config, items) -> None:
    if not production_gate_enabled():
        return
    if env_flag("EVE_TRADE_E2E_ALLOW_SKIPS"):
        raise pytest.UsageError(
            "EVE_TRADE_E2E_ALLOW_SKIPS is forbidden in the production e2e gate."
        )

    live_items = [item for item in items if "live" in item.keywords]
    if not live_items:
        raise pytest.UsageError(
            "Production e2e gate collected zero live tests. Run the full e2e suite."
        )

    missing_markers = sorted(
        marker
        for marker in LIVE_MARKERS
        if not any(marker in item.keywords for item in live_items)
    )
    if missing_markers:
        raise pytest.UsageError(
            "Production e2e gate is missing live marker coverage: "
            + ", ".join(missing_markers)
        )


def pytest_runtest_logreport(report) -> None:
    global LIVE_PASSED, LIVE_SKIPPED
    if report.when != "call" or "live" not in report.keywords:
        return
    if report.passed:
        LIVE_PASSED += 1
    if report.skipped:
        LIVE_SKIPPED += 1


def pytest_sessionfinish(session, exitstatus) -> None:
    if not production_gate_enabled():
        return
    if LIVE_SKIPPED:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    if LIVE_PASSED == 0:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when not in {"setup", "call"} or not report.failed:
        return
    db = getattr(item, "_eve_trade_db", None)
    artifact_dir = Path(
        os.environ.get(
            "EVE_TRADE_E2E_ARTIFACT_DIR",
            str(Path(".pytest_cache") / "eve_trade_e2e_artifacts"),
        )
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodeid": item.nodeid,
        "phase": report.when,
        "longrepr": str(report.longrepr),
        "targets": {
            "gateway": os.environ.get("EVE_TRADE_GATEWAY_GRPC", "localhost:8080"),
            "market": os.environ.get("EVE_TRADE_MARKET_GRPC", "localhost:8081"),
            "settlement": os.environ.get(
                "EVE_TRADE_SETTLEMENT_GRPC", "localhost:9092"
            ),
        },
    }
    if db is not None:
        payload["table_counts"] = {
            table: db.table_count(table)
            for table in MUTABLE_TABLES
        }
    artifact_name = item.nodeid.replace("/", "_").replace("\\", "_").replace(":", "_")
    (artifact_dir / f"{artifact_name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


@pytest.fixture(scope="session")
def proto_modules(pytestconfig):
    generated_dir = pytestconfig.cache.mkdir("eve_trade_proto_py")
    compile_proto_stubs(generated_dir)
    return load_proto_modules(generated_dir)


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "EVE_TRADE_DATABASE_URL",
        "postgres://postgres:postgres@localhost:5432/eve_trade_e2e",
    )


@pytest.fixture(scope="session")
def database_preflight(database_url) -> None:
    db = TradeDatabase.connect(database_url)
    try:
        db.assert_schema_ready()
        if env_flag("EVE_TRADE_RESET_DATABASE"):
            db.reset_mutable_state()
    finally:
        db.close()


@pytest.fixture
def trade_db(request, database_url, database_preflight):
    db = TradeDatabase.connect(database_url)
    request.node._eve_trade_db = db
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
