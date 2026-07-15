# Live end-to-end tests

This package exercises the canonical simulator -> Quilkin UDP -> API Gateway
-> Market -> Encore Pub/Sub worker -> trade-settlement -> PostgreSQL path. The test
names are intentionally not copied into this file because a hand-maintained
list previously drifted and exaggerated the active suite.

Generate the authoritative catalog from collected executable tests:

```sh
python -m pytest distributed-backend/tests/e2e --collect-only -q
```

Production-gate runs set `EVE_TRADE_E2E_PRODUCTION_GATE=true`. In that mode,
any skipped test fails the session. The live gate also provides authenticated
seller/buyer edge credentials, the runtime database role, the direct
trade-settlement endpoint, and the simulator/Quilkin endpoints, so none of the
security, privilege, load, or settlement-contract groups may disappear behind
a skip.

Concurrency and load-sensitive scenarios are repeated three times in CI after
the complete suite. The full suite remains the source of truth; neither a raw
test count nor this document is presented as a count of independent risks.
