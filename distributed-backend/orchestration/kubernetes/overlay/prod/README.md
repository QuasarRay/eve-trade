# Production Overlay

This overlay deploys the Encore-native runtime topology:

* `quilkin`
* `encore-backend`
* `nsqd`
* `trade-settlement`
* PostgreSQL credentials supplied out of band

Required production secrets:

* `trade-settlement-database` with runtime `DATABASE_URL`
* `trade-settlement-migration-database` with migration `DATABASE_URL`
* `gateway-edge-auth` with `GAME_PACKET_HMAC_SECRET` and `GAME_PACKET_PRINCIPAL_KEYS_JSON`

The Encore backend image is produced with:

```bash
encore build docker --config infra/encore/self-host.nsq.json registry.example.com/eve-trade/encore-backend:<tag>
```

The Rust `trade-settlement` and Quilkin images remain separate non-Go images.
