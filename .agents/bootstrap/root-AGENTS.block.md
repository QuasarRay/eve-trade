# Aegis Agent Bootstrap

This repository uses the project-independent Aegis framework under `.agents/`.

## Hard defaults

- Use the named **Max reasoning profile** by default for parent and subagents whenever the active host/model
  supports it. Do not silently downgrade it to save credits and do not silently substitute a different
  experimental tier. If exact Max is unavailable, use the strongest supported fallback and explicitly record
  the limitation when material.
- Optimize cost by reducing unnecessary agent turns, redundant context/network reads, repeated tests,
  speculative edits and rework—not by reducing reasoning depth.
- At most **one subagent may be active at a time**. No fan-out, no parallel agents, no nested delegation.
  Finish, verify, integrate and close one child's lease before creating another.
- Give each child a fresh bounded context. Do not dump the entire parent transcript/repository into it.
- Preserve user work. Never use broad destructive Git/workspace operations without explicit authorization.
- Do not modify, disable, special-case, or evade tests/laws to manufacture success. Production code must pass
  through the real behavior path.
- Do not claim a command/test/inspection occurred unless it actually did. Important final claims require
  direct evidence or must be labeled inference/unverified.
- Do not reread/re-fetch unchanged context merely because a phase or child changed. Use the context ledger,
  local search and compact established facts.
- Parent owns canonical task state and final claims. Child reports are evidence/proposals, not proof.

For substantial work, read `.agents/INDEX.md` and only the routed policies relevant to the task. Project-local
instructions discovered through the host's normal instruction mechanism still apply according to that host's
native precedence/scoping rules. Optional agent/environment modules may strengthen these rules but may not
weaken the hard defaults above.
