# Architecture Framework and Language Specification

## Specification Metadata

| Field | Value |
| --- | --- |
| Specification ID | `ADF-EVE-TRADE-001` |
| Framework name | `eve-trade Architecture Description Framework` |
| ADL name | `eve-trade Architecture Description Language` |
| Version | 1.2 |
| Date | 2026-06-22 |
| Status | Canonical framework and language specification for this repository |
| Applies to | Architecture documents under `Architecture/ISO-42010` |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

## Purpose

This document makes the project-specific Architecture Description Framework
and Architecture Description Language explicit enough to reuse, review, and
validate. It does not claim that this repository defines a general-purpose
external ADF. It defines the rules for the `eve-trade` architecture description.

## Framework Scope

| Framework area | Rule |
| --- | --- |
| Applicability | Applies only to the `eve-trade` distributed trade backend unless a future architecture decision explicitly broadens it. |
| Required work products | The architecture description, stakeholder/concern register, viewpoint specifications, architecture views, correspondence/rationale file, conformance checklist, risk register, glossary, evidence manifest, central facts catalog, and stakeholder governance register. |
| Optional work products | Additional runbooks, rendered diagrams, formal ADR files, generated traceability reports, and validation artifacts may be added when they are linked from this document set. |
| Tailoring | A required viewpoint or model kind can be omitted only when the omission is recorded in the conformance checklist and risk register. |
| Extension | New viewpoints, views, model kinds, aspects, concerns, facts, risks, and correspondences must receive IDs from the ID namespace table below. |
| Versioning | Framework changes increment the version in this file and `00-architecture-description.md`. |
| Review | Framework changes require backend maintainer review. Security, data, deployment, or validation rule changes also require the corresponding stakeholder role listed in `20-stakeholder-review-governance.md`. |

## Required Document Set

| Document | Required role |
| --- | --- |
| `00-architecture-description.md` | Identifies the architecture description, system of interest, purpose, scope, environment, evidence, limitations, and document map. |
| `01-stakeholders-concerns-perspectives.md` | Identifies stakeholders, concerns, stakeholder perspectives, and architecture aspects. |
| `02-viewpoints.md` | Specifies viewpoints, model kinds, notations, required elements, and analysis methods. |
| `03` through `14` | Architecture views governed by one or more viewpoints. |
| `09-correspondences-rationale.md` | Records cross-view correspondences, correspondence methods, consistency checks, and decisions. |
| `10-conformance-checklist.md` | Records internal ISO 42010 concept coverage and remaining conformance risks. |
| `15-risk-register.md` | Records architecture risks, owners, due dates, acceptance authority, residual severity, and closure criteria. |
| `16-glossary.md` | Defines ISO 42010 and project terminology used by the document set. |
| `17-adf-adl-specification.md` | Defines this framework, ADL, model schemas, ID rules, evidence levels, and conformance tests. |
| `18-evidence-manifest.md` | Records the review baseline, architecture file hashes, validation commands, and source anchors. |
| `19-architecture-facts.md` | Provides central reusable facts for timeouts, readiness, DLQ topology, production gates, secrets, and validation state. |
| `20-stakeholder-review-governance.md` | Records review status, owner resolution, concern priority scoring, sign-off rules, and governance lifecycle. |

## Process Boundary

The architecture description includes some process rules because they protect
architectural consistency. These rules are project governance, not ISO 42010
process requirements.

| Content type | Location | Interpretation |
| --- | --- | --- |
| Architecture description content | Documents `00` through `14`, `16`, `17`, and `19` | Describes system structure, behavior, viewpoints, models, concerns, correspondences, and facts. |
| Risk management content | `15-risk-register.md` | Tracks unresolved architecture and implementation risks. |
| Governance process | `08-development-validation-view.md`, `10-conformance-checklist.md`, and `20-stakeholder-review-governance.md` | Defines repository review rules and release gates. |
| Operational runbook requirements | `12-resilience-recovery-view.md`, `13-observability-view.md`, and `19-architecture-facts.md` | Defines required recovery and diagnostic procedures. |
| Formal standard conformance | `10-conformance-checklist.md` | Limited to public concept alignment; purchased-standard clause mapping is not assessed. |

## ID Namespace Rules

