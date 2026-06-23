# ISO 42010 Conformance Checklist

## Checklist Metadata

| Field | Value |
| --- | --- |
| Checklist status | Canonical internal gap assessment |
| Last reviewed | 2026-06-23 |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |
| Certification status | Not certified; clause-level normative audit not assessed |

## Purpose

This checklist maps the `eve-trade` architecture description to the public
ISO/IEC/IEEE 42010 architecture description concepts used by this document set.
It is a repository gap-assessment checklist, not a certification statement and
not a substitute for a purchased-standard clause audit.

Checklist status values are defined in
[Architecture Framework and Language Specification](./17-adf-adl-specification.md#adl-status-vocabulary).
This checklist does not use `Satisfied` as a broad synonym for "present."

## Checklist

| ISO 42010 concept | Status | Acceptance criteria | Evidence | Gap or note |
| --- | --- | --- | --- | --- |
| Architecture description has identifier and metadata | Evidence-backed | Identifier, date, version, status, maintainer, location, and baseline are recorded. | `00-architecture-description.md`, `18-evidence-manifest.md` | File hashes provide reproducible document baseline until commit. |
| Entity/system of interest is identified | Evidence-backed | Included/excluded scope is explicit. | `00-architecture-description.md` | None recorded. |
| Environment is identified | Evidence-backed | Local and production-like environments are described. | `00-architecture-description.md`, `06-deployment-operations-view.md` | Static/unit/render validation ran; live e2e flow execution did not run. |
| Purpose of the AD is identified | Evidence-backed | Intended use and audience are stated. | `00-architecture-description.md` | None recorded. |
| Architecture Description Framework is identified | Evidence-backed | Custom framework name, scope, work products, tailoring, extension, and governance are defined. | `17-adf-adl-specification.md` | Project-specific framework, not an external ADF. |
| Architecture Description Language is identified | Evidence-backed | Markdown/Mermaid/table conventions, ID rules, status vocabulary, model schemas, and evidence levels are defined. | `17-adf-adl-specification.md` | No automated ADL parser yet. |
| Stakeholders are identified | Structurally represented | Stakeholder classes, roles, accountability, and review status are listed. | `01-stakeholders-concerns-perspectives.md`, `20-stakeholder-review-governance.md` | Stakeholder sign-off is unvalidated. |
| Stakeholder concerns are identified | Structurally represented | Concerns have IDs, priority, type, owner, source class, confidence, and review status. | `01-stakeholders-concerns-perspectives.md` | Most concerns are repository-derived and unvalidated. |
| Stakeholder perspectives organize concerns | Evidence-backed | Perspectives group concerns and stakeholders. | `01-stakeholders-concerns-perspectives.md` | None recorded. |
| Architecture aspects are identified | Evidence-backed | Cross-cutting aspects are listed, tied to concerns, and mapped to view components. | `01-stakeholders-concerns-perspectives.md`, `17-adf-adl-specification.md` | Some aspects point to open risks. |
| Architecture viewpoints are specified | Evidence-backed | Viewpoints have stakeholders, concerns, model kinds, notation, required elements, and analysis methods. | `02-viewpoints.md` | Clause-level standard mapping not assessed. |
| Viewpoint conventions are sufficient to govern views | Structurally represented | Construction, interpretation, analysis, failure criteria, model IDs, and component IDs exist. | `02-viewpoints.md`, `17-adf-adl-specification.md` | Needs future reviewer validation and linter enforcement. |
| Model kinds are defined | Evidence-backed | Model kind construction, invalidity rules, IDs, and table schemas are listed. | `02-viewpoints.md`, `17-adf-adl-specification.md` | Formal metamodel classes are not machine-enforced. |
| Views are provided | Evidence-backed | Views exist for context, runtime, data, deployment, security, development, performance, recovery, observability, and threats. | Files `03` through `14`. | None recorded. |
| Views declare governing viewpoints | Evidence-backed | Each view has a Governed by section or metadata. | Files `03` through `14`. | Linter not implemented. |
| Views address stakeholder concerns | Structurally represented | Each view lists concern satisfaction or maps to concern coverage. | Files `03` through `14`. | Stakeholder validation is missing. |
| Architecture models are included in views | Evidence-backed | Views include diagrams, tables, matrices, state models, and registers with model IDs recorded in `02`. | Files `03` through `15`, `02-viewpoints.md` | Mermaid render verification not implemented. |
| Model legends or explanations are provided | Evidence-backed | Diagrams and non-obvious tables have legends/explanations. | Views and model kind specs. | Some diagrams rely on companion tables for detail. |
| Correspondences are recorded | Evidence-backed | Correspondence rules, method definitions, participating elements, and verification statuses are recorded. | `09-correspondences-rationale.md` | Automated consistency checks do not exist. |
| Consistency among views is considered | Structurally represented | Consistency checks and correspondence matrices are recorded. | `09-correspondences-rationale.md` | Human review only. |
| Architecture rationale is recorded | Structurally represented | ADR register and rationale exist. | `09-correspondences-rationale.md` | ADRs need future expansion as full decision records during normal governance. |
| Known limitations and risks are recorded | Evidence-backed | Limitations, production gates, and expanded risk register exist. | `00-architecture-description.md`, `15-risk-register.md`, `19-architecture-facts.md` | Critical identity and operations risks remain open. |
| Evidence baseline is identified | Evidence-backed | Commit, date, validation scope, architecture file hashes, and source anchors are listed. | `18-evidence-manifest.md` | Static/unit/render validation is recorded; live e2e flow execution remains a gap. |
| Historical material is distinguished from current architecture | Evidence-backed | Historical conflict register exists and historical docs carry deprecation banners. | `00-architecture-description.md`, `Architecture/README.md`, historical `v1.md` files | Historical docs remain retained for design history. |
| Clause-level normative conformance | Not assessed | Requires access to the purchased standard text and formal review. | Public references only. | Not certified. |

## Completeness Review

| Review question | Answer |
| --- | --- |
| Does every stakeholder have at least one concern? | Yes, but stakeholder sign-off is unvalidated. |
| Does every concern have at least one view? | Yes. Concern coverage is listed in document `01` and repeated by views. |
| Does every view have a governing viewpoint? | Yes. Documents `03` through `14` declare governing viewpoints. |
| Does every viewpoint define model kinds? | Yes. Document `02` includes model kind rules and viewpoint conventions. |
| Are cross-view relationships explicit? | Yes. Document `09` records correspondence rules, matrices, and consistency checks. |
| Is rationale explicit for major design decisions? | Partially. ADRs have a register and rationale, but future updates must use the full ADR metadata rule. |
| Are known architecture gaps explicit? | Yes. Documents `00`, `07`, `12`, `14`, and `15` record critical gaps and risks. |
| Are validation results tied to dates? | Partially. Static/unit/render checks are recorded in `18-evidence-manifest.md`; live e2e flow execution and full CI/Terraform validation remain open. |

## Open Conformance Risks

| Risk | Related flaw IDs | Current handling |
| --- | --- | --- |
| Stakeholder concerns are not externally validated. | F-008, F-009, V2-009, V2-010, V2-011 | Review status, owner resolution, priority method, and sign-off template are documented in `20-stakeholder-review-governance.md`. |
| Clause-level ISO assessment is not performed. | F-001, F-007, V2-002 | Checklist no longer claims certification, full normative audit, or broad satisfaction. |
| Live e2e and full release validation were not run during this repair. | F-013, F-048, V2-015 | Evidence manifest records passed static/unit/render checks and the remaining live-runtime gap. |
| Implementation security gaps remain. | F-020, F-042, F-043, V2-036, V2-037 | Security and threat views mark identity and settlement privilege gaps as production blockers. |
| Operations runbooks are incomplete. | F-028, F-029, F-040, V2-025, V2-034 | Resilience, observability, architecture facts, and risk views record runbook gaps and required gates. |
| Architecture document validation is manual. | V2-047, V2-049 | ADF/ADL validation tests are specified; automated linter/render checks remain open governance gates. |

## Maintenance Rule

This checklist must be updated whenever a new architecture view, viewpoint,
stakeholder group, concern, correspondence, model kind, material design
rationale, or open risk is added or removed.
