# Durable EVE-TRADE invariants

These are stable project constraints for reusable Codex work.

Task-specific prompts may add stronger requirements.

## Freshness and evidence

- Never rely on a previous repository review without verifying the current relevant tree.
- When a prompt gives a branch, expected commit title, SHA, CI run, or other freshness signal, verify it before using old conclusions.
- Record mismatches instead of forcing the tree to match an old assumption.
- Old `.o11y/runs`, old CI artifacts, prior chat conclusions, and stale local checkouts are historical context only.
- Current health claims require current, provenance-matched evidence.
- A green check that skipped the intended path is not evidence of correctness.

## Canonical runtime path

The production-oriented flow is:

```text
game frontend
→ Quilkin UDP
→ Encore gateway
→ Market
→ Encore Pub/Sub settlement work
→ settlement worker
→ Rust trade-settlement
```

Preserve clear ownership at every boundary.

## Go / Encore boundaries

- Gateway owns UDP-edge concerns, not trade rules.
- Market owns game-trade interpretation and planning.
- Market must not directly own settlement database mutation.
- Transport handlers must not become the canonical home for domain rules, authorization policy, fingerprints, retry policy, or durable lifecycle policy.
- Side effects should sit behind narrow adapters where practical.

## Rust settlement boundary

- Rust trade-settlement is the correctness-critical sole writer for settlement state.
- It must independently enforce structural and semantic correctness required at the persistence boundary.
- It must not blindly trust Go merely because the caller is internal.
- Actor authority, resource ownership, legal state transitions, and whole-plan semantics must be explicit where relevant.

## Async durability

- At-least-once delivery is assumed where the platform provides it.
- Duplicate work and duplicate results must be harmless.
- Durable state must distinguish accepted/queued work from processing and terminal success/failure where the task touches async lifecycle.
- A committed settlement must not be repeated merely because result delivery failed.
- Crash boundaries and retry ownership must be explicit.

## Idempotency and replay

- Server-side correctness must not trust client-supplied fingerprints as authority.
- Canonical fingerprints must cover the correct semantic domain.
- Correctness-sensitive 64-bit values must not pass through lossy floating-point decoding.
- In-memory replay/rate-limit state must be bounded.
- Cross-replica semantics must be deliberate and documented.

## Security

- Security-relevant request metadata must be authenticated when the protocol requires trust in it.
- Do not log secrets, raw credentials, or secret material.
- Internal service status does not remove the need for explicit authority at correctness-critical boundaries.
- Do not weaken production strict-mTLS design when adding defense in depth.

## Health and deployment

- Liveness means the process/event loop can serve.
- Readiness means the advertised service path and required dependencies are actually usable.
- A pod must not remain Ready while its externally advertised UDP path is dead.
- Production artifacts must identify real produced images, not syntactically valid placeholders.

## Observability truth

- A failure observation is not automatically a root cause.
- Use exact provenance, real chronology, dependency edges, and primary producer evidence.
- Missing mandatory evidence is `INSUFFICIENT_EVIDENCE`, not a confident diagnosis.
- Parallel failures remain independent unless evidence links them.
- Confidence must be proportional to evidence.

## Verification

- Do not fix failures by deleting tests, weakening assertions, skipping E2E, converting required failures to warnings, or hiding missing evidence.
- Local verification and canonical CI should share the same truth as far as the environment allows.
- Partial verification must say it is partial.
- Prefer black-box E2E evidence for end-to-end behavior claims.
- Architecture boundaries that matter should be enforced, not merely documented.

## Refactoring

- Consolidate duplicated policy only after understanding the concrete repeated behavior.
- Prefer typed states, canonical representations, explicit ownership, deterministic behavior, composable validation, and narrow side-effect adapters.
- Do not add abstraction that merely renames another abstraction.
- Performance-sensitive paths require evidence before accepting allocation or latency regressions.