| Element | Pattern | Example |
| --- | --- | --- |
| Architecture description | `AD-[A-Z0-9-]+` | `AD-EVE-TRADE-ISO42010` |
| Stakeholder | `STK-##` | `STK-06` |
| Concern | `CON-##` | `CON-19` |
| Perspective | `PER-##` | `PER-04` |
| Aspect | `ASP-##` | `ASP-05` |
| Viewpoint | `VP-##` | `VP-12` |
| Model | `MODEL-[A-Z]+-##` | `MODEL-RUN-01` |
| View component | `VC-[A-Z]+-##` | `VC-DATA-02` |
| Correspondence | `COR-##` | `COR-08` |
| Architecture fact | `FACT-###` | `FACT-010` |
| Evidence anchor | `EVID-###` | `EVID-021` |
| Architecture risk | `RISK-###` | `RISK-002` |
| Threat | `THR-###` | `THR-004` |
| Architecture decision | `ADR-##` | `ADR-04` |
| Validation item | `VAL-###` | `VAL-011` |

IDs are stable once published. Retired IDs remain reserved and should not be
reused for a different meaning.

## ADL Status Vocabulary

| Status | Meaning |
| --- | --- |
| `Verified` | Evidence includes a passing command result, test result, rendered artifact, or stakeholder sign-off at the recorded baseline. |
| `Evidence-backed` | The concept or claim is tied to exact files, symbols, configuration keys, or manifests, but no current validation command proves it. |
| `Structurally represented` | The document set contains the required concept or model, but the claim is not fully evidenced or reviewed. |
| `Gap recorded` | A known architecture, implementation, operational, or evidence gap is intentionally documented and linked to a risk or follow-up. |
| `Unvalidated` | The item depends on stakeholder approval or live evidence that has not been obtained. |
| `Not run` | A validation command is defined but was not executed for the recorded update. |
| `Not assessed` | The item requires purchased-standard clause mapping, external certification, or another assessment outside this repository. |
| `Required before production` | The item is a release or exposure gate and must be closed or formally accepted before production use with untrusted callers. |

`Satisfied` is not an approved checklist status unless the row also records
explicit acceptance criteria, evidence, validation result, and reviewer
approval. Existing documents should prefer the statuses above.

## Enforcement Vocabulary

| Enforcement tag | Meaning |
| --- | --- |
| `Enforced by test` | A named test or validation command proves the rule at the recorded baseline. |
| `Enforced by code` | Implementation structure currently enforces the rule; no automated architecture guard is recorded. |
| `Enforced by manifest` | local Encore/Kubernetes, Kubernetes, Terraform, or policy manifests encode the rule. |
| `Enforced by policy` | A governance or release policy requires the rule, but mechanical enforcement is not shown. |
| `Enforced by review` | Human review is the current enforcement mechanism. |
| `Partially enforced` | Some controls exist, but a material gap remains. |
| `Not enforced` | The rule is only a target or required future state. |

## Evidence Levels

| Level | Required evidence |
| --- | --- |
| E0 None | No evidence beyond prose. |
| E1 Path | Repository path is cited. |
| E2 Symbol or object | File plus symbol, function, config key, message, resource name, table, or migration object is cited. |
| E3 Test or render | A named test, command, rendered manifest, or generated output is cited. |
| E4 Current pass | The command or review passed at the recorded baseline with date and result. |
| E5 Stakeholder accepted | A named reviewer, role, decision, date, and unresolved objections are recorded. |

Nontrivial architecture claims should reach at least E2. Production-readiness
claims should reach E4 or E5.

## Model Kind Schema Requirements

