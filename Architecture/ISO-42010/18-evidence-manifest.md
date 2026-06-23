# Evidence Manifest

## Manifest Metadata

| Field | Value |
| --- | --- |
| Manifest ID | `EVIDENCE-EVE-TRADE-ISO42010-2026-06-23` |
| Date | 2026-06-24 |
| Status | Working-tree evidence baseline for the ISO 42010-informed architecture documents |
| Source commit inspected | `fe5c6af1dcb68715ccb339a00912729a4febdf2d` |
| Source branch inspected | `main` |
| Manifest rule | This manifest excludes its own file hash to avoid self-referential churn. |
| Certification status | Not certified; purchased-standard clause audit not performed. |

## Baseline Interpretation

The implementation evidence is anchored to Git commit
`fe5c6af1dcb68715ccb339a00912729a4febdf2d`. The architecture documentation
changes are content-addressed by the SHA-256 hashes below until they are
committed. A later commit should replace this file-hash baseline with the final
commit hash and update the manifest. The 2026-06-24 item-ledger update was
validated against the current dirty working tree rather than a committed source
revision.

## Architecture File Hashes

| File | SHA-256 |
| --- | --- |
| `Architecture/ISO-42010/00-architecture-description.md` | `d33f1971b2e6186aec8b9f494cb3b82e873742b4dba6a81a65db0ceee60cbae2` |
| `Architecture/ISO-42010/01-stakeholders-concerns-perspectives.md` | `de68028a24b148a811bc3a1780f76d2ac877e3403623b873cfb969b0deb1da56` |
| `Architecture/ISO-42010/02-viewpoints.md` | `e4ee7045bf54674ce176e48631c904919727d15fc3e4cce7e704d0c43eb8c011` |
| `Architecture/ISO-42010/03-context-view.md` | `9598c7aa52a83a002bc76fbeebc7ed50794dbf967b3647370e0c50145b4f2444` |
| `Architecture/ISO-42010/04-functional-runtime-view.md` | `5bd0e578e7ba01e7aed775c39fe9dc14335518e31393498e4c01c4bb95cb5fb0` |
| `Architecture/ISO-42010/05-information-data-integrity-view.md` | `7a13ed391d89879c6263b72c31bfdab60b2e4688e69ea0c500f84ac6ad761596` |
| `Architecture/ISO-42010/06-deployment-operations-view.md` | `db9bcba64d971bd0231c87a34b0844c11cff8228da7926f1cb3ca013d520ed95` |
| `Architecture/ISO-42010/07-security-trust-view.md` | `7c0c1dfc3cd72aa6d1b7142c230cfabf576e4f78d182c0c66ac22f1f0770a9f6` |
| `Architecture/ISO-42010/08-development-validation-view.md` | `4456802d1152defcc424d262a8d474e897612bb1e15f2c774ab1256b609b5229` |
| `Architecture/ISO-42010/09-correspondences-rationale.md` | `30b242d248acc88bde9c014fd8585022e0ed35ba500320feb188f53e869d1089` |
| `Architecture/ISO-42010/10-conformance-checklist.md` | `78690904daee8c0fd6d0a6b90a51fc7a2bae4bbeb00a1f6aa604ca57ba620b23` |
| `Architecture/ISO-42010/11-performance-capacity-view.md` | `9ef2b1f5d36f42096b7720535605b0f5814255212cbf70d255bc3946a76505f1` |
| `Architecture/ISO-42010/12-resilience-recovery-view.md` | `9d90cc9f988f58d10f6b6f73de6b58183d595a4c67ce3a941021c68faac8172a` |
| `Architecture/ISO-42010/13-observability-view.md` | `19de903626d5bbcf533ebc052b0b8edbf56a6492602fe1763d305756e9bd5aa7` |
| `Architecture/ISO-42010/14-threat-model-view.md` | `13db48c561b82391ac720ee8a9b4fdccdd36fced9cdf31ac9e70985dd7d372d5` |
| `Architecture/ISO-42010/15-risk-register.md` | `69f7ab52a24329dbbbf70cf2d03ed0c1d031066e262ecc3ee4693ea500083d47` |
| `Architecture/ISO-42010/16-glossary.md` | `961de9ba5e941b68867c3d362d94dbc0fd0d043c02551283ac2faf60f2d9b2d5` |
| `Architecture/ISO-42010/17-adf-adl-specification.md` | `3c3185a44cdc4d6164aa3ee8f98c1ac5eff24005875d1b2ce3d5f94b5e2f788f` |
| `Architecture/ISO-42010/19-architecture-facts.md` | `048a79566208d994788b4bf12f2af833ce1fb783b09ca6b7aed3fb2f0a8115d8` |
| `Architecture/ISO-42010/20-stakeholder-review-governance.md` | `e2736cb164f1a08584c5b59a53f951a1546fda69585673513e0d6c875352e8ed` |
| `Architecture/Trade Request Lifecycle/v1.md` | `8ce7a688ba4309c6085714987afa6823df9472f00bfa345a8b5e70482dea2615` |
| `Architecture/Trade State Lifecycle/v1.md` | `492f7d32138fbfb3c83a2a40e747ad32a41ba622a0bbdd17e98fa7beefc7e1b3` |
| `Architecture/Proto Architecture/v1.md` | `87823415db254a6676db9316a27482f0c44dbdfcbd49ef76a3cf2eeead8a216a` |
| `Architecture/Conceptual Database Schema/v1.md` | `1fedac219f697f0901f8f01ca97a8cfa2e90b06347646c76cebe27d1f2ca1108` |
| `Architecture/Canonical SQLx Design/v1.md` | `760cfa170df16bc72997e3a4ab5de6f70969bc857f8721222aec50b1169e0737` |

