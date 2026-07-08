# Evidence Manifest

## Manifest Metadata

| Field | Value |
| --- | --- |
| Manifest ID | `EVIDENCE-EVE-TRADE-ISO42010-V9` |
| Date | 2026-07-08 |
| Status | Working-tree evidence baseline for the v9 experimental refactor |
| Source commit compared | fetched `origin/main` `8ec73d600be7bbb5382d96d1f015848d3712c60a` |
| Source branch inspected | `experimental` |
| Certification status | Not certified; purchased-standard clause audit not performed. |

## Baseline Interpretation

This manifest is a repository evidence map for the current experimental
architecture after the v9 refactor. The detailed command log, files changed,
and residual risks are recorded in `changes/v9/changes.md`.

## Validation Result Register

| Validation ID | Command or check | Current result |
| --- | --- | --- |
| VAL-010 | Encore gateway Go unit tests | `ENCORERUNTIME_NOPANIC=1 go test ./gateway` passed locally after restoring API gateway proto validation for UDP envelope/config/actor binding and downstream response identity. |
| VAL-011 | Market/game-trade and settlement worker Go unit tests | `ENCORERUNTIME_NOPANIC=1 go test ./internal/gametrade` and `ENCORERUNTIME_NOPANIC=1 go test ./settlementworker` passed locally during v9 work. `ENCORERUNTIME_NOPANIC=1 go test ./market` fails on existing handler expectations for synchronous settlement/replay errors versus current queued async responses. Plain `go test ./...` remains blocked outside Encore unless the Encore runtime panic is disabled. |
| VAL-012 | Generated proto compilation plus Buf build/lint/generation freshness | `buf build`, `buf lint`, `buf generate`, and `go test ./proto/gen/eve/api_gateway/v1 ./proto/gen/eve/market/v1 ./proto/gen/eve/trade/v1 ./proto/gen/eve/trade_settlement/v1 ./proto/gen/eve/validation/v1` passed locally during v9 work. |
| VAL-013 | Protovalidate runtime integration | Go boundaries call `buf.build/go/protovalidate`; Rust trade-settlement calls `prost-protovalidate` with generated descriptors. `cargo test --manifest-path distributed-backend/src/trade-settlement/Cargo.toml --no-default-features` passed locally during v9 work. Local `protoc-gen-go-grpc` installation timed out, so gRPC service stubs were not generated in this environment. |
| VAL-020 | Simulator packet boundary test: `python manage.py test trade_gui` in `simulator` | Historical v6 pass; not rerun during this v9 validation pass |
| VAL-030 | Python compile check: `python -m compileall simulator distributed-backend/tests/e2e` | Historical v6 pass; not rerun during this v9 validation pass |
| VAL-040 | Protobuf lint/generation freshness | Enforced by `.github/workflows/verify.yaml`; local v9 run recorded in `changes/v9/changes.md` |
| VAL-050 | Architecture boundary drift guard | Enforced by `scripts/verify_architecture_boundaries.py` and CI |
| VAL-060 | Kubernetes local and production overlay render/validate | Enforced by CI with `kubectl kustomize` and kubeconform |
| VAL-070 | local Encore/Kubernetes e2e path | CI runs simulator -> Quilkin -> Encore gateway UDP -> Market -> settlement worker -> trade-settlement -> PostgreSQL through `Kubernetes/E2E runtime configuration` |
| VAL-080 | Go/Rust/Python security and strictness checks | Enforced by CI where tooling is practical: govulncheck, cargo audit, pip-audit, staticcheck, clippy, vet, race tests |

## Source Anchor Register

