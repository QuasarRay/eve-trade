# Changes V5

Date: 2026-06-24

## Trade GUI Simulator

- Added a root-level Django REST Framework simulator under `simulator/`.
- Implemented trade-only EVE Online GUI controls with DRF `ModelViewSet` classes:
  - `GameGuiButtonViewSet` exposes the trade GUI buttons and a `press` action.
  - `GameGuiInteractionViewSet` records packets sent from the simulator.
- Seeded simulator buttons for the EVE trade surfaces documented by EVE University:
  - Regional Market sell order creation.
  - Regional Market buy-from-sell-order action.
  - Regional Market order cancellation.
  - Item-exchange contract creation and acceptance.
  - Direct trade offer and acceptance.
- Added the simulator browser UI at `/`, limited to trade-related windows only: Regional Market, Contracts, Direct Trade, and Wallet Orders.
- Added `simulator/Dockerfile`, Django settings, migrations, serializers, admin registration, templates, and the `seed_trade_gui` management command.

## Quilkin And UDP Gateway Path

- Added `distributed-backend/docker/quilkin.Dockerfile`, which installs `quilkin` binary from the `quilkin` crate and builds `eve-trade/quilkin:dev`.
- Updated Compose and Kubernetes to run Quilkin with its UDP service enabled on `26001/udp` and a static endpoint pointing to the API gateway UDP listener on `26000/udp`.
- Added the `RUSTFLAGS="-C target-feature=+aes,+sse2"` build setting required by Quilkin 0.10's `gxhash` dependency.
- Added the seccomp-unconfined runtime setting required by Quilkin 0.10's `io_uring` UDP backend in Docker/Kubernetes.
- Added an API gateway UDP listener for Quilkin-compatible datagrams.
- Added API gateway configuration:
  - `API_GATEWAY_QUILKIN_UDP_ADDR`, default `:26000`.
  - `API_GATEWAY_QUILKIN_UDP_ENABLED`, default `true`.
  - `API_GATEWAY_QUILKIN_MAX_PACKET_BYTES`, default `8192`.
- The API gateway validates only the transport envelope constraints, then forwards the raw packet bytes to market through gRPC without interpreting or mutating the GUI payload.
- Added `scripts/quilkin_udp_fallback.py`, a local UDP proxy from `127.0.0.1:26001` to `127.0.0.1:26000`, so local simulator runs can proceed when the Quilkin container image cannot be pulled.
- Updated `docker-compose.integration.local.yml` to expose only `127.0.0.1:26000/udp` for the API gateway while keeping the other integration ports isolated.

## Proto And Market Mapping

- Added `SubmitTradeGuiInteraction` to the market and API gateway proto services.
- Regenerated the Go proto and Connect bindings with `buf generate`.
- Added `SubmitTradeGuiInteractionRequest` with:
  - `source_transport`.
  - `source_address`.
  - `raw_payload`.
- Added `SubmitTradeGuiInteractionResponse` with:
  - `interaction_id`.
  - `mapped_operation`.
  - a result for issue, accept, or cancel operations.
- Updated API gateway handlers and market clients to forward the raw simulator/Quilkin packet to market.
- Updated market to parse the exact trade GUI packet at the market boundary and map EVE trade UI actions to existing settlement-producing operations:
  - `market_place_sell_order`, `contract_create_item_exchange`, and `direct_trade_offer` map to `IssueTradeInstance`.
  - `market_buy_from_sell_order`, `contract_accept_item_exchange`, and `direct_trade_accept` map to `AcceptTradeInstance`.
  - `market_cancel_order`, `contract_cancel_item_exchange`, and `direct_trade_cancel` map to `CancelTradeInstance`.

## Download And Local Run Pipeline

- Added `scripts/downloads/fetch_simulator_deps.py`.
  - Downloads simulator Python wheels into `.downloads/python-wheels`.
  - Tries bounded Quilkin image pulls without waiting indefinitely.
  - Falls back to `scripts/quilkin_udp_fallback.py` when Quilkin image pulls fail.
- Added `dagger/pipeline.py`.
  - Provides a Dagger pipeline for simulator Python wheel download.
  - Provides a separate bounded Quilkin image pull task.
  - Leaves the Python fallback proxy path available if Dagger or Quilkin image downloads are unavailable.
- Updated `.gitignore` for generated local artifacts:
  - `.downloads/`
  - `simulator/db.sqlite3`
  - `simulator/.venv/`
  - `distributed-backend/docker/local-bin/`

## Compose And Runtime

- Updated `compose.yaml` with `quilkin` and `simulator` services.
- Changed Compose to build the project-owned `eve-trade/quilkin:dev` image instead of pulling a remote Quilkin image.
- Removed the old `simulator/quilkin.yaml` config file because Quilkin 0.10 is configured with explicit service/provider flags in the project manifests.
- Exposed `26000/udp` from the API gateway Dockerfile.
- Wired the simulator container to send UDP packets to Quilkin.
- Kept the simulator and Quilkin transport UDP-only; gRPC remains inside the API gateway-to-market backend boundary.

## Kubernetes Local Cluster

