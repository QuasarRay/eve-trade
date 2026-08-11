# Sequential Subagent Protocol

Hard invariant: **zero or one active child context globally per framework workspace**. Never fan out.
Never start the next child until the previous child has reached a terminal handoff, its load-bearing claims
have been checked by the parent, canonical state is updated, and its logical lease is closed. Closing through
`agentctl subagent close` requires an `accepted`, `rejected`, or `partial` outcome plus a compact integration
summary; optional evidence IDs are validated and the handoff is retained in canonical `child_history`.

Children may not spawn children. Host adapters should enforce this mechanically where possible and also
state it in every role instruction. The Codex adapter sets V1 spawn depth to one and, for the current V2
backend, caps total session threads at two (root + at most one child). The host-independent logical lease is
the cross-backend source of truth because current Codex V1/V2 concurrency controls are not safely portable
as a single legacy setting; policy prohibition remains authoritative wherever host enforcement is incomplete.

## Context isolation

A child gets a fresh bounded brief, not an automatic dump of parent history. Include only mission,
verified facts needed to avoid rediscovery, exact relevant paths/symbols, constraints, current evidence,
write permissions, non-goals, and required return shape. Reviewers/verifiers should receive requirements
and observable evidence/diff, not the implementer's persuasive narrative, to reduce anchoring.

## Logical lease

`agentctl subagent open` atomically creates `.aegis/leases/subagent-lease.json` and records the lease ID
in canonical `.aegis` task state. The lease deliberately outlives the short CLI process. `close` checks that the
global lease and task lease agree before clearing state. A mismatch fails closed.

Recovery is never automatic. If a crash leaves a stale logical lease, inspect it and use explicit
`subagent recover --force --reason ...`; record why recovery is safe. This is preferable to heuristically
assuming a long-running child is dead.

## Delegation economics under Max reasoning

All agent reasoning defaults to Max. Save cost by delegating only when an isolated context has positive
expected value: difficult root-cause analysis, architecture, compatibility/security review, or independent
verification. Keep deterministic searches, formatting, simple edits and mechanical commands in the parent
or direct tools. Fewer Max turns is the primary cost lever; silently lowering reasoning effort is not.