## Validation Result Register

| Validation ID | Command or check | Last result for this update | Evidence status |
| --- | --- | --- | --- |
| VAL-001 | Markdown relative-link check over `Architecture` | Passed on 2026-06-22 | Verified in this documentation update |
| VAL-002 | Heading anchor checker | Not implemented | Gap recorded |
| VAL-003 | `rg -n "T[O]DO|T[B]D|FIXM[E]" Architecture/ISO-42010 Architecture/changesv2.md` | Passed on 2026-06-22 | Verified in this documentation update |
| VAL-004 | `rg -n --pcre2 "[^\\x00-\\x7F]" Architecture/ISO-42010 Architecture/changesv2.md` | Passed on 2026-06-22 | Verified in this documentation update |
| VAL-005 | Architecture ID/schema linter | Not implemented | Gap recorded |
| VAL-006 | Mermaid render validation | Not implemented | Gap recorded |
| VAL-010 to VAL-080 | Go, Rust, Python e2e skip-mode, Compose, Kubernetes, and targeted vet/static validation | Passed on 2026-06-23 except live e2e flows were skipped because service/database URLs were not configured | Verified for this update with live-runtime gap recorded |
| VAL-060 | `kubectl kustomize` for Istio, Gateway, observability, and production overlays | Passed on 2026-06-23 | Verified in this infrastructure update |
| VAL-070 | Terraform/OpenTofu `fmt -check -recursive`, `init -backend=false`, and `validate` for deployment roots | EKS and GKE passed on 2026-06-23 with OpenTofu v1.10.0; Talos/Omni formatted on 2026-06-24, but local provider initialization was blocked by registry/provider download errors | Partially verified; CI matrix is configured for AWS/EKS, GCP/GKE, and Talos/Omni |
| VAL-080 | `python -m py_compile ci-cd/pipeline.py` and `python ci-cd/pipeline.py --help` in a temp venv | Passed on 2026-06-23; full Dagger/GitHub/GitLab execution not run locally | Partially verified |
| VAL-090 | `rg -n "settlement_attempt\|trade_state_change_ledger\|remaining quantity effect\|operation allow rule\|allowlist" Architecture/ISO-42010 --glob "!18-evidence-manifest.md"` | Passed on 2026-06-23 with no stale ISO matches outside the evidence manifest | Verified in this documentation update |
| VAL-091 | Focused responsibility/transport searches over ISO records for requested-operation wording, `request_attempt`, `trade_state_change`, and direct/connect transport caveats | Passed on 2026-06-23 | Verified in this documentation update |
| VAL-092 | `cargo fmt --all -- --check`, `cargo check --locked`, and `cargo test --locked` in `distributed-backend/src/trade-settlement`; `kubectl kustomize` for Kubernetes base and production overlays; `docker compose config --quiet`; source-vs-Kubernetes migration copy comparison; item-ledger writer search | Passed on 2026-06-24; Rust crate has zero tests; live PostgreSQL migration apply was not run because Docker engine and local PostgreSQL tools were unavailable | Verified for this item-ledger update with live-database gap recorded |
| VAL-093 | Talos/Omni portability update checks: `terraform fmt -check -recursive distributed-backend/terraform`, `python -m py_compile ci-cd/pipeline.py`, `python ci-cd/pipeline.py terraform --help`, and `kubectl kustomize` for production, Gateway, Istio, and observability manifests | Passed on 2026-06-24; local Terraform provider init/validate for the final Talos/Omni root was blocked by registry/provider download errors | Partially verified with provider-download gap recorded |

