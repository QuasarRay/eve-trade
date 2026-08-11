# Strict Sequential Subagents

## Global invariant

At most one child/subagent working context may be active. No fan-out. No parallel children.
No nested delegation. Do not keep completed child threads open as speculative future workers.

Lifecycle:

`justify -> bounded brief -> acquire child lease -> spawn -> wait -> receive -> close child ->
release lease -> verify evidence/diff -> integrate canonical state -> compact -> reconsider`

Use `agentctl subagent open/close` when practical; host adapters should additionally enforce a
one-child concurrency cap where the platform supports it.

## Spawn gate

Create a child only when isolated context materially improves correctness, independence, or
context health. Do not delegate deterministic bookkeeping, formatting, trivial lookup, or an
obvious edit. Every spawn needs one bounded objective, explicit non-goals, permissions, and a
proof/return contract.

All children use Max reasoning when configurable.

## Independent context

Never dump the parent transcript. Send verified facts with source locations, relevant changed
files, minimal failure evidence, acceptance condition, and constraints. A reviewer receives
requirements + actual diff/evidence, not the implementer's persuasive narrative.

## Parent ownership

Children do not edit canonical framework runtime state, orchestration configuration, or agent
policy unless the user's task is specifically to modify the framework. Child reports are
claims; parent checks load-bearing evidence and changed files before reliance.
