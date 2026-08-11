# Aegis source policy router

This repository root is the authoritative framework source. The active `.agents/` tree is deployed governance and is immutable to ordinary agents; do not mirror source edits into it. Mutable execution state belongs under `.aegis/`.

## Constitutional and workflow policy

- `core/00-charter.md` — objective order, constitutional invariants, truthfulness, and scope.
- `core/01-max-reasoning-and-cost.md` — Max reasoning and cost optimization without quality reduction.
- `core/02-hardened-state-machine.md` — TDD-aware task lifecycle and finalization gates.
- `core/03-sequential-subagents.md` — one child, no nesting, parent canonical authority.
- `core/04-context-cache-bandwidth.md` — content-addressed freshness and bounded context.
- `core/05-evidence-claims.md` — epistemic evidence classes and provenance.
- `core/06-execution-workflow.md` — law/property-first RED/characterization to GREEN workflow.
- `core/07-code-architecture-quality.md` — maintainability and architectural discipline.
- `core/08-testing-and-laws.md` — frozen test contracts, Hypothesis, anti-cheating, and regression-first repair.
- `core/09-git-workspace.md` — scope, user-owned dirty work, and references.
- `core/10-tools-and-shells.md` — direct argv and environment selection.
- `core/11-security-dependencies.md` — dependency, secret, and supply-chain discipline.
- `core/12-failure-recovery.md` — atomicity, crash recovery, and fail-closed behavior.
- `core/13-performance.md` — measured resource and cache behavior.
- `core/14-reporting.md` — evidence-calibrated final claims.

## Executable protocols

- `protocols/STATE.md` — `.aegis` layout, canonical state, TDD phases, epochs, gates, and final audit.
- `protocols/LAW_TESTS.md` — source/deployment law execution, 834+105 inventories, and capability truth.
- `protocols/SUBAGENT.md` — global sequential lease and bounded handoff.
- `protocols/MODULES.md` — source/deployed modules and immutable governance constraints.
- `protocols/ENVIRONMENT_SELECTION.md` — shell/host capability selection.
- `infra/README.md` — source-side control-plane commands and architecture.
- `infra/law_tests/README.md` — traceability and outer-runner contracts.
- `MIGRATION.md` — legacy runtime migration and human-only deployment.

Project contracts may specialize domain behavior and strengthen requirements. They cannot disable the constitution, TDD, property requirements, evidence freshness, falsification, independent review, scope controls, or test/reference integrity.
