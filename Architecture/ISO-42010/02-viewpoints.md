# Architecture Viewpoints

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical |
| Last reviewed | 2026-06-22 |
| Governing framework | eve-trade Architecture Description Framework |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

## Purpose

This document specifies the viewpoints used by the `eve-trade` architecture
description. Each viewpoint defines the stakeholders, concerns, model kinds,
notations, and analysis methods that govern one or more architecture views.

The viewpoint set is part of the custom `eve-trade Architecture Description
Framework` defined in [Architecture Description](./00-architecture-description.md)
and formalized in
[Architecture Framework and Language Specification](./17-adf-adl-specification.md).

## VP-01 Context Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-01 |
| Purpose | Show the system of interest, external actors, external dependencies, and service boundaries. |
| Primary stakeholders | STK-01, STK-03, STK-05, STK-06, STK-09 |
| Framed concerns | CON-01, CON-03, CON-12, CON-16, CON-17, CON-18, CON-32 |
| Related perspectives | PER-01, PER-03, PER-04, PER-07 |
| Related aspects | ASP-01, ASP-04, ASP-05, ASP-07 |
| Model kinds | System context diagram, boundary table, interface catalog |
| Notation | Mermaid flowchart plus tables |
| Required model elements | System of interest, external actors, external dependencies, internal services, data store, message broker, observability sink, trust boundaries |
| Analysis method | Confirm every runtime dependency in Compose/Kubernetes is represented and every public contract has an owner. |

View governed by this viewpoint:

- [Context View](./03-context-view.md)

## VP-02 Functional Decomposition Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-02 |
| Purpose | Explain the responsibilities of each service and module. |
| Primary stakeholders | STK-02, STK-03, STK-04, STK-08, STK-10 |
| Framed concerns | CON-01, CON-02, CON-03, CON-05, CON-28, CON-29 |
| Related perspectives | PER-01, PER-02, PER-06 |
| Related aspects | ASP-01, ASP-03, ASP-04 |
| Model kinds | Responsibility table, module map, operation catalog |
| Notation | Tables with explicit responsibility and dependency statements |
| Required model elements | API Gateway, Market, messaging library, settlement-worker, trade-settlement, PostgreSQL, protobuf contracts |
| Analysis method | Verify Market-specific trade decisions remain in Market and durable mutation execution remains in trade-settlement operation handlers. |

View governed by this viewpoint:

- [Functional and Runtime View](./04-functional-runtime-view.md)

## VP-03 Runtime Transaction Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-03 |
| Purpose | Show request flow, settlement flow, idempotency behavior, and failure behavior for issue, accept, and cancel commands. |
| Primary stakeholders | STK-01, STK-02, STK-04, STK-05, STK-08, STK-09 |
| Framed concerns | CON-04, CON-06, CON-07, CON-10, CON-12, CON-13, CON-14, CON-15, CON-32, CON-33 |
| Related perspectives | PER-01, PER-02, PER-03, PER-07 |
| Related aspects | ASP-02, ASP-03, ASP-04, ASP-07 |
| Model kinds | Sequence diagram, state/effect table, failure mode table |
| Notation | Mermaid sequence diagram plus tables |
| Required model elements | Request, idempotency key, request fingerprint, Market validation, configured settlement transport, RabbitMQ command and worker consumption when `SETTLEMENT_TRANSPORT=rabbitmq`, settlement batch, database transaction, response |
| Analysis method | Trace success, replay, conflict, validation failure, broker failure, worker failure, and settlement failure outcomes. |

View governed by this viewpoint:

- [Functional and Runtime View](./04-functional-runtime-view.md)

