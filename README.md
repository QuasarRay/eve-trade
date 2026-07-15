# eve-trade

`eve-trade` is a backend/platform slice for an EVE-like trade flow.

Canonical path:

`game frontend -> Quilkin UDP -> Encore gateway -> Market -> Encore Pub/Sub settlement work -> settlement worker -> Rust trade-settlement`

The Go backend is one Encore application rooted at this repository root. Encore owns Go service APIs and Go-to-Go calls. The UDP edge remains a thin custom adapter because the game-facing Quilkin protocol is UDP, not HTTP. The Rust `trade-settlement` service remains a separate gRPC/protobuf service and owns settlement database transactions.

## Run Locally

Prerequisites:

* Encore CLI
* Go 1.26
* PostgreSQL with the settlement schema applied
* Rust `trade-settlement` running on `127.0.0.1:9092` when settlement execution is required

Start the Go backend:

```powershell
./scripts/run-local.ps1
```

This runs `encore run`, starts Encore HTTP on `http://localhost:4000`, and starts the retained UDP gateway adapter on `localhost:26000`.

Local Encore Pub/Sub is used by `encore run`. Self-hosted Kubernetes builds use `infra/encore/self-host.nsq.json` and an NSQ workload.

## Runtime Responsibilities

The gateway package owns UDP-only concerns: packet size limits, empty-packet rejection, bounded queue/workers, HMAC integrity, replay protection, principal-bound rate limiting, downstream timeouts, compact UDP responses, and telemetry.

Market owns game-trade interpretation. It maps GUI issue, accept, and cancel actions into explicit settlement work and publishes typed work to Encore Pub/Sub. Market does not write settlement database state directly.

The settlement worker owns asynchronous settlement execution. Its Encore subscription consumes at-least-once settlement work, converts the typed work to the Rust protobuf request, calls `trade-settlement` over standard gRPC, and publishes typed settlement results.

`trade-settlement` owns correctness-critical persistence: atomic settlement transactions, idempotency records, item ownership transfer, wallet transfer, escrow release, and settlement state.

## Platform

Kubernetes deploys:

* `encore-backend` for all Go Encore services plus the UDP adapter
* `nsqd` for self-hosted Encore Pub/Sub
* `trade-settlement` for the Rust settlement service
* `quilkin` for the external UDP proxy
* PostgreSQL through the selected platform path

Terraform supports EKS, GKE, and Talos/Omni. CI installs a pinned Encore CLI, validates the Encore app, runs Go and Rust checks, builds the Encore image with `encore build docker`, renders Kubernetes, validates Terraform roots, and runs the remaining Python and observed integration checks.
