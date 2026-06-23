# Stakeholders, Concerns, Perspectives, and Aspects

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical |
| Last reviewed | 2026-06-22 |
| Governing framework | eve-trade Architecture Description Framework |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

## Purpose

This document identifies the stakeholders of `eve-trade`, their architecture
concerns, stakeholder perspectives that organize those concerns, and
architecture aspects that recur across multiple views.

## Stakeholders

| ID | Stakeholder | Role in relation to the system |
| --- | --- | --- |
| STK-01 | Game server integrator | Calls API Gateway and depends on stable command semantics and error behavior. |
| STK-02 | Gameplay/product owner | Defines acceptable trade mechanics, cancellation behavior, escrow behavior, and player-facing outcomes. |
| STK-03 | Backend service developer | Evolves Go and Rust services, protobuf contracts, messaging, and SQL-backed behavior. |
| STK-04 | Settlement/data integrity owner | Is accountable for atomic trade, wallet, item stack, escrow, ledger, and idempotency semantics. |
| STK-05 | SRE/platform operator | Deploys, scales, monitors, and troubleshoots the system in local and Kubernetes environments. |
| STK-06 | Security reviewer | Reviews trust boundaries, authorization assumptions, network isolation, and misuse cases. |
| STK-07 | Database/migration owner | Maintains schema, migrations, seed data, indexes, constraints, and data compatibility. |
| STK-08 | QA/test engineer | Validates request flows, idempotency, failure behavior, and environment readiness. |
| STK-09 | Observability/on-call engineer | Uses logs, metrics, traces, health checks, and settlement metadata during incidents. |
| STK-10 | CI/release maintainer | Maintains build, lint, test, image, integration, and deployment validation workflows. |

## Stakeholder Accountability Model

| Stakeholder | Architecture accountability | Review status |
| --- | --- | --- |
| STK-01 Game server integrator | Approves public command behavior, error semantics, timeout expectations, and idempotency usage. | Assumed; requires upstream integrator review. |
| STK-02 Gameplay/product owner | Approves trade lifecycle rules, expiration behavior, cancellation behavior, and player-visible outcomes. | Assumed; requires product review. |
| STK-03 Backend service developer | Maintains service implementation consistency with contracts and views. | Repository-derived. |
| STK-04 Settlement/data integrity owner | Approves invariants, idempotency behavior, ledgers, and settlement failure semantics. | Repository-derived; requires owner sign-off. |
| STK-05 SRE/platform operator | Approves probes, network policy, deployment topology, recovery, and capacity assumptions. | Repository-derived; requires operations sign-off. |
| STK-06 Security reviewer | Approves trust boundaries, identity binding, service authentication, secrets, and threat model. | Gap recorded; requires security review. |
| STK-07 Database/migration owner | Approves migration order, table constraints, indexes, retention, and rollback assumptions. | Repository-derived; requires database owner review. |
| STK-08 QA/test engineer | Approves validation matrix, e2e scope, replay tests, and failure-mode tests. | Repository-derived; requires QA review. |
| STK-09 Observability/on-call engineer | Approves traces, logs, metrics, alerts, dashboards, and incident queries. | Gap recorded; requires on-call review. |
| STK-10 CI/release maintainer | Approves build, lint, test, image, manifest, and release validation gates. | Repository-derived. |

Review status in this table is not stakeholder sign-off. `Repository-derived`
and `Assumed` mean the content was inferred from implementation artifacts or
maintainer analysis. The authoritative review vocabulary, owner resolution
rules, and current sign-off log are in
[Stakeholder Review and Governance Register](./20-stakeholder-review-governance.md).

## Stakeholder Perspectives

### PER-01 Product And Integration Perspective

Primary stakeholders: STK-01, STK-02, STK-03, STK-08.

Concerns:

- CON-01: Which public trade commands exist and what contract they expose.
- CON-02: How issue, accept, and cancel flows map to game mechanics.
- CON-03: Which service returns trade, escrow, and settlement identifiers.
- CON-04: How idempotency keys and external request IDs should be supplied.
- CON-05: Which behavior is intentionally not provided, such as a separate
  automatic expiration workflow.

Relevant views:

- Context View
- Functional and Runtime View
- Development and Validation View

### PER-02 Correctness And Data Integrity Perspective

Primary stakeholders: STK-02, STK-04, STK-07, STK-08, STK-09.

Concerns:

- CON-06: All settlement operations in a command batch must succeed or fail as
  one unit.
- CON-07: Replayed idempotent requests must not duplicate wallet, item, escrow,
  trade, or ledger mutations.
- CON-08: Item and wallet ledgers must remain append-only audit trails; item-stack ledger history must not be merged or rewritten and must be reconstructable from ordered ledger rows.
- CON-09: Trade state and remaining quantity must remain consistent with escrow
  and accepted quantities.
- CON-10: Failed settlements must leave durable failure metadata without partial
  business mutations.
- CON-11: Migration and seed data must produce a valid local world for tests.

Relevant views:

- Functional and Runtime View
- Information and Data Integrity View
- Correspondences and Rationale

### PER-03 Runtime Reliability Perspective

Primary stakeholders: STK-01, STK-05, STK-08, STK-09, STK-10.

Concerns:

- CON-12: Service readiness should reflect downstream dependency availability
  where implemented.
- CON-13: RabbitMQ command processing must separate normal messages from dead
  messages.
- CON-14: Timeouts and failures must be visible to callers and operators.
- CON-15: The asynchronous boundary must not hide settlement failure outcomes.
- CON-16: Local and production deployment topologies should run the same service
  chain.

Relevant views:

- Context View
- Functional and Runtime View
- Deployment and Operations View

### PER-04 Security And Trust Perspective

Primary stakeholders: STK-01, STK-05, STK-06, STK-09.

Concerns:

- CON-17: External game-facing callers must be separated from internal
  settlement execution.
- CON-18: Production network reachability must be least-privilege.
- CON-19: Actor identity, ownership, and authorization assumptions must be
  explicit.
- CON-20: Direct access to generic settlement operations must be restricted.
- CON-21: Secrets and database credentials must not be embedded in images or
  code.
- CON-22: Transport security expectations must be clear for ingress and
  service-to-service traffic.

Relevant views:

- Context View
- Deployment and Operations View
- Security and Trust View

### PER-05 Operations And Deployment Perspective

Primary stakeholders: STK-05, STK-07, STK-09, STK-10.

Concerns:

- CON-23: Runtime services, ports, configuration, and dependencies must be
  deployable locally and in Kubernetes.
- CON-24: Database migrations must run before services depend on schema.
- CON-25: Runtime resources should expose probes and telemetry.
- CON-26: Kubernetes manifests must encode intended communication paths.
- CON-27: Infrastructure definitions must be traceable to service needs.

Relevant views:

- Deployment and Operations View
- Development and Validation View

### PER-06 Development And Evolution Perspective

Primary stakeholders: STK-03, STK-07, STK-08, STK-10.

Concerns:

- CON-28: Protobuf changes must remain compatible with generated Go and Rust
  code paths.
- CON-29: Go, Rust, SQL, Kubernetes, Terraform, and Python test artifacts must
  remain buildable and reviewable.
- CON-30: Architecture documents should distinguish current implementation from
  historical notes.
- CON-31: CI checks should catch contract, formatting, lint, unit, and integration
  regressions.

Relevant views:

- Development and Validation View
- Correspondences and Rationale

### PER-07 Observability And Assurance Perspective

Primary stakeholders: STK-04, STK-05, STK-08, STK-09, STK-10.

Concerns:

- CON-32: Requests must be traceable across gateway, Market, messaging, worker,
  settlement, and database effects.