## VP-04 Information And Data Integrity Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-04 |
| Purpose | Describe persistent information, integrity rules, ledgers, idempotency records, and schema responsibilities. |
| Primary stakeholders | STK-04, STK-07, STK-08, STK-09 |
| Framed concerns | CON-06, CON-07, CON-08, CON-09, CON-10, CON-11, CON-24, CON-33 |
| Related perspectives | PER-02, PER-05, PER-07 |
| Related aspects | ASP-02, ASP-03, ASP-08 |
| Model kinds | Data group table, invariant catalog, transaction model, migration map |
| Notation | Tables and textual invariants |
| Required model elements | Trade tables, escrow tables, item stack tables, wallet tables, ledger tables, settlement metadata, idempotency metadata, migrations |
| Analysis method | Check that every business mutation has an invariant and every invariant is enforced by service logic, SQL constraints, or database transaction semantics. |

View governed by this viewpoint:

- [Information and Data Integrity View](./05-information-data-integrity-view.md)

## VP-05 Deployment And Operations Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-05 |
| Purpose | Describe local and production deployment topology, runtime configuration, probes, network reachability, migration, and observability. |
| Primary stakeholders | STK-05, STK-07, STK-09, STK-10 |
| Framed concerns | CON-12, CON-13, CON-16, CON-23, CON-24, CON-25, CON-26, CON-27, CON-34 |
| Related perspectives | PER-03, PER-05, PER-07 |
| Related aspects | ASP-04, ASP-06, ASP-07, ASP-08 |
| Model kinds | Deployment diagram, environment table, probe table, network policy table |
| Notation | Mermaid flowchart and tables |
| Required model elements | Docker Compose services, Kubernetes workloads, ConfigMaps, Secrets, Services, probes, network policies, migration job, Terraform resources |
| Analysis method | Confirm intended service-to-service paths are allowed and unintended paths are omitted by policy. |

View governed by this viewpoint:

- [Deployment and Operations View](./06-deployment-operations-view.md)

## VP-06 Security And Trust Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-06 |
| Purpose | Identify trust boundaries, security controls, authorization assumptions, and residual security gaps. |
| Primary stakeholders | STK-01, STK-05, STK-06, STK-09 |
| Framed concerns | CON-17, CON-18, CON-19, CON-20, CON-21, CON-22 |
| Related perspectives | PER-04 |
| Related aspects | ASP-05, ASP-06, ASP-07 |
| Model kinds | Trust boundary diagram, control table, gap table |
| Notation | Mermaid flowchart and tables |
| Required model elements | External caller boundary, ingress boundary, internal service boundary, settlement boundary, database boundary, secrets boundary |
| Analysis method | Trace a malicious or compromised caller from each boundary and identify which control stops or detects misuse. |

View governed by this viewpoint:

- [Security and Trust View](./07-security-trust-view.md)

## VP-07 Development And Validation Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-07 |
| Purpose | Show source modules, generated code boundaries, build/test responsibilities, and repeatable validation commands. |
| Primary stakeholders | STK-03, STK-08, STK-10 |
| Framed concerns | CON-28, CON-29, CON-30, CON-31, CON-35 |
| Related perspectives | PER-06, PER-07 |
| Related aspects | ASP-01, ASP-08 |
| Model kinds | Source map, validation matrix, change-impact matrix |
| Notation | Tables |
| Required model elements | Go modules, Rust crate, protobuf generation, SQL migrations, tests, CI workflows, deployment manifests |
| Analysis method | For each kind of change, identify the affected contract, generated artifact, tests, and deployment assets. |

View governed by this viewpoint:

- [Development and Validation View](./08-development-validation-view.md)

## VP-08 Performance And Capacity Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-08 |
| Purpose | Describe latency, throughput, queueing, bottlenecks, scaling assumptions, backpressure, and capacity risks. |
| Primary stakeholders | STK-01, STK-05, STK-09, STK-10 |
| Framed concerns | CON-12, CON-14, CON-15, CON-23, CON-25, CON-27 |
| Related perspectives | PER-03, PER-05, PER-07 |
| Related aspects | ASP-04, ASP-06, ASP-07 |
| Model kinds | Timeout budget table, bottleneck table, capacity assumption table, performance risk table |
| Notation | Tables with configuration evidence and explicit unknowns |
| Required model elements | Caller timeout, API Gateway timeout, Market settlement timeout, RabbitMQ publish timeout, worker timeout, prefetch, HPAs, resource limits, database contention points |
| Analysis method | Trace the maximum caller-visible path and identify queueing or lock contention where latency can grow. |

