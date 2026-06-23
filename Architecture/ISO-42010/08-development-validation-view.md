# Development and Validation View

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical |
| Last reviewed | 2026-06-22 |
| Governing viewpoints | VP-07 Development and Validation, VP-11 Contract Compatibility and Evolution |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

Governed by: [VP-07 Development And Validation Viewpoint](./02-viewpoints.md#vp-07-development-and-validation-viewpoint)

## Concerns Addressed

This view addresses CON-28, CON-29, CON-30, CON-31, and CON-35.

## Source Module Model

| Area | Location | Responsibility |
| --- | --- | --- |
| Protobuf source | `distributed-backend/proto/eve` | API Gateway, Market, and trade-settlement service contracts. |
| Generated Go protobuf code | `distributed-backend/proto/gen` | Go clients and servers for protobuf contracts. |
| API Gateway service | `distributed-backend/src/api-gateway` | Game-facing Connect server and Market client. |
| Market service | `distributed-backend/src/market` | Trade mechanics, validation, repository reads, and settlement command publishing. |
| Messaging library | `distributed-backend/src/messaging/rabbitmqsettlement` | RabbitMQ topology, publish, consume, reply, and dead-letter behavior. |
| settlement-worker service | `distributed-backend/src/settlement-worker` | RabbitMQ consumer and trade-settlement client. |
| Observability Go module | `distributed-backend/src/observability` | Shared Go telemetry/logging support. |
| trade-settlement crate | `distributed-backend/src/trade-settlement` | Rust settlement service, SQL operations, migrations, configuration, and seed data. |
| E2E tests | `distributed-backend/tests/e2e` | Python end-to-end trade lifecycle tests. |
| Compose runtime | `compose.yaml` | Local development and integration runtime topology. |
| Kubernetes manifests | `distributed-backend/orchestration/kubernetes` | Cluster deployment, network, traffic, observability, and platform resources. |
| Terraform | `distributed-backend/terraform/eks`, `distributed-backend/terraform/gke`, and shared modules under `distributed-backend/terraform/lib` | AWS/EKS and GCP/GKE infrastructure plus runtime asset provisioning. |
| CI | `.github/workflows/verify.yaml` and `ci-cd` | Static validation, tests, image build, and integration pipeline definitions. |

## Change Impact Matrix

| Change type | Likely affected artifacts | Required validation focus |
| --- | --- | --- |
| Public trade command field | Protobuf source, generated code, API Gateway handler/client, Market handler, tests, docs | Contract compatibility and request validation. |
| Settlement operation kind | trade-settlement protobuf, generated Go/Rust code, Market settlement planner, worker, Rust operations, SQL invariants | Operation semantics and atomicity. |
| Database schema | SQL migrations, generated SQLx metadata if used, Market repository reads, Rust operations, seed data, Kubernetes migration manifests | Migration order, compatibility, constraints, and local seed validity. |
| RabbitMQ topology | Messaging library, Market config, settlement-worker config, Compose, Kubernetes ConfigMaps/Secrets | Publish/consume/reply behavior and dead-letter behavior. |
| Deployment ports or paths | Service config, Compose, Kubernetes manifests, network policies, probes, Gateway/Istio resources | Runtime reachability and readiness. |
| Security policy | API Gateway, Market auth logic, network policies, Istio security resources, Secrets, docs | Trust boundary preservation and abuse cases. |
| Observability fields | Shared observability code, service startup, manifests, dashboards/runbooks | Cross-service correlation and operational signal quality. |

## Validation Matrix

| Validation ID | Validation goal | Representative command or workflow | Required before merge | Last documented result in this architecture update | Notes |
| --- | --- | --- | --- | --- | --- |
| VAL-001 | Architecture Markdown links | Custom PowerShell relative-link check over `Architecture` | Yes for architecture-only changes | Passed on 2026-06-22 | Checks local relative links only. |
| VAL-002 | Architecture heading anchors | Markdown anchor checker or documented manual check | Yes for architecture-only changes | Not run | Required by ADF/ADL but no checker exists yet. |
| VAL-003 | Architecture unresolved-marker scan | Search `Architecture/ISO-42010` and `Architecture/changesv2.md` for standard unresolved work markers | Yes for architecture-only changes | Passed on 2026-06-22 | Prevents accidental placeholder text. |
| VAL-004 | Architecture ASCII scan | `rg -n --pcre2 "[^\\x00-\\x7F]" Architecture/ISO-42010 Architecture/changesv2.md` | Yes for architecture-only changes | Passed on 2026-06-22 | Keeps docs ASCII unless explicitly needed. |
| VAL-005 | Architecture ID/schema linting | Future architecture-doc linter for IDs, required sections, and table schemas | Yes when linter exists | Not implemented | Governance gate remains open. |
| VAL-006 | Mermaid render validation | Render all Mermaid blocks using the selected renderer | Yes before external publication | Not implemented | Prevents invisible diagram syntax/layout failures. |
| VAL-010 | Go formatting | `gofmt` over Go service and generated code packages | Yes for Go changes | Not run | CI should fail on unformatted source. |
| VAL-011 | Go unit tests | `go test ./...` per Go module or workspace-aware module list | Yes for Go changes | Not run | Covers API Gateway, Market, messaging, worker, and helper modules. |
| VAL-012 | Go static checks | `go vet ./...` per Go module or configured CI equivalent | Yes for Go changes | Not run | Catches common Go correctness issues. |
| VAL-020 | Rust formatting | `cargo fmt --check` in `distributed-backend/src/trade-settlement` | Yes for Rust changes | Not run | Keeps Rust source stable. |
| VAL-021 | Rust linting | `cargo clippy --all-targets --all-features -- -D warnings` | Yes for Rust changes | Not run | Enforces Rust warnings as failures. |
| VAL-022 | Rust tests | `cargo test` in `distributed-backend/src/trade-settlement` | Yes for settlement changes | Not run | Covers settlement logic and service helpers where tests exist. |
| VAL-030 | Protobuf linting | `buf lint` | Yes for protobuf changes | Not run | Checks protobuf contract style. |
| VAL-031 | Protobuf generation | Repository generation script or CI generation check | Yes for protobuf changes | Not run | Generated code must match source contracts. |
| VAL-040 | Python E2E collection | `python -m pytest --collect-only distributed-backend/tests/e2e` | Yes for e2e test changes | Not run | Verifies test import and collection without live services. |
| VAL-041 | Live E2E | Compose runtime plus Python tests | Yes for trade-flow behavior changes | Not run | Requires PostgreSQL, RabbitMQ, and all services. |
| VAL-050 | Compose syntax | `docker compose config` | Yes for Compose changes | Not run | Validates local runtime definition. |
| VAL-060 | Kubernetes rendering | `kubectl kustomize` or `kustomize build` for base and overlays | Yes for manifest changes | Passed locally on 2026-06-23 for Istio, Gateway, observability, and production overlays | Validates manifest composition. |
| VAL-061 | Production placeholder rejection | Script, CI policy, or admission control for example hosts, issuers, emails, and zero digests | Yes before release | Not implemented | Production gate open. |
| VAL-070 | Terraform validation | `terraform validate` in the EKS and GKE Terraform roots | Yes for Terraform changes | Passed locally on 2026-06-23 with OpenTofu v1.10.0 for both roots | Terraform CLI was unavailable locally; plans and applies still require provider-specific credentials and variables. |
| VAL-080 | CI pipeline | GitHub Actions and `ci-cd` Dagger workflow | Yes before release | Python syntax and parser help passed locally on 2026-06-23; full Dagger/GitHub/GitLab execution not run | Provides repeatable repository validation. |

## Contract Compatibility Rules

| Artifact type | Compatibility rule | Validation gate |
| --- | --- | --- |
| Protobuf field addition | Add new fields with new field numbers; do not reuse or change existing numbers. | VAL-030, VAL-031, service tests. |
| Protobuf field removal or semantic change | Treat as breaking unless all producers and consumers are updated atomically and release notes document migration. Removed fields and enum values must be marked `reserved` when the contract supports it. | Contract review plus service/e2e tests. |
| Protobuf enum evolution | Add enum values only with explicit default/unknown handling. Do not renumber values. Reserve removed numeric values and names. | VAL-030, generated-code review, service tests. |
| Protobuf `oneof` changes | Treat movement into or out of a `oneof` as breaking unless all services are updated atomically. | Contract review and e2e tests. |
| Public service method removal | Removing or changing a public service method requires a new API version or coordinated release with documented migration. | API Gateway tests, Market tests, e2e tests. |
| API Gateway public method | Public paths must remain stable unless a versioned API is introduced. | API Gateway tests and e2e tests. |
| Market request semantics | Actor, idempotency, quantity, price, wallet, and item semantics must remain backward compatible or be versioned. | Market unit tests and trade lifecycle e2e tests. |
| Settlement operation variant | New variants require Rust command conversion, operation handler, SQL effect analysis, and Market planning changes where used. If operation-provenance or allow policy is introduced later, validation must describe it as a separate implemented control. | Rust tests, generated code, invariant matrix update. |
| SQL migration | Migrations must be ordered, repeatable in fresh environments, and compatible with current service startup. Rollback is roll-forward unless an explicit down migration or restore procedure is documented. | Compose migration, Kubernetes render, settlement tests. |
| Data retention change | Idempotency, ledger, escrow, trade, and settlement metadata retention changes require product, SRE, and database review. | Data review plus backup/restore validation. |
| Kubernetes port/config change | Service, ConfigMap, NetworkPolicy, probes, and docs must change together. | Kustomize render and deployment review. |
| Terraform infrastructure change | Runtime assumptions, secrets, networking, and cost/capacity notes must be updated. | Terraform validation and platform review. |

## Architecture Governance Process

| Governance item | Rule |
| --- | --- |
| Architecture owner | Backend maintainers own the document set until a named architecture owner is assigned. |
| Required reviewers | Security view changes require STK-06 review; data integrity changes require STK-04/STK-07 review; deployment changes require STK-05 review. |
| Review cadence | Review the architecture description at least once per release or whenever protobuf, database, messaging, network, or identity boundaries change. |
| Drift detection | Compare context, deployment, and data views against `README.md`, `.proto` files, Compose, Kubernetes manifests, migrations, and service entrypoints. |
| Change log | Material architecture-document repairs must be recorded in `Architecture/changes.md` or a successor change log such as `Architecture/changesv2.md`. |
| Risk updates | New or unresolved gaps must be added to `15-risk-register.md`. |
| Conformance updates | Any new viewpoint, view, model kind, correspondence, or known gap must update `10-conformance-checklist.md`. |
| Owner resolution | Role owners resolve through `20-stakeholder-review-governance.md` until named reviewers are recorded. |
| Production gates | Production readiness cannot be claimed while gates in `19-architecture-facts.md` are open. |
| Enforceability | CODEOWNERS, branch protection, PR checklist, and CI linting are required future enforcement mechanisms; current governance is policy plus review. |

## Generated Artifact Rules

- Protobuf source files are the contract source of truth.
- Generated Go files must correspond to protobuf source files.
- Rust generated protobuf modules are produced during Rust build.
- SQL migrations are the schema source of truth.
- Kubernetes migration ConfigMaps must stay synchronized with SQL migration
  files used by local and service runtime.

## Architecture Decision Record Rules

The decision records in [Correspondences and Rationale](./09-correspondences-rationale.md)
must include status, date, owner, considered alternatives, decision drivers,
consequences, and evidence. Lightweight rationale without these fields is
treated as a partial decision record.

## Documentation Evolution Rules

- Architecture documents in `Architecture/ISO-42010` are canonical for the
  current system.
- Historical notes under other `Architecture` subdirectories may remain as
  design history.
- If a code change changes a stakeholder concern, viewpoint, interface, data
  invariant, deployment boundary, or trust boundary, update the corresponding
  architecture view and the correspondences/rationale file.

## Development View Assertions

| Assertion | Enforcement tag | Evidence or gap |
| --- | --- | --- |
| API contracts, generated code, service implementation, and tests must change together. | Convention plus CI | CI/generation checks should enforce; exact local result not recorded in this documentation update. |
| Database migrations and data integrity tests are architecture-level artifacts. | Convention plus CI | Migration and invariant views treat schema as architecture. |
| A change is not architecture-consistent until its runtime path and validation path are both known. | Governance rule | Change Impact Matrix and Validation Matrix. |
| Independent service deployment compatibility is not fully governed. | Gap recorded | Compatibility rules exist; no release compatibility test suite is documented. |
