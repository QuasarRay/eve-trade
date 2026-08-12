# Deterministic time decision

## Decision

Use AnySystem logical phase ticks for deterministic controller ordering only. Do not inject application-visible time in this implementation. Treat physical wall-clock time as an observed nondeterministic input.

Pinned AnySystem revision: `74613a368c73fb12f25778ce33ca11c9a833da96`.

## `CONTROLLER_LOGICAL_TIME`

The controller increments one logical tick for every emitted action and every accepted barrier outcome. Identical scenario revision, parameters, seed, and barrier outcomes reproduce the same tick/action trace. Logical ticks never sleep and never stand in for a real witness.

## `APPLICATION_VISIBLE_TIME`

Mode: `REAL_UNINJECTED`.

Repository inspection found isolated unit-test clocks (`internal/testkit.ManualClock` and injectable rate-limiter/replay callbacks), but the real deployed path still uses Go `time.Now`, Rust `Utc::now`, and PostgreSQL `clock_timestamp()` at material trade-expiry, lease, retry, and settlement boundaries. There is no single safe observable deployment-only clock adapter spanning those components. Adding one in this task would risk divergent production semantics and would not control PostgreSQL or unrelated infrastructure clocks.

Time-sensitive business contracts therefore use authoritative database time or bounded real monotonic observations unless their execution remains unresolved. No scenario claims deterministic application time.

## `PHYSICAL_WALL_CLOCK_TIME`

Pod scheduling, network timing, database locks, process scheduling, broker scheduling, and recovery latency remain physical. Every timeout is a bounded failure guard; controller progress uses named witness/barrier outcomes rather than sleeping for an assumed duration.

## Future decision boundary

Application clock injection may be reconsidered only after a production-safe explicit clock interface exists for every affected real component, the emulation deployment makes it observable, PostgreSQL-time semantics are addressed, and future verification names can prove affected and unaffected clock domains independently.
