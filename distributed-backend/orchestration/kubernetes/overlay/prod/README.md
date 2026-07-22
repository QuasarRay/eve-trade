# Production Overlay

This overlay deploys the Encore-native runtime topology:

* `quilkin`
* `encore-backend`
* `nsqd`
* `trade-settlement`
* PostgreSQL credentials supplied out of band

Required production secrets:

* `market-database` with read-only `MARKET_DATABASE_URL`
* `trade-settlement-database` with settlement writer `DATABASE_URL`
* `trade-settlement-migration-database` with migration `DATABASE_URL`
* `gateway-edge-auth` with `GAME_PACKET_HMAC_SECRET` and `GAME_PACKET_PRINCIPAL_KEYS_JSON`
* `nsq-server-tls` with `tls.crt`, `tls.key`, and `ca.crt`
* `nsq-client-tls` with `tls.crt`, `tls.key`, and `ca.crt`

The NSQ server certificate must contain the DNS SAN
`nsqd.eve-trade.svc.cluster.local`. The client certificate must chain to the
CA in `nsq-server-tls/ca.crt`. NSQD requires and verifies that certificate;
the restartable `nsq-client-proxy` init container exposes only a loopback
plaintext listener to Encore and originates TLS 1.3 with the client identity.
Port 4150 is excluded from Istio interception so the native NSQ mutual-TLS
session is not terminated by the service mesh.

`MARKET_DATABASE_URL` must use a PostgreSQL role with `CONNECT`, `USAGE` on
schema `public`, and `SELECT` only on:

* `item_stack`
* `wallet`
* `trade_instance`
* `item_stack_escrow`
* `idempotency_record`
* `settlement_batch`
* `settlement_step`

It must not have INSERT, UPDATE, DELETE, schema creation, migration, ledger
mutation, balance mutation, item-stack mutation, or trade-state mutation
privileges. `trade-settlement-database` remains exclusive to the Rust
settlement workload.

The protected `main` release job produces the Encore backend image with the
exact verified merge SHA as its tag and records the registry-returned manifest
digest in `release-image-lock.json`:

```bash
encore build docker --push --config infra/encore/self-host.nsq.json \
  "ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/eve-trade-encore-backend:${GITHUB_SHA}"
```

The Rust `trade-settlement` and Quilkin images remain separate non-Go images.
`scripts/render_release_kubernetes.py` binds all three immutable digests to the
same repository and SHA before the strict production manifest verifier runs.