## Source Anchor Register

| Evidence ID | Claim area | Exact anchor |
| --- | --- | --- |
| EVID-001 | API Gateway public trade RPCs | `distributed-backend/proto/eve/api_gateway/v1/api_gateway.proto`, service `GameTradeGatewayService` |
| EVID-002 | Market service RPCs and idempotency fields | `distributed-backend/proto/eve/market/v1/market.proto`, service `MarketService`, request fields `idempotency_key` |
| EVID-003 | Settlement service RPC and operation enum | `distributed-backend/proto/eve/trade_settlement/v1/trade_settlement.proto`, `TradeSettlementService.ExecuteSettlementBatch`, `SettlementOperationKind` |
| EVID-004 | Market trade planning tests | `distributed-backend/src/market/game-trade/trade_instance_test.go`, tests for issue, accept, cancel, expired trade |
| EVID-005 | API Gateway forwarding tests | `distributed-backend/src/api-gateway/distributed-backend/handler_test.go`, `TestGatewayHandlerForwards*` |
| EVID-006 | Market handler replay/error tests | `distributed-backend/src/market/distributed-backend/handler_test.go`, replay conflict and unavailable settlement tests |
| EVID-007 | Settlement executor transaction/idempotency behavior | `distributed-backend/src/trade-settlement/src/executor.rs`, `execute_batch`, savepoint helpers, idempotency completion/failure helpers |
| EVID-008 | Settlement command conversion validation | `distributed-backend/src/trade-settlement/src/commands.rs`, `TryFrom<pb::ExecuteSettlementBatchRequest>` for `ExecuteBatchCommand` |
| EVID-009 | RabbitMQ settlement topology and worker branches | `distributed-backend/src/messaging/rabbitmqsettlement/config.go`, `worker.go`, and `publish_test.go` |
| EVID-010 | Settlement schema, hash-chained item ledgers, wallet ledgers, idempotency, and metadata | `distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql` |
| EVID-011 | Timeout configuration | `distributed-backend/src/api-gateway/distributed-backend/config.go`, `distributed-backend/src/market/distributed-backend/config.go`, `distributed-backend/src/settlement-worker/cmd/settlement-worker/config.go`, Kubernetes `configmaps.yaml` |
| EVID-012 | Service health/readiness handlers | API Gateway, Market, and settlement-worker server/health files under `distributed-backend/src` |
| EVID-013 | Kubernetes deployment, probe, network, and policy manifests | `distributed-backend/orchestration/kubernetes/base`, `overlay/prod`, `platform/gateway/prod`, and `platform/istio/prod` |
| EVID-014 | Local Compose runtime and healthchecks | `compose.yaml` |
| EVID-015 | Production placeholder values | Production kustomization, HTTPRoute/Gateway hostnames, ClusterIssuer email, and Istio issuer/JWKS values under `distributed-backend/orchestration/kubernetes` |
| EVID-016 | Deployment infrastructure roots | `distributed-backend/terraform/eks`, `distributed-backend/terraform/gke`, `distributed-backend/terraform/talos-omni`, and shared modules under `distributed-backend/terraform/lib` |
| EVID-020 | Observability assets | `distributed-backend/OBSERVABILITY.md` and Kubernetes observability manifests |
| EVID-021 | CI/release validation entry points | `.github/workflows/verify.yaml` and `ci-cd` |

## Evidence Gaps

| Gap | Current handling |
| --- | --- |
| Live e2e trade-flow validation was not run. | Static/unit/render validation passed; e2e package ran in skip-allowed mode with 109 skipped because live service/database URLs were not configured. |
| Stakeholder sign-off was not obtained. | Recorded in `20-stakeholder-review-governance.md`. |
| Mermaid diagrams were not rendered. | Recorded as VAL-006 and RISK-016. |
| Architecture linter does not exist. | Recorded as VAL-005 and RISK-016. |
| Source anchors are mostly file/symbol level, not line-level. | Accepted for this remediation pass; future linter/source trace can deepen anchors. |
| Live PostgreSQL migration execution was not run for the item-ledger schema update. | Docker Desktop engine was unavailable and `psql`/PostgreSQL server binaries were not installed locally; static SQL/render/Rust validation passed. |
