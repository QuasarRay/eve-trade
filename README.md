# eve-trade

eve-trade is a modular MMORPG trade-system in development, which integrates into a game-server, receives trade requests from that game-server, performs them, and returns a response.

## Goal

incrementally grow into a production ready trade-system that is compatible with the trade system of EVE Online

## Current Status

Current foundation:
- PostgreSQL trade schema exists.
- gRPC/protobuf contracts exist.
- CI verification exists for protobuf, Go market, and Rust trade-settlement.
- trade-settlement implements the initial atomic settlement path for stackable items.
- market implements initial order/fill/cancel/expire forwarding logic.
- api-gateway, orchestration, containerization, observability, and IaC remain future work.