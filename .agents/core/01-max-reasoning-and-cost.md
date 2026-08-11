# Max Reasoning and Cost Discipline

## Default: Max

When a host exposes reasoning effort, request the maximum ordinary reasoning setting for the
parent and every child. For GPT-5.6 this means `max`. Do not automatically reduce effort for
mechanical work; instead avoid invoking a model at all when a deterministic local tool can do
the already-decided operation safely.

If Max is unavailable, do not silently claim equivalence. Record the effective limitation.
Continue only under higher-priority instruction or when the task can still be completed without
misrepresenting the requested guarantee.

## Cost is reduced structurally

Prefer, in order:

- reason once from sufficient evidence;
- reuse verified conclusions while fresh;
- target symbols/ranges instead of entire repositories;
- use deterministic programs for bookkeeping, hashing, filtering, and repeated checks;
- keep prompts/briefs lean and deduplicated;
- spawn fewer children, sequentially;
- cache source/context fingerprints;
- run the cheapest falsifying test before broad suites;
- avoid speculative edits and retries;
- compact completed phases into canonical state.

Do **not** save credits by lowering reasoning effort, skipping necessary evidence, weakening
verification, or delegating to a weaker model when the framework/host adapter promises Max.

## Reasoning quality

Before a consequential action establish: invariant, evidence, uncertainty, cheapest
falsifier, blast radius, rollback, and final proof. Keep private chain-of-thought private;
record only decisions, evidence, assumptions, confidence, and falsifiers.
