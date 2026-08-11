# Canonical state and evidence protocol

## Runtime layout

All ordinary mutable control data is under `.aegis`:

```text
.aegis/tasks/             canonical task state and anchors
.aegis/evidence/          append-only evidence records
.aegis/leases/            global child/control locks
.aegis/compiled-policy/   content-addressed task contracts
.aegis/cache/             content-freshness cache
.aegis/audit/             TDD/final/release receipts
.aegis/manifests/         runtime-generated manifests
.aegis/state/             migration/install recovery metadata
```

No runtime helper resolves beneath `.agents`. Redirected `.aegis` paths (symlink/junction/reparse escape) fail closed.

## TDD authority and epochs

`PRECHECK`, `TRIAGE`, `PLAN`, `TEST_DESIGN`, `BASELINE_EXECUTION`, the exact observed baseline mode, `IMPLEMENT`, `GREEN`, `FALSIFY`, `REVIEW`, `VERIFY`, `FINAL_AUDIT`, and `FINALIZE` are first-class authority boundaries. Test design cannot write its corresponding production scope; implementation cannot begin until the frozen baseline predicate is true.

Each implementation/remediation entry advances `change_epoch`, clears verification/final audit, stales review, and reopens proven gates. Evidence used for proof must bind task ID, task revision or gate creation point, current epoch, relevant gate IDs, and an allowed provenance. Failed commands and caller-authored manual claims do not prove gates.

HARD gates are never waivable. REQUIRED gates may be waived only with current task/epoch external evidence minted by trusted user/host authority. ADVISORY omission requires recorded justification and cannot satisfy a mandatory gate family.

State and evidence writes are locked, atomic, revision-checked, and anchor-history verified. Whole-history rollback, deleted gate/risk history, evidence-tail mismatch, corrupt state, stale anchor, and concurrent mutation fail closed.

## Review and finalization

Review receipts bind independent actor, diff, requirements, evidence, epoch, findings, specialist role where required, and falsification attempts. Rubber stamps and self-review are rejected.

The final audit requires an exact bijection with the 40 required checks. Every observation is sealed with evidence digest, workspace digest, provenance, and current status. Missing, stale, manual, blocked, or unproved checks reject the audit. Finalized loads recompute the workspace and evidence head so post-finalization mutation cannot leave a raw terminal state accepted.
