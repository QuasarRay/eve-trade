# Codex infrastructure module

This adapter translates host-independent Aegis invariants into Codex configuration and bounded custom roles. It is optional; the stdlib-only core remains usable without Codex.

Ordinary Aegis CLI exposes module discovery and read-only verification only. It does not install/uninstall Codex configuration or mutate `.agents`, root instructions, or agent policy. A human maintainer who chooses to configure Codex must do so outside an ordinary governed task, after reviewing the generated configuration and preserving unrelated `.codex` content.

Static verification checks source schema, managed values, roles, Max reasoning, one-child/no-nesting policy, and wrapper/config syntax. It cannot prove effective live parent/child model, reasoning effort, selected role, sandbox, approval behavior, depth, or concurrency. Observe those through host status/debug metadata in a fresh session where available; otherwise record `UNAVAILABLE`, never PASS.

Codex backends count concurrency differently. Aegis does not force-enable a backend. The managed V2 capacity is two total threads (root plus one child); V1 depth is one; the cross-backend `.aegis/leases` contract and explicit child no-delegation instruction remain authoritative when host enforcement is incomplete.

Legacy `install.py`/`uninstall.py` exist for compatibility and fixture-based recovery tests. Their durable metadata uses `.aegis/state/install-state`, and active governance/root-instruction mutation guards remain in force.
