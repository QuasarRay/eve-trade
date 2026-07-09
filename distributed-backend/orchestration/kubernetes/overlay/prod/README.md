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

The Encore backend image is produced with:

```bash
encore build docker --config infra/encore/self-host.nsq.json registry.example.com/eve-trade/encore-backend:<tag>
```

The Rust `trade-settlement` and Quilkin images remain separate non-Go images.
