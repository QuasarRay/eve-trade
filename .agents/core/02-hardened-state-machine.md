# Hardened TDD task state machine

Canonical state is parent-owned under `.aegis/tasks/<task-id>/state.json`. Children submit bounded evidence and recommendations; they never own the canonical task or final claim.

For mutating behavioral work, the authority path is:

```text
CREATED -> PRECHECK -> TRIAGE -> PLAN -> TEST_DESIGN -> BASELINE_EXECUTION
        -> RED_OBSERVED | CHARACTERIZATION_OBSERVED | TEST_FIRST_OBSERVED
        -> IMPLEMENT -> GREEN -> FALSIFY -> REVIEW -> VERIFY
        -> FINAL_AUDIT -> FINALIZE
```

Exploration, research, architecture, diagnosis, remediation, and blocking states are constrained side paths. `FAILED` and `FINALIZE` are terminal. `BLOCKED` records and resumes only its prior state.

Implementation authority requires current governance, a compiled scope, frozen test/oracle digests, a pre-implementation baseline digest, a valid harness, a semantic failure for RED-required work (or GREEN characterization for pure refactor), and no active child conflict. Entering implementation advances the change epoch and invalidates verification, review, previously proven gates, and final audit.

Finalization requires current same-contract GREEN, falsification, independent review where compiled, verified gates, resolved critical risks, no child lease, valid evidence chain/anchors, the exact 40-check final-audit receipt, and an unchanged workspace fingerprint. Caller-edited state cannot substitute for the bound evidence records.