- CON-33: Settlement metadata must support post-failure diagnosis.
- CON-34: Health and readiness endpoints must be aligned with deployment probes.
- CON-35: Validation commands must be explicit enough to repeat during review.

Relevant views:

- Functional and Runtime View
- Information and Data Integrity View
- Deployment and Operations View
- Development and Validation View

## Architecture Aspects

The following aspects cut across multiple viewpoints and views.

| Aspect ID | Aspect | Applies to concerns | Description |
| --- | --- | --- | --- |
| ASP-01 | Contract compatibility | CON-01, CON-03, CON-04, CON-28 | Public and internal API messages are protobuf contracts shared by API Gateway and Market, with settlement contracts shared by Market, messaging, worker, and trade-settlement. |
| ASP-02 | Idempotency | CON-04, CON-07, CON-10, CON-32 | Requests carry idempotency keys and fingerprints so completed requests replay and conflicting in-progress requests do not duplicate mutation. |
| ASP-03 | Transactional integrity | CON-06, CON-08, CON-09, CON-10 | trade-settlement atomically applies requested settlement operations and settlement metadata through one PostgreSQL transaction with savepoint-based failure handling. |
| ASP-04 | Asynchronous settlement | CON-12, CON-13, CON-14, CON-15 | Market reaches settlement through RabbitMQ and settlement-worker rather than a direct in-process call. |
| ASP-05 | Trust boundary control | CON-17, CON-18, CON-19, CON-20, CON-22 | API Gateway is game-facing; Market owns trade policy; settlement is internal in the checked-in production-like topology and executes requested operation batches. |
| ASP-06 | Deployment parity | CON-16, CON-23, CON-24, CON-26 | Local Compose and Kubernetes both model the same service chain and database/messaging dependencies. |
| ASP-07 | Observability | CON-14, CON-25, CON-32, CON-33, CON-34 | Services expose health/readiness and telemetry, while settlement metadata records execution state. |
| ASP-08 | Schema governance | CON-08, CON-09, CON-11, CON-24, CON-29 | SQL migrations, local seed data, database constraints, and generated query code are part of the architecture. |

## Concern Register

