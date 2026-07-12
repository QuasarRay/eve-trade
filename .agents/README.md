# EVE-TRADE sequential subagent pack

This directory is the reusable source of truth for Codex's accuracy-first workflow in EVE-TRADE.

## Why there is also `.codex/config.toml`

Codex's official project-scoped agent configuration is discovered through `.codex/config.toml` and, by default, standalone project agents under `.codex/agents/`.

This repository intentionally keeps the reusable role files in root `.agents/`. The small `.codex/config.toml` bridge registers those files and sets:

- `max_threads = 1` — only one subagent thread may be open at a time;
- `max_depth = 1` — only the root may spawn direct subagents.

Do not remove those limits unless the user explicitly decides to permit parallelism or recursive delegation.

## Normal use

After installation, new prompts should usually contain only the task-specific goal, constraints, freshness signal, and acceptance criteria.

Do not rewrite `.agents/` for every prompt.

The root `AGENTS.md` tells Codex to load the reusable orchestration and EVE-TRADE invariants automatically for nontrivial tasks.

## Role set

- `evidence-investigator.toml`: fresh bounded investigation, no implementation.
- `adversarial-verifier.toml`: independent attempt to disprove one important claim.
- `implementation-worker.toml`: one bounded writer after evidence and design are settled.
- `final-auditor.toml`: fresh final completion challenge.

The small role set is deliberate. More roles increase orchestration overhead, duplicated context, and token usage.

## Cost strategy

The pack does **not** assume sequential execution is automatically cheaper than parallel execution.

It saves credits only by:

1. refusing unnecessary delegation;
2. spawning one agent only after prior evidence is known;
3. using prior results to eliminate or narrow later work;
4. giving each agent the smallest sufficient context packet;
5. avoiding duplicate full-tree scans;
6. reusing exact-SHA evidence;
7. independently verifying only high-risk or materially uncertain claims;
8. keeping raw logs outside model context.

Accuracy has priority over cost. Cost optimization may never justify skipping evidence, weakening tests, lowering high-risk reasoning depth, or accepting an unresolved contradiction.

## Installation

Extract the archive at the repository root.

If the repository already has an `AGENTS.md` or `.codex/config.toml`, merge rather than blindly overwrite:

- preserve the mandatory sequential rules;
- preserve `[agents] max_threads = 1` and `max_depth = 1`;
- preserve all four role registrations;
- resolve conflicting project instructions explicitly.

Start a new Codex session after installation so project instructions are rediscovered.

## Updating this pack

Change `.agents/` only when a durable EVE-TRADE invariant or working method changes.

Do not encode one-off ticket details, transient SHAs, temporary failures, or a specific prompt's checklist into these files.
