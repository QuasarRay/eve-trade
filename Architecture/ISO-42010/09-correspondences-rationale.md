# Correspondences and Rationale

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical current state |
| Last reviewed | 2026-06-25 |
| Governing framework | eve-trade Architecture Description Framework |
| Evidence baseline | v6 architecture cleanup; starting commit recorded in `changes/v6/changes.md` |

## Purpose

This document records cross-view correspondences and the rationale for material
architecture decisions after the v6 cleanup. It intentionally removes the old
command-shaped public API model.

## Correspondence Matrix: Packet Path To Service Implementation

| Runtime concern | Source view element | Implementation evidence | Current correspondence |
| --- | --- | --- | --- |
| Game packet shape | Context/runtime views identify `eve-trade-gui.v1` payload. | `simulator/trade_gui/views.py`, `simulator/trade_gui/tests.py` | Simulator outbound payload is validated from the actual socket send path and contains only game GUI fields plus player input. |
| UDP envelope integrity | Security/runtime views identify signed edge envelope. | `simulator/trade_gui/udp_client.py`, `gateway/auth.go`, `gateway/response.go` | Simulator signs canonical payload; Encore gateway verifies HMAC before forwarding. |
| UDP edge forwarding | Context/runtime views keep Encore gateway at transport boundary. | `gateway/udp.go`, `gateway/packet.go`, `gateway/proto_validation.go`, `gateway/udp_test.go`, `proto/eve/api_gateway/v1/api_gateway.proto` | Gateway validates UDP envelope/config/actor binding through proto views and forwards `SubmitTradeGuiInteractionRequest{raw_payload}` in the current runtime path. |
| Market interpretation | Runtime/data views assign game trade interpretation to Market. | `market/gui.go`, `market/proto_validation.go`, `market/proto_service.go`, `proto/eve/market/v1/market.proto` | Market validates typed RPC/GUI payload shape through proto, then calls helper functions for issue/accept/cancel decisions. |
| Settlement execution | Data/runtime views assign only low-level operation batches to trade-settlement. | `proto/eve/trade_settlement/v1/trade_settlement.proto`, Rust executor | trade-settlement receives operation batches plus idempotency/audit metadata, not game trade mechanics. |
| Production overlay boundary | Deployment/security views separate local simulator and production backend. | `distributed-backend/orchestration/kubernetes/overlay/local`, `overlay/prod`, `scripts/verify_architecture_boundaries.py` | Local overlay includes simulator secrets/resources; production overlay includes Quilkin and excludes simulator resources. |
| CI enforcement | Development/validation view requires drift detection. | `.github/workflows/verify.yaml`, `scripts/verify_architecture_boundaries.py` | CI rejects removed RPCs, source metadata in gateway-to-Market contracts, simulator identity leaks, stale generated protos, and invalid manifests. |

## Correspondence Matrix: Settlement Operation To Data Effects

| Settlement operation family | Runtime owner | Primary data effects | Integrity view reference |
| --- | --- | --- | --- |
| Trade instance row operations | trade-settlement | `trade_instance`, settlement step output, trade state history. | Data integrity view |
| Item stack creation/transfer/merge operations | trade-settlement | `item_stack`, `item_stack_escrow`, hash-chained `item_stack_ledger` rows. | Data integrity view |
| Wallet escrow/transfer operations | trade-settlement | `wallet`, `wallet_escrow`, `wallet_ledger` rows. | Data integrity view |
| Settlement metadata operations | trade-settlement | `idempotency_record`, `settlement_batch`, `settlement_attempt`, `settlement_step`. | Data integrity view |

## Correspondence Matrix: Deployment Flow To Policy

