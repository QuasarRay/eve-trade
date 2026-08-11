# Aegis Sequential Max Codex Skill

Use repository-root `AGENTS.md`, then `.agents/INDEX.md`, then this module only when Codex is the active host.

## Before delegation

1. Prefer direct deterministic tools when delegation would add no independent reasoning value.
2. When a child is justified, ensure the parent session is `gpt-5.6-sol` at `max` reasoning.
3. Inspect effective Codex configuration with `/status` and `/debug-config` when available. Static TOML is
   configuration intent, not runtime proof.
4. Acquire the Aegis logical child lease **before** spawning.
5. Build a fresh bounded brief from canonical state; do not fork uncontrolled full conversation history.

## Spawn

Spawn exactly one child. Prefer the registered `aegis_*` role matching the mission. The role files pin Sol/Max
and no-delegation, while the project-level defaults independently pin Sol/Max so model/effort remain correct
if a client version falls back to parent inheritance.

Codex multi-agent APIs can vary by backend/version. If the current spawn interface cannot select a registered
custom role, do not pretend it did. Use a generic single child with explicit Sol/Max routing when supported and
include the relevant role mission + shared child invariants in the bounded spawn brief. Record the role-profile
application as unverified. Never respond to a missing role selector by spawning additional children.

Never use parallel fan-out. Never keep two child threads active. Children never delegate.

## Close and integrate

Wait for terminal child output. Treat the report as claims, not proof. Parent checks load-bearing evidence,
integrates accepted findings into canonical state, compacts the phase, and then closes/releases the logical
lease. Only after that may another child be considered.

For mutating tasks, follow the hardened task lifecycle, acceptance gates, evidence chain and final-audit
workspace fingerprint. Max reasoning remains the default; cost savings come from smaller prompts, context
reuse, deterministic `agentctl` bookkeeping, targeted tests, and fewer child/tool calls.
