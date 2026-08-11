# Behavioral, constitutional, and TDD law protocol

A law states an implementation-independent invariant, its observation boundary, falsifier, severity, capability, and executable oracle. Presence of a name or file is never proof.

## Source and deployment layouts

In an authoritative checkout, laws/tests resolve from root `infra/`, `laws/`, and `tests-to-impl/`. In a clean deployment they resolve from `.agents/`. Results always write to `.aegis/law-results/`. Source execution never synchronizes or reads a stale active mirror when a complete source layout exists.

Framework acceptance consists of an exact 834-name historical registry plus an exact 105-name constitutional/TDD catalog. Reviewed properties may subsume multiple names, but every mapping names real source symbols and exact runtime observations. Hypothesis state-machine symbols bind to their generated `runTest` record. A passing sibling cannot conceal a skipped/failed mapped method.

## Lifecycle truth

Each law is collected, started, completed, capability-classified, oracle-counted, definition-bound, and evidence-sealed. PASS requires at least one executed oracle, AVAILABLE capability, unchanged definition, and successful production-boundary observation. Missing, incomplete, zero-oracle, expected-failure, unexpected-success, integrity-error, or invalid ledger states fail.

Unavailable host behavior is `UNAVAILABLE`, `BLOCKED`, or `UNTESTED`, never PASS. Foreign platforms and unavailable optional adapters remain explicit. `PASS_WITH_EXPLICIT_CAPABILITY_LIMITATIONS` is accepted only when the report is internally consistent and every non-PASS law carries concrete capability evidence.

Law/test files and references are frozen during execution. The runner verifies content before/after and detects transient rewrite/restore. Production may not inspect law IDs, test names, call stacks, fixture markers, or harness environment. Command laws use direct argv, minimal environment, bounded capture, timeout/exit evidence, and real production paths.

Machine traceability is deterministic and exact; a final ledger promotes entries only to verified, implemented/proven, stronger-equivalent/proven, proven external limitation, or justified not-applicable states. No unexplained requirement is permitted.