View governed by this viewpoint:

- [Performance and Capacity View](./11-performance-capacity-view.md)

## VP-09 Resilience And Recovery Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-09 |
| Purpose | Describe failure recovery, ambiguous outcomes, idempotent replay, dead-letter handling, reconciliation, backup/restore, and operational ownership. |
| Primary stakeholders | STK-04, STK-05, STK-08, STK-09 |
| Framed concerns | CON-07, CON-10, CON-13, CON-14, CON-15, CON-24, CON-33 |
| Related perspectives | PER-02, PER-03, PER-05, PER-07 |
| Related aspects | ASP-02, ASP-03, ASP-04, ASP-07, ASP-08 |
| Model kinds | Recovery scenario table, ambiguous outcome matrix, DLQ operations table, RTO/RPO table |
| Notation | Tables and Mermaid state diagrams |
| Required model elements | Idempotency states, failed settlement metadata, caller timeout, broker failure, worker failure, database failure, DLQ, replay/reconciliation process |
| Analysis method | For each failure, identify durable state, caller-visible outcome, operator action, and whether automatic recovery exists. |

View governed by this viewpoint:

- [Resilience and Recovery View](./12-resilience-recovery-view.md)

## VP-10 Observability Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-10 |
| Purpose | Define trace, log, metric, alert, and diagnostic requirements for GUI interactions, UDP edge handling, and settlement. |
| Primary stakeholders | STK-05, STK-08, STK-09, STK-10 |
| Framed concerns | CON-14, CON-25, CON-32, CON-33, CON-34, CON-35 |
| Related perspectives | PER-03, PER-05, PER-07 |
| Related aspects | ASP-02, ASP-04, ASP-07 |
| Model kinds | Telemetry map, correlation key table, alert signal table, incident query table |
| Notation | Tables |
| Required model elements | Interaction ID, external request ID, idempotency key, settlement batch ID, RabbitMQ correlation ID, service spans, logs, metrics, health/readiness signals |
| Analysis method | Confirm each runtime step has at least one observable signal and one correlation key. |

View governed by this viewpoint:

- [Observability View](./13-observability-view.md)

## VP-11 Contract Compatibility And Evolution Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-11 |
| Purpose | Govern changes to protobuf contracts, generated code, SQL migrations, deployment contracts, and release validation. |
| Primary stakeholders | STK-03, STK-07, STK-08, STK-10 |
| Framed concerns | CON-28, CON-29, CON-31, CON-35 |
| Related perspectives | PER-06 |
| Related aspects | ASP-01, ASP-08 |
| Model kinds | Compatibility rule table, change impact table, validation gate table |
| Notation | Tables |
| Required model elements | Protobuf source, generated Go code, Rust generated modules, migrations, Kubernetes manifests, CI checks, rollback considerations |
| Analysis method | For each change type, identify backward compatibility, generated artifacts, validation gates, and rollback constraints. |

View governed by this viewpoint:

- [Development and Validation View](./08-development-validation-view.md)

## VP-12 Threat Model Viewpoint

