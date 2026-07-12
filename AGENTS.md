# EVE-TRADE Codex execution policy

These instructions govern *how* Codex works in this repository. The user's current prompt governs *what* to accomplish.

## Mandatory startup

Before substantive work on any nontrivial task:

1. Read `.agents/00-ORCHESTRATION.md`.
2. Read `.agents/01-DELEGATION-GATES.md`.
3. Read `.agents/05-EVE-TRADE-INVARIANTS.md`.
4. For tasks expected to span several steps, create or update the local task state described in `.agents/04-CHECKPOINTS.md`.
5. Treat the current user prompt as task-specific scope. Do not require edits to `.agents/` for ordinary new tasks.

## Non-negotiable execution rules

- Optimize first for correctness, factual accuracy, low false negatives, low false positives, reasoning depth, and context integrity.
- Speed is the lowest priority. Never parallelize to finish sooner.
- Use explicit real Codex subagents when fresh context or independent verification materially lowers mistake risk.
- Never simulate independent review merely by changing personas inside one thread when a real independent review is required.
- At most one real subagent may be open or active at a time.
- Close the current subagent thread before spawning another.
- Never spawn multiple agents in one instruction.
- Never use `spawn_agents_on_csv`.
- Never ask for "one agent per point."
- Never request parallel agent work.
- Subagents must never spawn subagents.
- Use the custom roles registered by `.codex/config.toml`.
- Keep the root thread as coordinator, integrator, and owner of cross-cutting reasoning.
- Keep noisy exploration, logs, stack traces, and rejected hypotheses out of the root context.
- Use one writer at a time. Investigators and verifiers are read-only in intent.
- Do not spawn a subagent unless the expected accuracy gain justifies separate model/tool work.
- Cost control comes from fewer agents, narrower scopes, reuse of exact-SHA evidence, adaptive stopping, and avoiding duplicated scans—not from weaker reasoning.
- For high-risk claims, use fresh independent verification after the first investigation and after materially changing the relevant implementation.
- Never claim zero risk or perfect certainty. Pursue the lowest practical mistake rate and state residual uncertainty honestly.

## Reasoning level

The named project subagents are pinned to GPT-5.6 with `xhigh` reasoning. Do not silently replace them with a faster or cheaper model for high-risk work.

The root should be run at the highest reasoning level the user selected. When the user selects Extra High, preserve that depth. Do not trade reasoning depth for latency.

## Freshness

When a task depends on a branch, commit, CI run, deployment artifact, observability run, or current repository state:

- verify the exact current source before relying on prior conclusions;
- record SHA/ref/run provenance;
- invalidate only conclusions affected by relevant changes;
- never let old `.o11y/runs`, previous chat analysis, or stale local state prove current health.

## Completion

Do not stop because code compiles, a first test passes, or a subagent reports success.

For nontrivial changes, completion requires:

1. direct evidence for the requested behavior;
2. targeted regression tests;
3. a final diff review against the task's invariants;
4. independent verification when `.agents/01-DELEGATION-GATES.md` requires it;
5. an honest report of remaining uncertainty or blockers.
