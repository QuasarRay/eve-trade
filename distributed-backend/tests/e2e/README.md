# E2E Tests

These tests exercise the project through the same boundaries production uses:
protobuf contracts, gRPC streams, PostgreSQL state, and the trade instance
lifecycle.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r distributed-backend\tests\e2e\requirements.txt
```

## Run

Start PostgreSQL and the services first, then run:

```powershell
pytest distributed-backend\tests\e2e
```

The test harness generates Python protobuf stubs into `.pytest_cache` at runtime,
so generated files are not committed.

## Environment

The defaults match the local integration stack:

```powershell
$env:EVE_TRADE_DATABASE_URL = "postgres://postgres:postgres@localhost:5432/eve_trade"
$env:EVE_TRADE_SETTLEMENT_GRPC = "localhost:9092"
$env:EVE_TRADE_MARKET_GRPC = "localhost:8081"
$env:EVE_TRADE_GATEWAY_GRPC = "localhost:8080"
```

Live tests skip automatically when their required service endpoint is not
reachable. Contract tests still run without services.
