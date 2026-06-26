# Evidence Manifest

## Manifest Metadata

| Field | Value |
| --- | --- |
| Manifest ID | `EVIDENCE-EVE-TRADE-ISO42010-V6` |
| Date | 2026-06-25 |
| Status | Working-tree evidence baseline for the v6 architecture cleanup |
| Source commit before work | `13baa27824010bd1fc3b4d17409a0dfe086d425c` |
| Source branch inspected | `main` |
| Certification status | Not certified; purchased-standard clause audit not performed. |

## Baseline Interpretation

This manifest is a repository evidence map for the current architecture after
the v6 cleanup. The detailed command log, files changed, and residual risks are
recorded in `changes/v6/changes.md`.

## Validation Result Register

| Validation ID | Command or check | Current result |
| --- | --- | --- |
| VAL-010 | API Gateway Go unit tests: `go test ./...` in `distributed-backend/src/api-gateway` | Passed locally during v6 work |
| VAL-011 | Market Go unit tests: `go test ./...` in `distributed-backend/src/market` | Passed locally during v6 work |
| VAL-012 | Generated proto Go tests: `go test ./...` in `distributed-backend/proto` | Passed locally during v6 work |
| VAL-020 | Simulator packet boundary test: `python manage.py test trade_gui` in `simulator` | Passed locally during v6 work |
| VAL-030 | Python compile check: `python -m compileall simulator distributed-backend/tests/e2e` | Passed locally during v6 work |
| VAL-040 | Protobuf lint/generation freshness | Enforced by `.github/workflows/verify.yaml`; local final run recorded in `changes/v6/changes.md` |
| VAL-050 | Architecture boundary drift guard | Enforced by `scripts/verify_architecture_boundaries.py` and CI |
| VAL-060 | Kubernetes local and production overlay render/validate | Enforced by CI with `kubectl kustomize` and kubeconform |
| VAL-070 | Compose e2e path | CI runs simulator -> Quilkin -> API Gateway UDP -> Market -> settlement-worker -> trade-settlement -> PostgreSQL through `docker-compose.integration.yml` |
| VAL-080 | Go/Rust/Python security and strictness checks | Enforced by CI where tooling is practical: govulncheck, cargo audit, pip-audit, staticcheck, clippy, vet, race tests |

## Source Anchor Register

| Evidence ID | Claim area | Exact anchor |
| --- | --- | --- |
| EVID-001 | Removed API Gateway public trade RPC contract | `distributed-backend/proto/eve/api_gateway/v1/api_gateway.proto` is deleted; generated API Gateway proto package is deleted |
| EVID-002 | Market one-RPC GUI submission contract | `distributed-backend/proto/eve/market/v1/market.proto`, `MarketService.SubmitTradeGuiInteraction`, `SubmitTradeGuiInteractionRequest.raw_payload` |
| EVID-003 | API Gateway UDP edge hardening and raw-payload forwarding | `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp.go` |
| EVID-004 | API Gateway boundary tests | `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp_test.go` |
| EVID-005 | Market GUI interpretation and private trade helpers | `distributed-backend/src/market/distributed-backend/handler.go` |
| EVID-006 | Market handler tests | `distributed-backend/src/market/distributed-backend/handler_test.go` |
| EVID-007 | Market internal trade-planning helpers | `distributed-backend/src/market/game-trade` |
| EVID-008 | Settlement service low-level operation contract | `distributed-backend/proto/eve/trade_settlement/v1/trade_settlement.proto` |
| EVID-009 | trade-settlement executor and idempotency behavior | `distributed-backend/src/trade-settlement/src/executor.rs` |
| EVID-010 | Settlement schema, ledgers, idempotency, and metadata | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql` |
| EVID-011 | Simulator production-identical packet construction | `simulator/trade_gui/views.py`, `simulator/trade_gui/udp_client.py` |
| EVID-012 | Simulator outbound packet leak test | `simulator/trade_gui/tests.py` |
| EVID-013 | E2E simulator-driven gateway client | `distributed-backend/tests/e2e/helpers.py`, `distributed-backend/tests/e2e/conftest.py` |
| EVID-014 | Local compose simulator/Quilkin/API Gateway path | `compose.yaml`, `docker-compose.integration.yml` |
| EVID-015 | Kubernetes API Gateway/Quilkin hardening | `distributed-backend/orchestration/kubernetes/base/api-gateway.yaml`, `base/configmaps.yaml`, `overlay/prod/quilkin.yaml`, `overlay/prod/networkpolicies.yaml` |
| EVID-016 | Local-only simulator overlay separation | `distributed-backend/orchestration/kubernetes/overlay/local/simulator.yaml`, `overlay/local/secrets.yaml` |
| EVID-017 | Production service authorization path | `distributed-backend/orchestration/kubernetes/overlay/prod/istio-security.yaml` |
| EVID-018 | Architecture boundary CI guard | `scripts/verify_architecture_boundaries.py` |
| EVID-019 | CI workflow gates | `.github/workflows/verify.yaml` |
| EVID-020 | Observability assets | `distributed-backend/OBSERVABILITY.md` and Kubernetes observability manifests |
| EVID-021 | Deployment infrastructure roots | `distributed-backend/terraform/eks`, `distributed-backend/terraform/gke`, `distributed-backend/terraform/talos-omni` |

## Evidence Gaps

| Gap | Current handling |
| --- | --- |
| Buf breaking check against the pre-v6 baseline is not enabled. | v6 intentionally removes public RPCs; generated freshness, buf lint/build, and architecture boundary guards are enforced instead. |
| API Gateway replay cache is process-local. | Market and trade-settlement durable idempotency prevent duplicate settlement effects; distributed edge replay remains future work. |
| HMAC packet integrity does not bind account identity to capsuleer IDs. | Documented as a security/risk limitation. |
| Production image digests/secrets are placeholders in checked-in manifests. | CI renders/validates manifests; release injection and production secret provisioning remain operator responsibilities. |
| Full local compose e2e depends on Docker availability. | CI runs the compose e2e path; local inability to run Docker must be recorded in `changes/v6/changes.md` if encountered. |