| Field | Specification |
| --- | --- |
| Viewpoint ID | VP-12 |
| Purpose | Analyze assets, attackers, trust assumptions, misuse cases, controls, residual risks, and verification gaps. |
| Primary stakeholders | STK-01, STK-04, STK-05, STK-06, STK-09 |
| Framed concerns | CON-17, CON-18, CON-19, CON-20, CON-21, CON-22 |
| Related perspectives | PER-04 |
| Related aspects | ASP-05, ASP-07 |
| Model kinds | Asset table, attacker model, STRIDE-style threat table, control verification table |
| Notation | Tables plus optional trust-boundary diagram |
| Required model elements | Actor identity, API Gateway, Market, RabbitMQ, settlement-worker, trade-settlement, PostgreSQL, secrets, telemetry, network and mesh controls |
| Analysis method | For each entry point, identify spoofing, tampering, repudiation, information disclosure, denial of service, and elevation-of-privilege risks where applicable. |

View governed by this viewpoint:

- [Threat Model View](./14-threat-model-view.md)

## Viewpoint Selection Rationale

| Viewpoint | Rationale |
| --- | --- |
| VP-01 Context | Required to bound the system of interest and external dependencies. |
| VP-02 Functional Decomposition | Required because service responsibility boundaries are a primary correctness mechanism. |
| VP-03 Runtime Transaction | Required because the trade path crosses UDP edge handling, gRPC/Connect, RabbitMQ, worker execution, and SQL transactions. |
| VP-04 Information and Data Integrity | Required because settlement mutates inventory, wallet, escrow, trade, ledger, and idempotency state. |
| VP-05 Deployment and Operations | Required because deployment manifests enforce communication and runtime behavior. |
| VP-06 Security and Trust | Required because actor identity, privileged settlement operations, and service isolation are high risk. |
| VP-07 Development and Validation | Required because the repository spans Go, Rust, protobuf, SQL, Python, Kubernetes, Terraform, and CI. |
| VP-08 Performance and Capacity | Added because the synchronous-over-asynchronous path has timeout, queueing, and database contention risks. |
| VP-09 Resilience and Recovery | Added because ambiguous outcomes, DLQs, and idempotent recovery are central to operations. |
| VP-10 Observability | Added because distributed request tracing and settlement diagnosis are explicit stakeholder concerns. |
| VP-11 Contract Compatibility and Evolution | Added because independent contract/schema changes can break the service chain. |
| VP-12 Threat Model | Added because the security view alone was too shallow for high-trust settlement flows. |

## Deferred Or Excluded Viewpoints

| Candidate viewpoint | Disposition | Reason |
| --- | --- | --- |
| User interface viewpoint | Excluded | The system of interest is backend-only and has no UI. |
| Business capability portfolio viewpoint | Deferred | Useful for enterprise planning, but not needed to repair the current architecture description flaws. |
| Cost optimization viewpoint | Deferred into Deployment and Performance views | Current repository has Terraform roots but no production cost targets. |
| Compliance/privacy viewpoint | Deferred into Security, Data Integrity, and Risk views | No concrete regulatory regime is declared in the repository. |

## Model Kind Specifications

