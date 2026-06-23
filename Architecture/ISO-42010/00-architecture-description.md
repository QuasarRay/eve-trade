# eve-trade Architecture Description

## Document Metadata

| Field | Value |
| --- | --- |
| Architecture description identifier | `AD-EVE-TRADE-ISO42010` |
| System of interest | `eve-trade` distributed trade backend |
| Version | 1.3 |
| Date | 2026-06-23 |
| Status | Canonical ISO/IEC/IEEE 42010-informed architecture description for internal review; not production-ready approval |
| Maintainers | Project maintainers and backend owners |
| Location | `Architecture/ISO-42010` |
| Standard basis | ISO/IEC/IEEE 42010 architecture description concepts |
| Source baseline | Repository commit `fe5c6af`; architecture document file hashes are recorded in `18-evidence-manifest.md` |

This document set is written as an architecture description for the system of
interest. It uses the ISO/IEC/IEEE 42010 concepts of stakeholders, concerns,
stakeholder perspectives, architecture aspects, viewpoints, views, model kinds,
architecture models, correspondences, and rationale.

The public ISO 42010 conceptual model identifies architecture description
elements and relationships. The full standard remains the normative reference;
this repository does not redistribute the standard text or claim certification.

## Standards References

- ISO/IEC/IEEE 42010:2022 public ISO catalogue entry:
  <https://www.iso.org/standard/74393.html>
- ISO 42010 conceptual model overview:
  <https://www.iso-architecture.org/42010/cm/>
- ISO 42010 getting started guidance:
  <https://www.iso-architecture.org/ieee-1471/getting-started.html>

## Conformance Position

This document set is structured to conform to the ISO 42010 conceptual model as
far as it can be assessed from public information and repository evidence. It is
not a formal ISO audit and does not include purchased-standard clause mapping.