| Evidence ID | Claim area | Exact anchor |
| --- | --- | --- |
| EVID-001 | Restored API gateway proto/gRPC contract and gateway proto validation | `proto/eve/api_gateway/v1/api_gateway.proto`, `gateway/proto_validation.go` |
| EVID-002 | Restored Market proto/gRPC contract, GUI submission contract, and proto adapter | `proto/eve/market/v1/market.proto`, `market/api.go`, `market/proto_service.go`, `market/proto_validation.go` |
| EVID-003 | Encore gateway UDP edge hardening and raw-payload forwarding | `gateway/udp.go`, `gateway/packet.go`, `gateway/auth.go`, `gateway/response.go` |
| EVID-004 | Encore gateway boundary tests | `gateway/udp_test.go` |
| EVID-005 | Market GUI interpretation and private trade helpers | `market/handler.go` |
| EVID-006 | Market handler tests | `market/handler_test.go` |
| EVID-007 | Market internal trade-planning helpers | `internal/gametrade` |
| EVID-008 | Settlement service low-level operation contract | `proto/eve/trade_settlement/v1/trade_settlement.proto` |
| EVID-009 | trade-settlement executor and idempotency behavior | `distributed-backend/src/trade-settlement/src/executor.rs` |
| EVID-010 | Settlement schema, ledgers, idempotency, and metadata | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql` |
| EVID-011 | Simulator protocol-schema and golden-packet conformance | `protocol`, `simulator/trade_gui/tests.py`, gateway golden-packet test |
| EVID-012 | Simulator outbound packet leak test | `simulator/trade_gui/tests.py` |
| EVID-013 | E2E simulator-driven gateway client | `distributed-backend/tests/e2e/helpers.py`, `distributed-backend/tests/e2e/conftest.py` |
| EVID-014 | Local simulator/Quilkin/Encore gateway path | `Encore local run scripts`, `Kubernetes/E2E runtime configuration` |
| EVID-015 | Kubernetes Encore gateway/Quilkin hardening | `distributed-backend/orchestration/kubernetes/base/encore-backend.yaml`, `base/configmaps.yaml`, `overlay/prod/quilkin.yaml`, `overlay/prod/networkpolicies.yaml` |
| EVID-016 | Local-only simulator overlay separation | `distributed-backend/orchestration/kubernetes/overlay/local/simulator.yaml`, `overlay/local/secrets.yaml` |
| EVID-017 | Production service authorization path | `distributed-backend/orchestration/kubernetes/overlay/prod/istio-security.yaml` |
| EVID-018 | Architecture boundary CI guard | `scripts/verify_architecture_boundaries.py` |
| EVID-019 | CI workflow gates | `.github/workflows/verify.yaml` |
| EVID-020 | Observability assets | `distributed-backend/OBSERVABILITY.md` and Kubernetes observability manifests |
| EVID-021 | Deployment infrastructure roots | `distributed-backend/terraform/eks`, `distributed-backend/terraform/gke`, `distributed-backend/terraform/talos-omni` |
| EVID-022 | Reusable protovalidate rules | `proto/eve/validation/v1/validation_rules.proto`, `buf.yaml`, `buf.gen.yaml` |
| EVID-023 | Go protovalidate boundary calls | `gateway/proto_validation.go`, `market/proto_validation.go`, `internal/gametrade/validation.go`, `settlementworker/convert.go` |
| EVID-024 | Rust protovalidate boundary call | `distributed-backend/src/trade-settlement/src/commands.rs`, `distributed-backend/src/trade-settlement/build.rs`, `distributed-backend/src/trade-settlement/src/proto.rs` |

## Evidence Gaps

| Gap | Current handling |
| --- | --- |
| Buf breaking check against the older main baseline is not enabled. | The experimental branch intentionally relocates proto generation and restores API gateway/Market proto contracts with new validation annotations; generated freshness and buf lint/build are enforced instead. |
| Encore gateway replay cache is process-local. | Market and trade-settlement durable idempotency prevent duplicate settlement effects; distributed edge replay remains future work. |
| HMAC packet integrity does not bind account identity to capsuleer IDs. | Documented as a security/risk limitation. |
| Production image digests/secrets are placeholders in checked-in manifests. | CI renders/validates manifests; release injection and production secret provisioning remain operator responsibilities. |
| Full local e2e depends on runtime dependencies and Encore tooling availability. | Local inability to run the full path must be recorded in `changes/v9/changes.md`; package-level validation is recorded above. |
