# eve-trade

eve-trade is a modular MMORPG trade-system in development, which integrates into a game-server, receives trade requests from that game-server, performs them, and returns a response.

## Goal

incrementally grow into a production ready trade-system that is compatible with the trade system of EVE Online

## Current Status

Early foundation phase:

- PostgreSQL schema exists.
- gRPC/protobuf contracts exist.
- trade-settlement on the verge of completion
- market is at the initial development stage
- api-gateway is to be implemented in the future
- orchestration, CI/CD, containerization, Observability, and IAC are to be implemented in the future