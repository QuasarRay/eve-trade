# Aegis Framework 4.0.0

Aegis is a project-independent, constitutionally opinionated software-engineering framework for coding agents. Projects define domain correctness; they may strengthen Aegis, but they cannot weaken its requirements for Test-Driven Development, evidence, falsification, review, workspace integrity, or truthful completion.

## Trust and deployment model

The three security domains are deliberately separate:

```text
framework source/   trusted human development and release verification
.agents/             deployed governing input; ordinary agents read but never write
.aegis/              mutable task state, evidence, locks, caches, compiled policy, and audits
```

This repository root is the authoritative framework source. The checked-in active `.agents` tree is not a development mirror and is never synchronized by source commands. A human maintainer verifies a deterministic artifact outside `.agents`, then manually copies that artifact to the target repository.

Source-maintainer commands:

```text
python -B bin/agentctl.py --root . doctor
python -B bin/agentctl.py --root . audit
python -B infra/law_tests/run_suite.py --root . --output .aegis/law-results/latest.json
python -B scripts/verify_release.py
python -B bin/agentctl.py --root . package build dist/aegis-governance
```

The last command produces `dist/aegis-governance/.agents/`. It never writes the active `.agents` tree. After verification, a human may copy that directory into a target repository and manually merge the reviewed block from `bootstrap/root-AGENTS.block.md` into the target root `AGENTS.md`. Ordinary agent execution exposes verification but no bootstrap install, uninstall, module install, module scaffold, manifest-write, or self-update command.

## Constitutional core

The executable constitution defines AEGIS-I001 through AEGIS-I022. It enforces:

- immutable governing policy and frozen acceptance criteria;
- no self-waiver and no weakening of HARD gates;
- current implementation/evidence epochs and production-path truth;
- contract-test and oracle integrity;
- independent acceptance, epistemic honesty, and capability honesty;
- controlled scope, user-work preservation, and minimal unjustified change;
- one active child globally, no nested delegation, and parent canonical authority;
- Max reasoning where supported, mandatory falsification, and reference/oracle integrity;
- test-first implementation authority, frozen RED/characterization contracts, same-contract GREEN, property-first assurance, and regression-first remediation.

Project contracts may add architecture, commands, laws, generated paths, compatibility requirements, dependencies, benchmarks, deployment targets, and stronger policy packs. Unknown constitutional keys or weakening values fail closed.

## Enforced TDD lifecycle

Mutating behavioral work follows an executable lifecycle:

```text
PRECHECK -> TRIAGE -> PLAN -> TEST_DESIGN -> BASELINE_EXECUTION
         -> RED_OBSERVED | CHARACTERIZATION_OBSERVED | TEST_FIRST_OBSERVED
         -> IMPLEMENT -> GREEN -> FALSIFY -> REVIEW -> VERIFY
         -> FINAL_AUDIT -> FINALIZE
```

The compiled task contract selects `RED_REQUIRED`, `CHARACTERIZATION_REQUIRED`, or a justified non-behavioral test-first mode. Implementation write authority does not exist until the test/oracle digests are frozen and the required baseline observation is bound to the pre-implementation digest. GREEN must use the same frozen test and oracle against the current implementation epoch. A changed contract, oracle, governance snapshot, baseline, or harness revokes authority. A counterexample discovered after implementation starts a new regression-first cycle.

Hypothesis is a development/test dependency, not a runtime dependency. The stdlib-only control plane remains recoverable without it. Profiles are `focused` (25 examples), `standard` (100), and `stress` (500); `RuleBasedStateMachine` models cover temporal TDD authority and evidence-cycle behavior.

## Policy compiler and PRECHECK

Structured project contracts compile into content-addressed artifacts under `.aegis/compiled-policy/`. Compilation performs monotonic task classification, applies non-weakenable policy packs, generates HARD/REQUIRED/ADVISORY gates, resolves source/generated/immutable/reference boundaries, freezes write scope and budgets, records command matrices, and derives review requirements. Reimplementation work requires a read-only reference contract, differential oracle, compatibility decisions, and reference digest.

PRECHECK is artifact-based. It records governance and instruction provenance, repository discovery, workspace/user-change boundaries, test/law baselines, TDD plan, compiled policy, gates, budgets, commands, and review requirements. Boolean self-reports do not complete it.

## State, evidence, review, and final audit

Canonical task state lives under `.aegis/tasks/`; evidence, locks, cache, policy, audit, manifest, and migration state use distinct `.aegis` subdirectories. State transitions are locked, revisioned, append-only, epoch-aware, and anchor-history protected. Legacy `.agents/runtime` and `.agents/persistent` data can be copied into `.aegis` by the explicit, idempotent runtime migration command; migration never deletes or rewrites governance.

Evidence distinguishes observation, external authority, inference, assumption, untested, unavailable, and blocked outcomes. Gate proof requires task/epoch relevance. REQUIRED waivers require current task-bound external user/host evidence; HARD gates cannot be waived. Review receipts bind reviewer independence, current diff, requirements, evidence, specialist role, findings, and concrete falsification attempts.

Finalization requires the exact 40-check audit contract. Every check is evidence-sealed and workspace-bound; missing, manual-only, stale, blocked, or tampered observations fail closed. The final workspace fingerprint is recomputed on load after finalization.

## Laws, properties, and capability truth

The acceptance system has two exact inventories:

- 834 historical `tests-to-impl` requirements with a deterministic machine-readable registry;
- 105 constitutional/TDD laws bound to exact executed test methods or generated state-machine `runTest` cases.

Properties may subsume multiple named regressions only through reviewed observation mappings. A collected-but-unstarted, incomplete, vacuous, skipped, mutated, or unsealed result cannot become PASS. Host limitations are recorded as `UNAVAILABLE`, `BLOCKED`, `UNTESTED`, or justified `NOT_APPLICABLE`; they are never relabeled PASS.

## Threat model and limits

Aegis prevents writes through its managed mutation APIs and independently detects out-of-band governance changes. Path checks cover traversal, case aliases, symlinks, Windows junctions/reparse points, rename endpoints, Git/codegen/formatter destinations, and redirected `.aegis` state. No in-process framework can prevent an already-privileged external process from editing files; digest checkpoints and final workspace verification make such changes invalidate the task. Platform behavior not exercised on the current host remains an explicit capability limitation.

The recovery-critical implementation uses Python 3.11+ and the standard library. Optional `mcpyrate`, `unpythonic`, Xonsh, and host adapters are capability-scoped and cannot weaken the constitutional core.

See [INDEX.md](INDEX.md), [MIGRATION.md](MIGRATION.md), [protocols/STATE.md](protocols/STATE.md), [protocols/LAW_TESTS.md](protocols/LAW_TESTS.md), and [infra/README.md](infra/README.md).
