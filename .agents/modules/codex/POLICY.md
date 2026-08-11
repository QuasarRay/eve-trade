# Codex Adapter Policy

When this adapter is installed, the intended managed configuration is:

- parent model: `gpt-5.6-sol`;
- parent reasoning: `max`;
- default spawned model: `gpt-5.6-sol`;
- default spawned reasoning: `max`;
- V2 total-thread capacity: `2` (root + one child);
- V1 spawn depth: `1`; V1 concurrency is additionally governed by the Aegis logical lease/no-delegation policy because current Codex V2 and V1 limits are not safely expressible together in one portable config;
- children still receive an explicit no-delegation instruction because host/version behavior can differ.

Registered `aegis_*` role profiles provide bounded read-only/write responsibilities and pin Sol/Max again
inside the profile. Ordinary Aegis execution exposes no Codex install/uninstall command and never modifies
`.agents`. Legacy configuration helpers are for trusted, out-of-band human maintenance analysis only; they
conservatively merge managed keys, reject conflicts/unmanaged collisions, and use transactional recovery.

Static configuration is not proof of effective runtime behavior. Codex multi-agent behavior evolves and
some released clients have had role/model-profile application bugs. On a machine with Codex installed,
verify the effective parent/child model, effort, role instructions, sandbox and sequential behavior using
the client's status/debug metadata when available. Fail explicitly rather than silently assuming a role
profile was honored.
