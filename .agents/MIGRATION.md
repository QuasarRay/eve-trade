# Migration and manual deployment

Aegis separates trusted framework release work from ordinary governed execution. An agent may inspect or challenge active `.agents` policy, but it never installs, updates, uninstalls, scaffolds, or synchronizes that policy.

## Build and verify the deployment artifact

From the authoritative source checkout:

```text
python -B scripts/verify_release.py
python -B bin/agentctl.py --root . package build dist/aegis-governance
python -B bin/agentctl.py --root . package verify dist/aegis-governance
```

The artifact is `dist/aegis-governance/.agents/`. Building is deterministic, content-addressed, and forbidden from targeting `.agents` or `.aegis`. The release verifier builds twice, compares content digests, validates a clean deployment, runs source and clean-room suites, and checks that active governance did not change.

## Human-maintainer deployment

1. Stop ordinary Aegis tasks in the target repository.
2. Back up the target `.agents`, root `AGENTS.md`, and project-owned policy additions.
3. Review project laws, overlays, architecture, contracts, and local modules; retain only compatible strengthening.
4. Manually replace/copy the verified artifact's `.agents` directory into the target.
5. Manually merge the reviewed `bootstrap/root-AGENTS.block.md` content into the target root `AGENTS.md`; preserve unrelated instructions.
6. In the target, run deployed `doctor`, `manifest verify`, `audit`, and `law run` before starting governed work.
7. Verify live host-adapter behavior in a fresh session. Static configuration is not proof of effective model, reasoning, sandbox, depth, or concurrency.

There is no ordinary-agent maintenance flag or one-time `.agents` exception.

## Mutable runtime migration

Old releases stored task/cache data under `.agents/runtime` and durable anchors/install metadata under `.agents/persistent`. The supported migration is copy-only:

```text
python .agents/bin/agentctl.py --root . runtime migrate
python .agents/bin/agentctl.py --root . runtime migrate --apply
```

The command plans and copies recognized data into named `.aegis` locations, records content digests, uses one atomic transaction, fails on collisions or interruption, and is idempotent. It never deletes the legacy source and never mutates governing content. A human may archive legacy mutable directories only after verifying the copy and ensuring no older task still uses them.

## Project policy and modules

Project-specific governance remains valid when it strengthens Aegis. During source development, canonical built-in modules live under `modules/`; after deployment they live under `.agents/modules/`. Target-project additions live under deployed `.agents/laws/project/` or `.agents/local-modules/` and must pass schema, path, dependency, digest, secret, and constitutional compatibility checks. They cannot replace protected hard-invariant adapters or write core policy.

If existing project policy conflicts with the constitution, stop with `POLICY_CONFLICT` or `BLOCKED`. Do not rewrite active governance to make the task proceed.