Concern priority follows the scoring method in
[Stakeholder Review and Governance Register](./20-stakeholder-review-governance.md#concern-priority-method).
Source anchors are summarized here and detailed in
[Evidence Manifest](./18-evidence-manifest.md#source-anchor-register).

| Concern | Priority | Type | Source anchor class | Source confidence | Owner | Review status |
| --- | --- | --- | --- | --- | --- | --- |
| CON-01 | High | Stakeholder need | Protobuf contracts and README | E2 symbol/object | STK-01, STK-03 | Unvalidated |
| CON-02 | Critical | Stakeholder need | Market game-trade code and tests | E2 symbol/object | STK-02, STK-03 | Unvalidated; product review needed |
| CON-03 | High | Stakeholder need | Protobuf response messages | E2 symbol/object | STK-01, STK-03 | Unvalidated |
| CON-04 | Critical | Stakeholder need | Protobuf idempotency fields and settlement executor | E2 symbol/object | STK-01, STK-04 | Unvalidated |
| CON-05 | Medium | Known gap | Current code lacks automatic expiration worker | E1 path | STK-02, STK-05 | Gap recorded |
| CON-06 | Critical | Architectural constraint | Settlement executor transaction behavior | E2 symbol/object | STK-04 | Unvalidated |
| CON-07 | Critical | Architectural constraint | Idempotency table and executor logic | E2 symbol/object | STK-04, STK-08 | Unvalidated |
| CON-08 | Critical | Architectural constraint | Ledger migrations and triggers | E2 symbol/object | STK-04, STK-07 | Unvalidated |
| CON-09 | Critical | Architectural constraint | Trade constraints, Market validation, settlement operations | E2 symbol/object | STK-02, STK-04, STK-07 | Unvalidated |
| CON-10 | Critical | Architectural constraint | Settlement failure metadata path | E2 symbol/object | STK-04, STK-09 | Unvalidated |
| CON-11 | High | Architectural constraint | Migrations and local seed data | E2 symbol/object | STK-07, STK-08 | Unvalidated |
| CON-12 | High | Stakeholder need | Health handlers, Compose, Kubernetes probes | E2 symbol/object | STK-05, STK-09 | Gap recorded |
| CON-13 | High | Stakeholder need | RabbitMQ topology and worker behavior | E2 symbol/object | STK-05, STK-09 | Unvalidated; operations runbook needed |
| CON-14 | High | Stakeholder need | Timeout config and downstream error paths | E2 symbol/object | STK-01, STK-05 | Gap recorded |
| CON-15 | Critical | Stakeholder need | Synchronous-over-asynchronous settlement path | E2 symbol/object | STK-01, STK-04, STK-09 | Gap recorded |
| CON-16 | High | Architectural constraint | Compose and Kubernetes manifests | E2 symbol/object | STK-05, STK-10 | Unvalidated |
| CON-17 | Critical | Architectural constraint | API Gateway boundary and network policy | E2 symbol/object | STK-06 | Gap recorded |
| CON-18 | Critical | Architectural constraint | NetworkPolicy and Istio AuthorizationPolicy | E2 symbol/object | STK-05, STK-06 | Gap recorded |
| CON-19 | Critical | Known gap | Actor fields and JWT placeholders | E2 symbol/object | STK-01, STK-06 | Gap recorded; production blocker |
| CON-20 | Critical | Known gap | Settlement RPC privilege | E2 symbol/object | STK-04, STK-06 | Gap recorded; production blocker |
| CON-21 | High | Architectural constraint | Kubernetes Secrets and Compose env vars | E2 symbol/object | STK-05, STK-06 | Gap recorded |
| CON-22 | High | Architectural constraint | Istio strict mTLS and local h2c | E2 symbol/object | STK-05, STK-06 | Gap recorded |
| CON-23 | High | Stakeholder need | Compose and Kubernetes manifests | E2 symbol/object | STK-05 | Unvalidated |
| CON-24 | Critical | Architectural constraint | Migration job and Compose migrate service | E2 symbol/object | STK-07, STK-05 | Unvalidated |
| CON-25 | High | Stakeholder need | Probes and OpenTelemetry manifests | E2 symbol/object | STK-05, STK-09 | Gap recorded |
| CON-26 | Critical | Architectural constraint | NetworkPolicy and Istio policy manifests | E2 symbol/object | STK-05, STK-06 | Gap recorded |
| CON-27 | Medium | Stakeholder need | Terraform roots and modules | E1 path | STK-05, STK-10 | Unvalidated |
| CON-28 | High | Architectural constraint | Protobuf and generated code paths | E2 symbol/object | STK-03, STK-10 | Unvalidated |
| CON-29 | High | Architectural constraint | Go, Rust, SQL, Python, Kubernetes, Terraform assets | E1 path | STK-03, STK-10 | Gap recorded |
| CON-30 | Medium | Architectural constraint | Architecture index and historical conflict register | E2 symbol/object | STK-03 | Evidence-backed |
| CON-31 | High | Architectural constraint | CI workflow and Dagger pipeline | E1 path | STK-10 | Unvalidated |
| CON-32 | High | Stakeholder need | Observability code, OTEL manifests, settlement metadata | E2 symbol/object | STK-09 | Gap recorded |
| CON-33 | High | Architectural constraint | Settlement metadata tables and executor | E2 symbol/object | STK-04, STK-09 | Unvalidated |
| CON-34 | High | Stakeholder need | Probe handlers and manifests | E2 symbol/object | STK-05, STK-09 | Gap recorded |
| CON-35 | Medium | Stakeholder need | Validation matrix and CI docs | E2 symbol/object | STK-08, STK-10 | Gap recorded |

## Concern Conflict Register

| Conflict | Competing concerns | Current decision | Residual risk |
| --- | --- | --- | --- |
| Synchronous caller path over RabbitMQ | CON-14, CON-15, CON-13 | Market waits for a brokered settlement reply so callers receive a trade result in one request. | Timeout-after-commit outcomes require idempotency-based recovery. |
| Strong settlement integrity versus latency | CON-06, CON-07, CON-14 | trade-settlement centralizes mutation in one PostgreSQL transaction. | Lock contention and database latency can dominate request time. |
| Local convenience versus production isolation | CON-16, CON-18, CON-21, CON-22 | Compose exposes PostgreSQL and RabbitMQ on loopback; production uses network policy and mesh controls. | Local exposure must not be copied to production. |
| Generic settlement operations versus least privilege | CON-20, CON-06, CON-28 | Settlement operations are generic for flexibility and central mutation. | A compromised internal caller has high-impact mutation capability. |
| Deployment portability versus platform-specific controls | CON-23, CON-27 | Compose and Kubernetes share logical topology; Terraform has AWS/EKS, GCP/GKE, and Talos/Omni roots. | Some behavior depends on provider-specific networking, registry, database, secrets, storage, and ingress behavior. |

## Aspect Operationalization

| Aspect | Applied in views | Tagged model components | Verification or gap |
| --- | --- | --- | --- |
| ASP-01 Contract compatibility | Context, Functional/Runtime, Development/Validation, Correspondences | VC-CTX-03, VC-VAL-01, VC-COR-01 | Protobuf source and generated code paths are listed; compatibility policy is added in Development/Validation. |
| ASP-02 Idempotency | Functional/Runtime, Information/Data Integrity, Resilience/Recovery | VC-RUN-02, VC-RUN-03, VC-DATA-02, VC-RES-01 | State machine and replay rules are explicit; ambiguous outcomes remain an operations risk. |
| ASP-03 Transactional integrity | Functional/Runtime, Information/Data Integrity | VC-RUN-01, VC-DATA-02, VC-DATA-03 | Transaction and invariant matrices map to executor and migration evidence. |
| ASP-04 Asynchronous settlement | Functional/Runtime, Performance/Capacity, Resilience/Recovery | VC-RUN-01, VC-RUN-03, VC-PERF-01, VC-RES-01 | RabbitMQ topology and failure semantics are explicit; DLQ runbook remains a risk. |
| ASP-05 Trust boundary control | Context, Security/Trust, Threat Model | VC-CTX-02, VC-SEC-01, VC-SEC-02, VC-THR-01 | Mesh and network controls are separated from app-level identity gaps. |
| ASP-06 Deployment parity | Deployment/Operations, Development/Validation | VC-DEP-01, VC-VAL-01 | Compose versus Kubernetes differences are listed explicitly. |
| ASP-07 Observability | Context, Observability, Resilience/Recovery | VC-OBS-01, VC-RES-01 | Required telemetry and alert signals are listed; dashboards and runbooks remain gaps. |
| ASP-08 Schema governance | Information/Data Integrity, Development/Validation | VC-DATA-01, VC-DATA-02, VC-VAL-01 | Table-level model and migration rules are documented. |

## Concern To View Coverage

| Concern range | Primary coverage |
| --- | --- |
| CON-01 to CON-05 | Context View, Functional and Runtime View |
| CON-06 to CON-11 | Information and Data Integrity View |
| CON-12 to CON-16 | Functional and Runtime View, Deployment and Operations View, Performance and Capacity View, Resilience and Recovery View |
| CON-17 to CON-22 | Security and Trust View, Threat Model View |
| CON-23 to CON-27 | Deployment and Operations View, Performance and Capacity View |
| CON-28 to CON-31 | Development and Validation View, Correspondences and Rationale |
| CON-32 to CON-35 | Functional and Runtime View, Information and Data Integrity View, Deployment and Operations View, Observability View, Resilience and Recovery View |