| Model kind | Model kind ID | Construction rules | Interpretation rules | Invalid when |
| --- | --- | --- | --- | --- |
| System context diagram | MK-CTX-DIAGRAM | Must show the system boundary, external actors, internal services, data stores, brokers, and observability destinations. | Arrows represent runtime or provisioning relationships as defined by the legend. | A runtime dependency exists in Compose/Kubernetes but is absent from the diagram or table. |
| Sequence diagram | MK-RUN-SEQUENCE | Must show game frontend or simulator, Quilkin, API Gateway UDP edge, Market, PostgreSQL, the configured settlement transport, trade-settlement, and success/failure alternatives when modeling GUI interactions. The checked-in Compose/Kubernetes model includes RabbitMQ and settlement-worker. | Ordered arrows are logical interaction order, not precise network timing. | A durable write is shown outside trade-settlement without an explicit exception. |
| Data table model | MK-DATA-TABLE | Must list table or data group, owner, key role, and evidence. | Table rows are architecture-level data responsibilities, not full DDL. | It omits a table that enforces a stated invariant. |
| Invariant catalog | MK-DATA-INVARIANT | Must name invariant, enforcement mechanism, evidence, tests or gaps. | An invariant is only fully satisfied when service logic, SQL, and tests are identified or a gap is recorded. | Enforcement is asserted without source evidence. |
| Deployment model | MK-DEP-TOPOLOGY | Must include local and Kubernetes differences, probes, ports, secrets, network policy, and platform egress. | Deployment arrows describe intended connectivity, not proof of live reachability. | It hides broad egress, placeholder configuration, or probe semantics. |
| Threat table | MK-SEC-THREAT | Must identify threat ID, asset, entry point, STRIDE category, preconditions, severity, controls, verification, and residual risk. | Residual risk remains open until a control and verification are implemented or accepted. | It relies only on network isolation for a high-privilege API. |
| Validation matrix | MK-VAL-MATRIX | Must list validation ID, command, required-before-merge status, last documented result, evidence anchor, and limitations. | `Not run` is acceptable only when explicit and risk-recorded. | It presents an aspirational check as a completed result. |
| Risk register | MK-RISK-REGISTER | Must include owner, severity, probability, impact, mitigation, status, due date, acceptance authority, residual severity, closure criteria, and linked views. | `Open` risks are unresolved architecture or implementation concerns. | A known critical gap is absent, ownerless, or lacks review mechanics. |