Conformance statuses are tracked in
[ISO 42010 Conformance Checklist](./10-conformance-checklist.md). Status
vocabulary is defined in
[Architecture Framework and Language Specification](./17-adf-adl-specification.md#adl-status-vocabulary).
The checklist uses evidence-oriented statuses such as:

- `Verified`: the claim has a current passing command result, test result,
  rendered artifact, or stakeholder sign-off;
- `Evidence-backed`: the claim is tied to exact files, symbols, configuration
  keys, manifests, or source anchors;
- `Structurally represented`: the concept exists in the document set but is not
  fully evidenced or stakeholder-reviewed;
- `Unvalidated`: stakeholder approval or live evidence is still missing;
- `Not assessed`: a normative clause-level assessment is not possible from the
  public source set available in this repository;
- `Gap recorded`: the architecture description intentionally documents an
  unresolved architecture or implementation risk.

The status `Satisfied` is intentionally not used for checklist rows unless
acceptance criteria, evidence, validation, and reviewer approval are all present.

## Architecture Description Framework

This document set uses a custom project framework named the `eve-trade
Architecture Description Framework`. It is intentionally narrow: it exists to
describe the current `eve-trade` distributed trade backend and to govern future
changes to that architecture description.

The complete ADF and ADL rules are specified in
[Architecture Framework and Language Specification](./17-adf-adl-specification.md).

| Framework element | Definition in this repository |
| --- | --- |
| Entity of interest | The `eve-trade` backend system identified in this document. |
| Stakeholder classes | Defined in `01-stakeholders-concerns-perspectives.md`. |
| Concern taxonomy | Concerns `CON-01` through `CON-35`, with priority, source, owner, and review status. |
| Stakeholder perspectives | Perspectives `PER-01` through `PER-07`, grouping concerns. |
| Architecture aspects | Aspects `ASP-01` through `ASP-08`, applied across views. |
| Architecture viewpoints | Viewpoints `VP-01` through `VP-12` in `02-viewpoints.md`. |
| Model kinds | Context diagrams, sequence diagrams, data tables, invariant catalogs, deployment models, threat tables, risk registers, validation matrices, and correspondence matrices. |
| Architecture views | View files `03` through `14`. |
| Correspondence rules | Defined in `09-correspondences-rationale.md`. |
| Governance rules | Defined in `08-development-validation-view.md` and checked in `10-conformance-checklist.md`. |
| Evidence and status vocabulary | Defined in `17-adf-adl-specification.md` and applied by `18-evidence-manifest.md`. |
| Tailoring rule | A future view may omit a model kind only when the omission is recorded in the conformance checklist or risk register. |

## Architecture Description Language And Conventions

This repository uses a lightweight project ADL made from Markdown, GitHub-style
tables, Mermaid diagrams, source-path references, and explicitly named model
kinds. The ADL is intentionally text-first so it can be reviewed in ordinary
code review. The formal ID, status, evidence-level, model-kind, and view
component rules are in
[Architecture Framework and Language Specification](./17-adf-adl-specification.md).

| Convention | Rule |
| --- | --- |
| File format | Architecture models are written as Markdown files in `Architecture/ISO-42010`. |
| Diagrams | Mermaid diagrams are allowed for context, sequence, state, deployment, and trust-boundary models. Diagram elements with punctuation must use quoted labels. |
| Tables | Tables must name the model kind they implement either by heading or by governing viewpoint. |
| Evidence | Claims about code, manifests, contracts, or migrations must cite repository paths, symbols, or explicit configuration keys. |
| Normative language | `Must` and `Should` identify documented governance or review expectations only when paired with a status tag. Current implementation claims are stated as facts and tied to evidence. |
| Status tags | View assertions and controls use the enforcement vocabulary in `17-adf-adl-specification.md`. |
| Links | Relative links must resolve within the repository. |
| Review | Architecture changes must update the conformance checklist and change log when they change viewpoints, views, correspondence rules, or risk status. |

## Document Purpose

This architecture description gives maintainers a shared, reviewable record of
how `eve-trade` is structured and why. It is intended to support:

- feature planning for trade mechanics and settlement behavior;
- correctness analysis for escrow, wallet, item stack, and ledger mutation;
- operational deployment and incident response;
- security review at service and network boundaries;
- onboarding of engineers who need to work across Go, Rust, protobuf, SQL,
  Kubernetes, Terraform, and CI;
- assessment of whether implementation changes remain consistent with the
  architecture.

## System Of Interest

`eve-trade` is a backend-only distributed service for issuing, accepting, and
canceling player trade instances in an MMORPG-like domain. The system exposes a
game-facing API, validates trade mechanics in Market, and applies requested
settlement operation batches to PostgreSQL with transactional integrity. The
Compose and Kubernetes configurations route settlement commands through
RabbitMQ and settlement-worker; the Market binary also supports a direct/connect
settlement transport when configured that way.

The system of interest includes:

- protobuf contracts under `distributed-backend/proto`;
- the Go API Gateway service;
- the Go Market service;
- the Go RabbitMQ settlement messaging library;
- the Go settlement-worker service;
- the Rust trade-settlement service;
- PostgreSQL schema, migrations, and local seed data;
- Docker Compose local runtime;
- Kubernetes manifests, Istio/Gateway API manifests, observability manifests,
  and Terraform infrastructure definitions;
- CI and validation scripts.

The system of interest excludes:

- game clients and the wider game server beyond their API integration point;
- identity provider implementation;
- external observability SaaS behavior;
- cloud provider managed-service internals;
- live production data and operational runbooks outside this repository.

## Environment

The system runs in two intended environments:

- Local development: Docker Compose starts PostgreSQL, migration, RabbitMQ,
  trade-settlement, settlement-worker, Market, and API Gateway. The public local
  entry point is API Gateway on `localhost:8080`; PostgreSQL and RabbitMQ are
  published to loopback for development.
- Production-like deployment: Kubernetes deploys the services with ConfigMaps,
  Secrets, service accounts, probes, network policies, Istio security/traffic
  resources, Gateway API ingress, observability collectors, and Terraform-managed
  platform prerequisites. The current Terraform roots support AWS/EKS with RDS
  and ECR, GCP/GKE with Cloud SQL and Artifact Registry, or an Omni-managed
  Talos Kubernetes cluster with provider-neutral image references and
  external-or-in-cluster PostgreSQL preparation.

## Architecture Summary

The main Compose and Kubernetes trade path is:

1. A game server calls API Gateway with a trade command.
2. API Gateway forwards the command to Market using generated protobuf/Connect
   clients and shared request/response contracts.
3. Market reads current snapshots from PostgreSQL, performs game-mechanic
   validation, and converts valid commands into settlement operation batches.
4. Market publishes settlement batches through RabbitMQ.
5. settlement-worker consumes settlement command messages and calls the Rust
   trade-settlement service.
6. trade-settlement atomically executes the requested settlement operations
   inside one PostgreSQL transaction and records idempotency and settlement
   audit metadata. It handles command-envelope and row-level data preconditions;
   it is not the owner of Market trade policy.
7. The settlement response returns through the worker and messaging reply path
   to Market and then to API Gateway.

## Architecture Description Map

| Document | ISO 42010 role |
| --- | --- |
| `00-architecture-description.md` | Identifies the architecture description, system of interest, purpose, scope, environment, evidence, and document structure. |
| `01-stakeholders-concerns-perspectives.md` | Identifies stakeholders, concerns, stakeholder perspectives, and architecture aspects. |
| `02-viewpoints.md` | Specifies architecture viewpoints, model kinds, stakeholders, framed concerns, notations, and analysis methods. |
| `03-context-view.md` | Architecture view governed by the Context viewpoint. |
| `04-functional-runtime-view.md` | Architecture view governed by the Functional Decomposition and Runtime Transaction viewpoints. |
| `05-information-data-integrity-view.md` | Architecture view governed by the Information and Data Integrity viewpoint. |
| `06-deployment-operations-view.md` | Architecture view governed by the Deployment and Operations viewpoint. |
| `07-security-trust-view.md` | Architecture view governed by the Security and Trust viewpoint. |
| `08-development-validation-view.md` | Architecture view governed by the Development and Validation viewpoint. |
| `09-correspondences-rationale.md` | Correspondences between views and architecture rationale for material decisions. |
| `10-conformance-checklist.md` | Checklist mapping this document set to ISO 42010 architecture description concepts. |
| `11-performance-capacity-view.md` | Architecture view governed by the Performance and Capacity viewpoint. |
| `12-resilience-recovery-view.md` | Architecture view governed by the Resilience and Recovery viewpoint. |
| `13-observability-view.md` | Architecture view governed by the Observability viewpoint. |
| `14-threat-model-view.md` | Architecture view governed by the Threat Model viewpoint. |
| `15-risk-register.md` | Architecture risk register used by multiple views. |
| `16-glossary.md` | Common ISO and project terminology. |
| `17-adf-adl-specification.md` | Project-specific ADF, ADL, model schema, ID, evidence, and conformance-test rules. |
| `18-evidence-manifest.md` | Content-hash baseline, validation results, source anchors, and evidence status. |
| `19-architecture-facts.md` | Central reusable facts for timeouts, DLQ topology, production gates, secrets, and validation status. |
| `20-stakeholder-review-governance.md` | Stakeholder review status, owner resolution, priority scoring, and governance gates. |

## Evidence Baseline

| Evidence item | Value |
| --- | --- |
| Source branch at review time | `main` |
| Source commit at review time | `fe5c6af` |
| Architecture document status | Content-addressed by `18-evidence-manifest.md` until committed. |
| Review date | 2026-06-22 |
| Repository validation run by this documentation update | Recorded in `18-evidence-manifest.md`. |
| Runtime validation run by this documentation update | Not run; runtime validation requires PostgreSQL, RabbitMQ, services, and environment-specific tooling. |

The architecture views are derived from the following repository artifacts:

- `README.md`
- `compose.yaml`
- `distributed-backend/proto/eve/api_gateway/v1/api_gateway.proto`
- `distributed-backend/proto/eve/market/v1/market.proto`
- `distributed-backend/proto/eve/trade_settlement/v1/trade_settlement.proto`
- `distributed-backend/src/api-gateway`
- `distributed-backend/src/market`
- `distributed-backend/src/messaging/rabbitmqsettlement`
- `distributed-backend/src/settlement-worker`
- `distributed-backend/src/trade-settlement`
- `distributed-backend/src/trade-settlement/migrations`
- `distributed-backend/src/trade-settlement/seeds/local_dev_world.sql`
- `distributed-backend/orchestration/kubernetes`
- `distributed-backend/terraform`
- `ci-cd`
- `.github/workflows/verify.yaml`
- `distributed-backend/OBSERVABILITY.md`

Historical architecture notes under `Architecture/*/v1.md` are treated as
design history. They are not authoritative when they conflict with current code.

## Production Readiness Position

This architecture description is canonical for describing the current
architecture, but it is not a production-readiness approval. The production
readiness gates in
[Architecture Facts Catalog](./19-architecture-facts.md#production-readiness-gates)
are open. In particular, actor identity binding, settlement API hardening,
placeholder deployment rejection, timeout/outcome contract resolution, DLQ
runbooks, backup/restore targets, runtime validation, and stakeholder reviews
remain blockers before untrusted production exposure.

## Historical Conflict Register

| Historical document | Conflict or limitation | Canonical source |
| --- | --- | --- |
| `Architecture/Trade Request Lifecycle/v1.md` | Describes older direct settlement assumptions and lifecycle details that do not fully match the current RabbitMQ plus settlement-worker path. | `04-functional-runtime-view.md` |
| `Architecture/Trade State Lifecycle/v1.md` | Contains deprecated lifecycle analysis and state assumptions. | `04-functional-runtime-view.md` and `05-information-data-integrity-view.md` |
| `Architecture/Proto Architecture/v1.md` | Retained for proto design history; current contracts are the `.proto` files under `distributed-backend/proto/eve`. | `03-context-view.md` and `08-development-validation-view.md` |
| `Architecture/Conceptual Database Schema/v1.md` | Conceptual schema may omit current migrations, triggers, and constraints. | `05-information-data-integrity-view.md` |
| `Architecture/Canonical SQLx Design/v1.md` | Lists settlement operation names that still correspond to current protobuf/Rust variants, but it is not a complete current data model or runtime description. | `05-information-data-integrity-view.md` |

## Architectural Constraints

- The game-facing API is protobuf-based and implemented with Connect-compatible
  HTTP services.
- Market owns trade rules, validation, and settlement-operation composition that
  depend on current database snapshots.
- trade-settlement's runtime responsibility is atomic execution of requested
  settlement operations plus ledger, settlement metadata, and idempotency writes
  inside PostgreSQL.
- PostgreSQL is the source of truth for trade, escrow, wallet, item stack,
  settlement, idempotency, and ledger state.
- Item-stack ledgers are append-only hash-chained records. Item stack current
  rows are projections that must match the latest ledger row; merge operations
  append new source and destination ledger rows instead of modifying or
  combining existing ledger history.
- RabbitMQ is the configured asynchronous command/reply boundary between Market
  and settlement execution in Compose and Kubernetes. Direct/connect settlement
  transport remains implemented as an alternate Market configuration.
- Production deployment assumes Kubernetes controls, service-specific
  configuration, probes, network policy, and observability.

## Known Architecture Limitations

- Authentication and caller identity are not fully modeled end to end. Request
  actor fields are accepted from upstream callers, and JWT/identity-provider
  binding to `issued_by_capsuleer_id`, `buyer_capsuleer_id`, and
  `cancelled_by_capsuleer_id` is a critical production-blocking gap until the
  mapping is enforced.
- Settlement commands are powerful generic operations. Current repository
  controls are topology/network isolation, Market-side command composition, and
  trade-settlement command-envelope and row-level precondition checks. No
  operation-provenance or operation-allow policy is implemented in the
  settlement API.
- Live end-to-end validation requires local runtime dependencies. Some checks can
  be static, but full trade flow validation needs PostgreSQL, RabbitMQ, and
  services.
- The architecture description is repository-grounded. It does not assert
  certification by ISO or any standards body.
- Several architecture controls are documented as intended controls, not
  implementation proof. These are tracked in
  [Architecture Risk Register](./15-risk-register.md).