| Runtime flow | Config or port evidence | Kubernetes policy evidence | Gap |
| --- | --- | --- | --- |
| Game UDP to Quilkin | Quilkin service UDP `26001` | Production Quilkin LoadBalancer service and `quilkin-ingress` NetworkPolicy. | External DDoS controls are outside this repo. |
| Quilkin to Encore gateway | Encore gateway UDP `26000` | `quilkin-egress` and Encore gateway UDP ingress NetworkPolicy. | None documented. |
| gateway to Market | `Encore service discovery for market.SubmitTradeGuiInteraction` | Encore gateway egress and Market ingress on `8081`; Istio policy allows only `SubmitTradeGuiInteraction`. | None documented. |
| Market to Encore Pub/Sub | Encore Pub/Sub `5672` | Market egress to Encore Pub/Sub and Encore Pub/Sub ingress from Market. | Broker-level per-service authorization not fully documented. |
| settlement worker to Encore Pub/Sub | Encore Pub/Sub `5672` | Worker egress to Encore Pub/Sub and Encore Pub/Sub ingress from worker. | Broker-level per-service authorization not fully documented. |
| settlement worker to trade-settlement | `TRADE_SETTLEMENT_GRPC_TARGET=trade-settlement:9092` | Worker egress and trade-settlement ingress on `9092`. | Depends on mesh/service-account policy in production. |
| Market and trade-settlement to PostgreSQL | `DATABASE_URL` | Broad TCP `5432` egress. | Destination is not selected by pod/namespace policy. |
| app pods to observability | OTLP `4317`/`4318` | Telemetry egress to collector namespace. | Alert/dashboard definitions remain incomplete. |

## Correspondence Rules

| ID | Method | Correspondence | Source AD element | Target AD element | Verification status |
| --- | --- | --- | --- | --- | --- |
| COR-01 | Constraint | Encore gateway runtime remains a UDP edge plus health/readiness surface; restored typed proto/gRPC contracts do not move Market business decisions into gateway. | Context view | API gateway proto, generated code, and gateway UDP service | Enforced by gateway tests and proto validation |
| COR-02 | Reuse | Encore gateway forwards the raw GUI payload into Market's runtime submission API while restored Market proto contracts define typed gRPC shapes for internal use. | Runtime view | Market proto and gateway UDP code | Enforced by tests |
| COR-03 | Refinement | Market GUI decisions refine to low-level `SettlementOperation` sequences. | Runtime view | trade-settlement proto/Rust handlers | Evidence-backed |
| COR-04 | Refinement | Settlement operation kinds correspond to SQL operation handlers and settlement step records. | Data view | Rust executor/migrations | Structurally represented |
| COR-05 | Dependency | Runtime service calls correspond to Kubernetes network policy allowances. | Context view | Deployment view | Gap recorded for database egress precision |
| COR-06 | Satisfaction | Health/readiness endpoints correspond to Kubernetes probes and operational readiness concerns. | Context view | Deployment view | Gap recorded for some dependency readiness |
| COR-07 | Trace | Idempotency keys and request fingerprints correspond across GUI payload, Market replay, Encore Pub/Sub messages, settlement metadata, and database rows. | Runtime view | Data view | Evidence-backed |
| COR-08 | Constraint | Simulator identity is allowed in local simulator database/log records but forbidden in outbound UDP payloads. | Context view | Simulator code/tests | Enforced by packet-boundary test |
| COR-09 | Constraint | Security trust boundaries correspond to deployment network boundaries and service responsibilities. | Security view | Deployment view | Gap recorded for identity binding and broad DB egress |
| COR-10 | Satisfaction | Validation commands correspond to the source modules and deployment assets they protect. | Development view | CI workflow | Partially verified locally; full compose e2e depends on Docker |

## Decision Record Register

| ADR | Status | Date recorded | Owner | Decision |
| --- | --- | --- | --- | --- |
| ADR-01 | Revised | 2026-07-08 | Backend maintainers | API gateway and Market proto/gRPC command-shaped contracts are restored with proto-owned validation; the runtime UDP edge still forwards raw GUI payloads and does not own trade mechanics. |
| ADR-02 | Accepted | 2026-06-25; updated 2026-07-08 | Backend maintainers | Market receives raw GUI interaction payloads in the current UDP runtime path and owns game trade interpretation; Market also owns typed trade proto request validation. |
| ADR-03 | Accepted | 2026-06-25 | Backend maintainers and security reviewer | Encore gateway keeps transport metadata internal and forwards no `source_transport` or `source_address` business fields to Market. |
| ADR-04 | Accepted | 2026-06-25 | Simulator owner | The Django simulator emits packets conforming to the versioned repository protocol schema and may identify itself only in private local records; external-client identity requires an external compatibility corpus. |
| ADR-05 | Accepted | 2026-06-25 | Settlement/data owner | trade-settlement receives only low-level settlement operation batches plus infrastructure idempotency/audit metadata. |
| ADR-06 | Accepted with limitations | 2026-06-25 | SRE/platform operator | UDP gateway resilience uses bounded queue/workers, per-remote rate limits, process-local replay cache, HMAC integrity, and downstream timeouts. |
| ADR-07 | Accepted | 2026-06-25 | SRE/platform operator | Production overlays include Quilkin UDP and exclude local simulator resources. |
| ADR-08 | Accepted | 2026-06-25 | CI owner | CI enforces architecture boundary drift guards, generated proto freshness, language checks, simulator packet tests, compose e2e, and manifest validation. |

