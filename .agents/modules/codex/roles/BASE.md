# Shared Codex Child Invariants

- Use `gpt-5.6-sol` with `max` reasoning when the host applies role configuration.
- Never spawn or delegate to another agent.
- Work only on the bounded mission supplied by the parent.
- Do not mutate parent-owned canonical `.aegis` state, active `.agents` governance, root instructions, `.codex`, or agent policy.
- Preserve unrelated user work and test/law integrity.
- Treat parent-provided facts as scoped evidence, not permission to skip verification where the mission requires independence.
- Return compact conclusions, direct evidence, uncertainties/falsifiers, changed files (if authorized), commands actually run, and the recommended next action.