Detailed table schemas for these model kinds are defined in
[Architecture Framework and Language Specification](./17-adf-adl-specification.md#model-kind-schema-requirements).

## Model ID Register

| Model ID | Model | Model kind | View component ID | Owning document |
| --- | --- | --- | --- | --- |
| MODEL-CTX-01 | System context model | MK-CTX-DIAGRAM | VC-CTX-01 | `03-context-view.md` |
| MODEL-CTX-02 | Boundary model | Boundary table | VC-CTX-02 | `03-context-view.md` |
| MODEL-CTX-03 | Interface catalog | Interface catalog | VC-CTX-03 | `03-context-view.md` |
| MODEL-RUN-01 | Runtime sequence model | MK-RUN-SEQUENCE | VC-RUN-01 | `04-functional-runtime-view.md` |
| MODEL-RUN-02 | Idempotency state model | State model | VC-RUN-02 | `04-functional-runtime-view.md` |
| MODEL-RUN-03 | Request outcome matrix | Outcome table | VC-RUN-03 | `04-functional-runtime-view.md` |
| MODEL-DATA-01 | Table-level model | MK-DATA-TABLE | VC-DATA-01 | `05-information-data-integrity-view.md` |
| MODEL-DATA-02 | Invariant enforcement matrix | MK-DATA-INVARIANT | VC-DATA-02 | `05-information-data-integrity-view.md` |
| MODEL-DATA-03 | Settlement operation semantics | Operation semantics table | VC-DATA-03 | `05-information-data-integrity-view.md` |
| MODEL-DEP-01 | Production-like deployment model | MK-DEP-TOPOLOGY | VC-DEP-01 | `06-deployment-operations-view.md` |
| MODEL-SEC-01 | Trust boundary model | Trust boundary diagram | VC-SEC-01 | `07-security-trust-view.md` |
| MODEL-VAL-01 | Validation matrix | MK-VAL-MATRIX | VC-VAL-01 | `08-development-validation-view.md` |
| MODEL-COR-01 | Correspondence rules | Correspondence matrix | VC-COR-01 | `09-correspondences-rationale.md` |
| MODEL-PERF-01 | Timeout and queueing budget | Timeout budget table | VC-PERF-01 | `11-performance-capacity-view.md` |
| MODEL-RES-01 | Ambiguous outcome matrix | Recovery matrix | VC-RES-01 | `12-resilience-recovery-view.md` |
| MODEL-OBS-01 | Telemetry map | Telemetry table | VC-OBS-01 | `13-observability-view.md` |
| MODEL-THR-01 | Threat register | MK-SEC-THREAT | VC-THR-01 | `14-threat-model-view.md` |
| MODEL-RISK-01 | Risk register | MK-RISK-REGISTER | VC-RISK-01 | `15-risk-register.md` |

## Viewpoint Convention Matrix

| Viewpoint | Construction rule | Interpretation rule | Analysis rule | Failure criterion |
| --- | --- | --- | --- | --- |
| VP-01 | Include every service and external runtime dependency in an interface or boundary table. | Boundary rows define trust/control intent, not only topology. | Compare against Compose, Kubernetes, protobuf, and README evidence. | A public or internal interface has no owner or contract source. |
| VP-02 | Assign each component positive responsibilities and prohibited responsibilities. | Responsibility ownership is normative where tagged `Enforced` or `Convention`. | Verify no service writes outside its stated ownership. | Mutating responsibility is duplicated without rationale. |
| VP-03 | Model success, replay, invalid input, downstream failure, and timeout. | The sequence is logical and must be paired with failure tables. | Trace durable state for every caller-visible outcome. | Ambiguous outcomes are not recoverable or risk-recorded. |
| VP-04 | Map data groups, tables, invariants, operations, and lifecycle. | Data ownership is determined by write authority and schema control. | Link each invariant to code, SQL, tests, or a gap. | An invariant is listed without enforcement evidence. |
| VP-05 | Separate local Compose, Kubernetes base, production overlay, and platform dependencies. | Probe and network policy claims must match manifest semantics. | Verify each allowed flow and broad egress exception. | Readiness, secret, or policy behavior is overstated. |
| VP-06 | Separate app, mesh, network, secret, and operational controls. | A control is not complete unless verification and residual risk are stated. | Analyze actor identity and settlement privilege first. | A critical trust assumption is hidden. |
| VP-07 | Link modules, generated artifacts, compatibility rules, and validation gates. | Validation entries distinguish required gates from last documented results. | Review every change type for contract/schema/deployment impact. | A compatibility-breaking change has no validation gate. |
| VP-08 | Derive timeout and capacity assumptions from config where available. | Unknown SLOs and limits are gaps, not defaults. | Identify bottlenecks and queueing points. | Performance claims have no metric or config evidence. |
| VP-09 | Model recovery from broker, worker, DB, settlement, and caller timeout failures. | Recovery actions are documented operator procedures unless implemented as tooling. | Identify durable state, replay path, and owner per failure. | A committed-but-not-replied outcome has no procedure. |
| VP-10 | Map telemetry to runtime steps and correlation keys. | A signal is useful only if it joins to request or settlement identity. | Confirm alerts for the highest-risk failure states. | A critical failure mode has no observable signal. |
| VP-11 | Define protobuf, generated code, SQL, manifest, and rollback compatibility rules. | Compatibility is repository-level unless independent deployment is introduced. | Require validation gates for each artifact class. | A schema or contract change bypasses compatibility review. |
| VP-12 | Model assets, attackers, entry points, STRIDE categories, controls, and verification gaps. | Residual risks remain open until verified controls exist. | Prioritize identity spoofing, broker injection, settlement abuse, and secret compromise. | A high-impact threat lacks mitigation or risk entry. |

## Viewpoint Consistency Rules

- A view must declare which viewpoint governs it.
- A view must identify which concerns it addresses.
- A view must use at least one model kind specified by its governing viewpoint.
- A model must have a legend or explanation for non-obvious elements.
- A view must include concern satisfaction evidence or explicitly link to a
  view-specific evidence table.
- A view must tag important assertions as `Enforced`, `Partially enforced`,
  `Convention`, or `Gap` when enforcement is material. New or modified rows
  should use the enforcement vocabulary in
  `17-adf-adl-specification.md`.
- Correspondences between views are recorded in
  [Correspondences and Rationale](./09-correspondences-rationale.md).
- Model-kind compliance is checked by review until automated architecture
  documentation linting is implemented.
