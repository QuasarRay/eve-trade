# Law/property-first execution workflow

1. **PRECHECK** — capture governing digests, instruction provenance, repository boundaries, user work, compiled policy, scope/budgets, commands, gates, review requirements, and the TDD plan.
2. **TRIAGE/PLAN** — classify behavioral change, bug fix, refactor, migration, security, compatibility, or non-behavioral work; define the invariant, observation boundary, independent oracle, and falsifier.
3. **TEST_DESIGN** — write the property/law first. Prefer Hypothesis for stateful, combinatorial, temporal, hostile-input, path, recovery, and policy behavior. Add deterministic regressions for valuable minimized examples.
4. **BASELINE_EXECUTION** — obtain legitimate semantic RED for missing/broken behavior, characterization GREEN for pure refactor, or test-first observation for justified non-behavioral work. Never manufacture RED.
5. **FREEZE** — bind test, oracle, baseline implementation, harness, expected mode, and gate expectations by digest.
6. **IMPLEMENT** — make the smallest coherent production change within compiled authority. Tests and oracles remain read-only.
7. **GREEN** — execute the same frozen contract against the current implementation epoch.
8. **FALSIFY/REVIEW** — attempt counterexamples, mutation, fault injection, stale evidence, bypasses, scope escape, and compatibility divergence. Review is independent and diff/evidence-bound where required.
9. **REGRESSION-FIRST REMEDIATION** — every new finding starts a new test-first cycle before its fix.
10. **VERIFY/FINAL AUDIT** — run focused, standard/stress property, complete, law, traceability, clean deployment, and workspace-integrity campaigns. Finalize only from current sealed evidence.

Pure refactor never justifies fake RED. An incorrect test contract is explicitly aborted, corrected, re-digested, and rerun against a preserved or equivalent pre-implementation baseline.
