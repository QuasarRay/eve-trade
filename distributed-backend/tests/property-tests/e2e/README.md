# EVE Trade Comprehensive Hypothesis Contract Suite

This package implements the complete test-name catalog from `eve_trade_comprehensive_test_catalog.zip` as Python/Hypothesis tests.

## Coverage registry

The generated registry contains exactly:

- **131** existing E2E test names, each exposed as a Hypothesis property that reruns the original native E2E node under generated process/hash state.
- **1,353** v3 gap-test names, each exposed as a Hypothesis property and routed to a concrete `trade`, `edge`, `repo`, or `fault` runner.
- **1,484** unique named contracts in total.
- **97** v3 implementation categories.

Run the zero-dependency structural audit before installing anything:

```bash
python tools/verify_generated_catalog.py
```

It fails if a catalog name is missing, duplicated, malformed, or assigned to no runner.


## Logic-audit repair status

This is the repaired build. The source-level audit of the first generated suite found direct semantic mismatches and a systemic trust-oracle problem in external-driver contracts. The repaired version uses protocol-v2 raw evidence, case-hash binding, name-specific predicates, and explicit semantic overrides for weak legacy branches. See `FIXES_AFTER_LOGIC_AUDIT.md`, `audit_resolution_manifest.json`, `semantic_override_manifest.json`, and `evidence_specs.json`.

## Why some properties use external drivers

Hypothesis can generate data and schedules, but it cannot truthfully create every deployment-specific failure by itself. A property such as “kill the worker after its processing transition,” “partition NSQ from the worker,” “fail over the PostgreSQL primary after commit,” or “prove the EKS IAM policy denies an action” requires the real deployment/control plane.

Those tests are **not implemented as no-op assertions**. They call the external fault/evidence protocol in `drivers/README.md`. In strict mode, a required driver that is absent causes a failure. In developer mode, unavailable environment-specific contracts skip rather than falsely pass.

The Python suite directly exercises contracts for which the current E2E helper surface is sufficient, including seeded trade state, issue/accept/cancel, settlement rows, wallets/items/escrows, replay/HMAC/UDP handling, conservation, concurrency, database assertions, and many repository/configuration checks. Environment-specific crash, failover, rollout, cloud, and CI-control-plane assertions are delegated by exact test name to a driver.

## Install

Use Python 3.12+ and install the suite dependencies:

```bash
python -m pip install -r requirements.txt
```

The EVE Trade checkout itself must also have the dependencies required by `distributed-backend/tests/e2e/requirements.txt` and its generated protobuf helpers.

## Point the suite at EVE Trade

```bash
export EVE_TRADE_REPO_ROOT=/path/to/eve-trade
export EVE_TRADE_TARGET_BRANCH=experimental
```

PowerShell:

```powershell
$env:EVE_TRADE_REPO_ROOT = 'E:\path\to\eve-trade'
$env:EVE_TRADE_TARGET_BRANCH = 'experimental'
```

Use `main` or `experimental` as appropriate. The 17 existing `test_udp_pool_regressions.py` contracts are experimental-only; they are non-applicable when explicitly testing a `main` checkout.

## Run

Development mode, where unavailable external systems are reported as skips:

```bash
python -m pytest -q
```

Strict mode, recommended for a production-quality verification gate:

```bash
python -m pytest -q --eve-hypothesis-strict
```

Strict mode can also be enabled with:

```bash
EVE_TRADE_HYPOTHESIS_STRICT=1
```

The existing E2E production gate environment variable also implies strict behavior.

## Hypothesis example counts

The defaults deliberately keep live E2E properties small because each example resets persistent state and may cross multiple services. Increase them in dedicated property/stress jobs:

- `EVE_TRADE_HYPOTHESIS_NATIVE_EXAMPLES` — existing native wrappers, default `2`.
- `EVE_TRADE_HYPOTHESIS_LIVE_EXAMPLES` — live trade/edge contracts, default `3`.
- `EVE_TRADE_HYPOTHESIS_STATIC_EXAMPLES` — repository/tooling checks, default `5`.
- `EVE_TRADE_HYPOTHESIS_MODEL_EXAMPLES` — fault/generated schedule cases, default `40` in config; fault decorators default conservatively unless overridden.

Hypothesis shrinking remains active. When a generated example exposes a failure, the failing case is minimized before pytest reports it.

## Live E2E environment

The live adapters deliberately import EVE Trade's own `distributed-backend/tests/e2e/helpers.py` rather than recreating its protocol. Therefore the same environment used by the native E2E suite must be configured: PostgreSQL DSN/runtime credentials, gateway/Quilkin endpoint, settlement gRPC endpoint, HMAC credentials, NSQ endpoint where required, and generated protobuf modules.

## Fault/evidence drivers

For exhaustive strict execution, configure:

```bash
export EVE_TRADE_FAULT_DRIVER='python /path/to/fault_driver.py'
export EVE_TRADE_EVIDENCE_DRIVER='python /path/to/evidence_driver.py'
```

or use the included `drivers/mapped_contract_driver.py` for both and provide a JSON command map. See `drivers/README.md` for the exact JSON protocol.

## Package layout

- `eve_trade_hypothesis/catalog.json` — immutable name registry generated from the combined catalog.
- `eve_trade_hypothesis/generated/` — pytest modules exposing all 1,484 named contracts.
- `eve_trade_hypothesis/contracts/trade_contracts.py` — live business/database properties.
- `eve_trade_hypothesis/contracts/edge_contracts.py` — UDP/HMAC/replay/gRPC/protocol properties.
- `eve_trade_hypothesis/contracts/repo_contracts.py` — source, CI, Terraform, Kubernetes, Protobuf, dependency, security, and configuration properties.
- `eve_trade_hypothesis/contracts/fault_contracts.py` — deterministic fault-injection delegation.
- `eve_trade_hypothesis/registry.py` — strict dynamic Hypothesis registration.
- `tests/test_catalog_registry.py` — meta-tests proving complete registry coverage.
- `tools/verify_generated_catalog.py` — structural verification that does not require Hypothesis.

## Design rule

A missing capability must never become a green test. Direct assertions either prove the named invariant against the checkout/live system, or the test requires explicit external evidence. Strict mode turns unavailable dependencies and missing drivers into failures.
