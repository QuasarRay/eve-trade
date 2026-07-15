# Stakeholder Review and Governance Register

## Metadata

| Field | Value |
| --- | --- |
| Register ID | `GOV-EVE-TRADE-001` |
| Date | 2026-06-22 |
| Status | Canonical governance and review register |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

## Purpose

This register separates stakeholder validation and governance from architecture
description content. It prevents repository-derived assumptions from being
mistaken for stakeholder approval.

## Review Status Vocabulary

| Status | Meaning |
| --- | --- |
| `Signed off` | Named reviewer or accountable team accepted the concern or view on a recorded date. |
| `Accepted with objections` | Reviewer accepted the content but unresolved objections are listed. |
| `Repository-derived` | Concern or rule was inferred from source code, tests, manifests, or existing docs. |
| `Assumed` | Concern or stakeholder need was inferred by maintainers and needs real stakeholder review. |
| `Unvalidated` | No stakeholder acceptance exists. This is the default for assumed and repository-derived rows. |
| `Rejected` | Reviewer disagreed with the concern, priority, or view content and the issue must be resolved. |

Unless a row has `Signed off` or `Accepted with objections`, it is not
stakeholder-validated.

## Owner Resolution Rule

Architecture documents may use roles instead of personal names, but every role
must resolve to an accountable party during review.

| Role label | Accountable party until replaced |
| --- | --- |
| Architecture owner | Backend maintainers |
| Backend owner | Backend maintainers |
| Product owner | Gameplay/product maintainer group |
| SRE/platform operator | Platform maintainers |
| Security reviewer | Security review owner or delegated reviewer |
| Database/migration owner | Database/schema maintainer |
| QA/test engineer | Test owner or CI/release maintainer |
| Observability/on-call engineer | On-call/observability maintainer |
| CI/release maintainer | Release maintainers |

If this repository is operated by a single maintainer, that maintainer may act
for multiple roles, but the review record must say which role was represented.

## Concern Priority Method

Concern priority is scored from impact, likelihood, stakeholder criticality,
and reversibility.

| Priority | Scoring rule |
| --- | --- |
| Critical | Failure can corrupt high-integrity trade/wallet/item state, permit unauthorized actor behavior, block safe production operation, or leave committed outcomes unrecoverable. |
| High | Failure can break integration, cause material outage, hide incidents, create serious operational toil, or make future evolution unsafe. |
| Medium | Failure creates review, maintenance, cost, or future-risk burden but does not immediately block safe operation. |
| Low | Failure is mostly readability, hygiene, or local maintainability. |

Critical concern priority requires either stakeholder sign-off or explicit risk
acceptance before production readiness can be claimed.

## Concern Type Taxonomy

| Type | Meaning | Example |
| --- | --- | --- |
| Stakeholder need | An outcome a stakeholder requires from the architecture. | Actor identity must be explicit and trustworthy. |
| Architectural constraint | A rule the architecture must obey. | PostgreSQL is the source of truth for settlement state. |
| Design decision | A chosen solution approach. | Market owns trade mechanics. |
| Known gap | A missing implementation, evidence, runbook, or decision. | DLQ redrive runbook is absent. |

The concern register in `01-stakeholders-concerns-perspectives.md` keeps the
ISO concern IDs focused on stakeholder-relevant matters. Design decisions and
known gaps are cross-linked through rationale and risk IDs instead of being
treated as stakeholder approval.

## Stakeholder Review Log

No stakeholder sign-off was obtained during the documentation-only remediation
on 2026-06-22. The rows below intentionally mark the current review state as
unvalidated until actual reviewers accept or reject the content.

| Stakeholder | Required review scope | Current review state | Required evidence for sign-off |
| --- | --- | --- | --- |
| STK-01 Game server integrator | API contract, timeout behavior, idempotency contract, ambiguous outcome handling | Unvalidated | Reviewer, date, accepted command semantics, unresolved objections |
| STK-02 Gameplay/product owner | Trade lifecycle, expiration, cancellation, player-visible outcomes | Unvalidated | Product decision on expiration cleanup and lifecycle states |
| STK-03 Backend service developer | Service responsibilities, protobuf compatibility, implementation alignment | Repository-derived, not signed off | Maintainer review record |
| STK-04 Settlement/data integrity owner | Idempotency, transaction, ledger, escrow, failure metadata, operation semantics | Unvalidated | Data integrity sign-off and invariant test evidence |
| STK-05 SRE/platform operator | Probes, networking, DLQ, recovery, capacity, backup/restore | Unvalidated | Operations sign-off, runbook acceptance, validation evidence |
| STK-06 Security reviewer | Actor binding, settlement privilege, mesh/network controls, secrets, threat model | Unvalidated | Security sign-off or explicit production-blocking rejection |
| STK-07 Database/migration owner | Migrations, constraints, retention, rollback, archival, backup/restore | Unvalidated | Database owner review record |
| STK-08 QA/test engineer | Validation matrix, e2e scope, failure-mode tests, replay tests | Unvalidated | Test plan acceptance and latest results |
| STK-09 Observability/on-call engineer | Telemetry, alerts, dashboards, incident queries, correlation keys | Unvalidated | On-call review and dashboard/alert evidence |
| STK-10 CI/release maintainer | Build, lint, render, image, deploy, placeholder gates | Repository-derived, not signed off | CI/release review and gate evidence |

## Review Record Template

| Field | Required content |
| --- | --- |
| Review ID | Stable ID such as `REV-2026-06-22-SEC-01`. |
| Reviewer | Person or accountable team. |
| Stakeholder role represented | One or more `STK-##` IDs. |
| Scope | Documents, concerns, risks, or gates reviewed. |
| Decision | `Signed off`, `Accepted with objections`, or `Rejected`. |
| Date | Review date. |
| Evidence | PR, issue, meeting note, test result, or written approval. |
| Objections | Unresolved objections, if any. |
| Follow-up | Risk IDs, action IDs, or required changes. |

## Governance Gates

| Gate | Mechanism required | Current enforcement |
| --- | --- | --- |
| Architecture document linting | Link, anchor, ID, table-schema, and required-section checks | Manual review and local checks; automated linter not implemented |
| Mermaid render validation | Render all Mermaid diagrams in CI or documented review | Not implemented |
| Production placeholder rejection | CI, release script, admission policy, or kustomize policy check | Not implemented |
| Security review | Required review for identity, trust boundary, mesh, secret, and settlement API changes | Policy only |
| Data integrity review | Required review for migration, ledger, idempotency, and settlement operation changes | Policy only |
| Runtime validation | local Encore/Kubernetes, e2e, Kubernetes render, Terraform validation, and relevant unit tests | Commands defined; not run in this doc update |
| Risk acceptance | Accepted-by, due date, residual severity, and closure criteria for open risk | Added in risk register; requires future execution |

## Change Log Lifecycle Rule

Architecture change logs must state whether each flaw was:

- fixed in documentation;
- fixed by implementation;
- documented as an open production blocker;
- deferred with owner and due date;
- not fixable without stakeholder review or runtime validation.

`Architecture/changes.md` records the first remediation pass.
`Architecture/changesv2.md` records the second remediation pass.