- Added `distributed-backend/orchestration/kubernetes/overlay/local/`.
- Added local Kubernetes resources for:
  - PostgreSQL.
  - Local development database secrets.
  - Local development world seed data.
  - Quilkin UDP proxy.
  - Django simulator.
- Patched the local migration job to wait for PostgreSQL before applying the settlement schema.
- Added a local seed job that waits for the schema, then inserts the seeded capsuleers, wallets, item stacks, and item-stack ledger rows required for successful simulator trades.
- Updated the base API gateway Kubernetes Service and Deployment to expose `26000/udp` for Quilkin traffic.
- Reduced local overlay replicas to one per backend service for the Docker Desktop Kubernetes cluster.
- Disabled OpenTelemetry exporters in the local overlay so missing observability infrastructure does not block local runs.
- Configured the Kubernetes simulator to use writable `/tmp/db.sqlite3` under a non-root, read-only-root-filesystem pod.

## Validation

- `buf generate` completed successfully.
- `go test ./...` passed in `distributed-backend/src/api-gateway`.
- `go test ./...` passed in `distributed-backend/src/market`.
- `go test ./...` passed in `distributed-backend/proto`.
- `docker compose -f compose.yaml config --quiet` passed.
- `python scripts/downloads/fetch_simulator_deps.py --timeout 60` downloaded simulator Python wheels successfully.
- Quilkin image pulls for `ghcr.io/embarkstudios/quilkin:0.9.0` and `us-docker.pkg.dev/quilkin/release/quilkin:0.1.0` failed quickly with TLS handshake timeouts, so the local fallback UDP proxy was used instead of waiting.
- Created a simulator virtual environment from the downloaded wheels and ran:
  - `python manage.py check`
  - `python manage.py migrate --noinput`
  - `python manage.py seed_trade_gui`
- Rebuilt local runtime-only Docker images for `api-gateway`, `market`, `settlement-worker`, and `trade-settlement`.
- Full local integration workflow passed with `109 passed in 59.82s`.
- Verified direct UDP to the API gateway at `127.0.0.1:26000/udp`; market returned the expected structured `invalid_argument` response for an unsupported GUI action.
- Started the fallback UDP proxy on `127.0.0.1:26001` and verified simulator-path UDP traffic reached market.
- Started the Django simulator on `http://127.0.0.1:8010/`.
- Verified `GET /api/gui/buttons/` returned seeded trade GUI buttons.
- Verified `POST /api/gui/buttons/1/press/` sent a valid Regional Market sell-order packet through Django, UDP, API gateway, market, and settlement, returning:
  - `mapped_operation: IssueTradeInstance`
  - a new `trade_instance_id`
  - a new `item_stack_escrow_id`
  - a new `settlement_batch_id`
- Built the real Quilkin image with `docker build -f distributed-backend/docker/quilkin.Dockerfile --build-arg QUILKIN_VERSION=0.10.0 -t eve-trade/quilkin:dev .`.
- Verified the image reports `quilkin 0.10.0`.
- Built Kubernetes runtime images:
  - `eve-trade/api-gateway:dev`
  - `eve-trade/market:dev`
  - `eve-trade/settlement-worker:dev`
  - `eve-trade/trade-settlement:dev`
  - `eve-trade/simulator:dev`
- Applied the full local Kubernetes overlay with `kubectl apply -k distributed-backend/orchestration/kubernetes/overlay/local`.
- Confirmed all local cluster workloads are ready:
  - `api-gateway` deployment `1/1`
  - `market` deployment `1/1`
  - `postgres` deployment `1/1`
  - `quilkin` deployment `1/1`
  - `settlement-worker` deployment `1/1`
  - `simulator` deployment `1/1`
  - `trade-settlement` deployment `1/1`
  - `rabbitmq` statefulset `1/1`
- Confirmed both Kubernetes jobs completed:
  - `settlement-db-migrate`
  - `local-dev-world-seed`
- Verified Quilkin logs show `Starting Quilkin`, the static endpoint `10.96.121.180:26000`, and `starting udp service` on port `26001`.
- Verified the Kubernetes API gateway logs show `api-gateway Quilkin UDP listener active` on `:26000`.
- Verified the in-cluster simulator API returns seeded buttons from `GET /api/gui/buttons/`.
- Verified an in-cluster `POST /api/gui/buttons/1/press/` created a sell order through simulator HTTP, simulator UDP, Quilkin, API gateway UDP, market, and settlement, returning:
  - `mapped_operation: IssueTradeInstance`
  - `trade_instance_id: 2a093995-058b-43b1-9e40-5d83c371c4f9`
  - `item_stack_escrow_id: 0eef9703-315f-4d9d-a984-18407ae8e12e`
  - `settlement_batch_id: 760e811b-2364-44e4-8fd5-b557acfea384`
- Started a Kubernetes port-forward for the simulator with `kubectl -n eve-trade port-forward svc/simulator 8010:8000`.
- Verified host access through the port-forward with `GET http://127.0.0.1:8010/api/gui/buttons/` returning HTTP `200`.
- Verified `docker compose -f compose.yaml config --quiet` still passes after switching Compose to the project-built Quilkin image.