| Model kind | Required columns or fields |
| --- | --- |
| System context diagram | System boundary, external actors, internal services, data stores, brokers, observability destinations, legend. |
| Boundary table | Boundary ID, inside, outside, control intent, related aspect, evidence level. |
| Interface catalog | Interface, provider, consumer, contract source, purpose, evidence anchor. |
| Sequence diagram | Participants, normal path, alternative paths, durable state change points, failure paths, legend. |
| Responsibility table | Component, responsibilities, prohibited responsibilities, enforcement tag, evidence anchor. |
| Timeout budget table | Segment, configured value, source key, source anchor, current gap or contract status. |
| Invariant catalog | Invariant ID, statement, service enforcement, SQL enforcement, test or gap, evidence level. |
| Operation semantics table | Operation, required fields, tables touched, locks/transaction behavior, failure modes, tests or gaps. |
| Deployment model | Runtime element, location, role, ports/config, policy evidence, current precision. |
| Secret inventory | Secret, consumers, source of truth, owner, rotation, scope, audit/break-glass status. |
| Telemetry map | Runtime step, required fields, metric/log/span source, dashboard/alert, status. |
| Threat table | Threat ID, asset, entry point, STRIDE category, preconditions, severity, controls, verification, residual risk. |
| Validation matrix | Validation ID, goal, command/workflow, required-before-merge, last result, evidence anchor, limitations. |
| Correspondence matrix | Correspondence ID, method, source AD element, target AD element, verification status, evidence anchor. |
| Risk register | Risk ID, severity, probability, impact, owner, mitigation, status, due date, acceptance authority, residual severity, closure criteria, linked views. |

## View Component Register

View components are separable, reusable pieces inside a view: diagrams, tables,
state models, matrices, registers, and runbook fragments. Correspondences and
evidence should reference view components when a whole-file reference is too
broad.

| Component ID | View component | Owning document |
| --- | --- | --- |
| VC-CTX-01 | System Context Model | `03-context-view.md` |
| VC-CTX-02 | Boundary Model | `03-context-view.md` |
| VC-CTX-03 | Interface Catalog | `03-context-view.md` |
| VC-RUN-01 | Runtime Sequence Model | `04-functional-runtime-view.md` |
| VC-RUN-02 | Idempotency State Model | `04-functional-runtime-view.md` |
| VC-RUN-03 | Request Outcome Matrix | `04-functional-runtime-view.md` |
| VC-DATA-01 | Table-Level Model | `05-information-data-integrity-view.md` |
| VC-DATA-02 | Invariant Enforcement Matrix | `05-information-data-integrity-view.md` |
| VC-DATA-03 | Settlement Operation Semantics | `05-information-data-integrity-view.md` |
| VC-DEP-01 | Production-Like Deployment Model | `06-deployment-operations-view.md` |
| VC-DEP-02 | Network Policy Intent | `06-deployment-operations-view.md` |
| VC-DEP-03 | Secrets Model | `06-deployment-operations-view.md` |
| VC-SEC-01 | Trust Boundary Model | `07-security-trust-view.md` |
| VC-SEC-02 | Settlement API Hardening Requirements | `07-security-trust-view.md` |
| VC-VAL-01 | Validation Matrix | `08-development-validation-view.md` |
| VC-COR-01 | Correspondence Rules | `09-correspondences-rationale.md` |
| VC-PERF-01 | Timeout And Queueing Budget | `11-performance-capacity-view.md` |
| VC-RES-01 | Ambiguous Outcome Matrix | `12-resilience-recovery-view.md` |
| VC-OBS-01 | Telemetry Map | `13-observability-view.md` |
| VC-THR-01 | Threat Register | `14-threat-model-view.md` |
| VC-RISK-01 | Risk Register | `15-risk-register.md` |

## Framework Conformance Tests

| Test ID | Test | Required result |
| --- | --- | --- |
| VAL-ADF-001 | Every canonical document listed in `00-architecture-description.md` exists. | Pass before architecture review closes. |
| VAL-ADF-002 | Every view declares governing viewpoint and concerns addressed. | Pass before architecture review closes. |
| VAL-ADF-003 | Every ID referenced by a correspondence, concern, risk, or threat follows the namespace rules. | Pass before architecture review closes. |
| VAL-ADF-004 | Every high or critical risk has owner, due date, acceptance authority, residual severity, and closure criteria. | Pass before release-readiness claim. |
| VAL-ADF-005 | Every diagram renders successfully through the selected Mermaid renderer. | Required before publication outside repository review. |
| VAL-ADF-006 | Every markdown relative link and critical heading anchor resolves. | Pass before architecture review closes. |
| VAL-ADF-007 | Every production-readiness claim has E4 or E5 evidence. | Pass before production readiness can be claimed. |

The current repository has not implemented an automated architecture-document
linter. Until one exists, these tests are enforced by review and the validation
records in `18-evidence-manifest.md`.
