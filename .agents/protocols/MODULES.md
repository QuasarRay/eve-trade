# Module protocol

Built-in module source lives under `modules/<id>/`; after human deployment it lives under `.agents/modules/<id>/`. Target-project strengthening may live under deployed `.agents/local-modules/<id>/`. Ordinary operational CLI exposes module discovery/show/verify only; it does not install, uninstall, or scaffold governance.

Discovery validates IDs, directory identity, manifests, kinds, versions, framework constraints, dependency graphs, policy paths, action argv, declared writes, symlink boundaries, and duplicate/replacement rules. A local replacement must explicitly replace the same built-in ID and cannot replace protected hard-invariant adapters.

Verification actions execute in a disposable copied workspace with direct argv, minimal environment, bounded output/time, and before/after snapshots. A read-only verification that mutates its fixture fails. Apply-capable legacy APIs are confined to explicit declared non-governance destinations and transactionally reject core policy, `.agents`, root instructions, tests/laws, undeclared files, and symlink topology changes.

Modules may strengthen host detection, policy, configuration, roles, compatibility, and verification. They cannot lower Max reasoning, widen child concurrency/depth, disable TDD/evidence/review/falsification, or grant governance write authority. Optional packages never become dependencies of the stdlib-only recovery core.