### ADR-01 Restore Internal Command-Shaped Proto Contracts

Decision: Restore API Gateway and Market protobuf service contracts for direct
issue, accept, cancel, and GUI submission methods, with request-shape rules in
proto. Keep the UDP runtime gateway as a transport edge that forwards raw GUI
payloads to Market.

Rationale:

- The production packet boundary is a game GUI interaction, not an external
  market command API.
- Typed contracts reduce handwritten adapter code and allow gRPC use without
  duplicating validation in Go.
- Gateway command-shaped proto messages are contracts, not ownership of trade
  mechanics; Market remains the business owner.
- Market can expose typed proto service adapters while still accepting raw GUI
  payloads from the UDP runtime path.

Consequences:

- Generated Go message types include API gateway and Market packages.
- Local gRPC service stub generation requires `protoc-gen-go-grpc`; that tool
  was unavailable during the local v9 pass.
- Boundary checks must distinguish restored internal proto/gRPC contracts from
  runtime code that would move trade mechanics into the gateway.

### ADR-02 Market Owns GUI Interpretation

Decision: Define `market.SubmitTradeGuiInteraction Encore API` with
`SubmitTradeGuiInteractionRequest { RawPayload []byte; }`.

Rationale:

- Market owns game trade mechanics and can evolve parsing/decisioning without
  moving business meaning into the UDP edge.
- The Encore gateway contract remains boundary-clean because it forwards bytes
  only.

Consequences:

- Market tests cover GUI action mapping and durable settlement effects.
- Gateway tests assert only raw payload reaches Market.

### ADR-03 Keep Gateway Metadata Internal

Decision: Encore gateway may log remote address and transport metadata internally,
but Market request messages must not contain source transport or source address
fields.

Rationale:

- Gateway-specific metadata is operational context, not Market business input.
- Keeping this out of Market prevents future policy drift where game trade
  decisions depend on local simulator, browser, or transport identity.

### ADR-04 Make Simulator Packets Production-Identical

Decision: The simulator sends a canonical GUI payload with schema version,
interaction ID, UI window/control/action, and player-provided trade inputs only.
The packet is signed in an edge envelope and contains no Django, browser, test,
simulator, framework, environment, or source metadata.

Rationale:

- The simulator should exercise the same packet boundary as a real frontend.
- Local implementation details can remain in simulator database/log records but
  must not leave the process in UDP payloads.

### ADR-05 Keep Settlement Game-Mechanic Agnostic

Decision: trade-settlement remains a low-level operation executor and does not
receive game trade command RPCs.

Rationale:

- Settlement correctness belongs to database transactions, operation
  preconditions, idempotency, audit metadata, and ledgers.
- Market is the correct owner for game mechanics.

### ADR-06 Harden UDP Gateway

Decision: The UDP gateway uses bounded worker/queue processing, max-packet and
empty-packet rejection, per-remote rate limiting, HMAC integrity, replay
rejection, downstream timeouts, compact UDP responses, structured logs, and OTel
metrics.

Current limitation: the replay cache is process-local. Durable double-settlement
protection remains in Market/trade-settlement idempotency.

### ADR-07 Separate Local And Production Overlays

Decision: Local overlays may include simulator resources and local-only secrets.
Production overlays include Quilkin and backend services but not simulator
resources.

Rationale: The simulator is a local frontend simulator, not part of the backend
production platform.

### ADR-08 Make CI Enforce Architecture

Decision: Add architecture boundary checks and stricter language/deployment
verification so green CI means the production architecture has not drifted back
to the old command-RPC design.

Current limitation: Buf breaking checks against the pre-v6 baseline are not
enabled because v6 intentionally removes public RPCs. Generated freshness and
boundary guards are enforced instead.
