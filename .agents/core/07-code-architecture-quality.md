# Code and Architecture Quality

Prefer strong types, explicit invariants, narrow interfaces, clear ownership, deterministic
state transitions, localized side effects, repository conventions, and adapters around legacy
contracts.

Do not add dependencies, global state, abstraction layers, async runtimes, macro systems, or
frameworks without a concrete net benefit. Public API/ABI/serialization/FFI changes require caller
and compatibility inspection.

For concurrency: identify ownership, synchronization, ordering, cancellation, lifetime and
reentrancy invariants. Avoid sleep-based correctness, busy waiting, accidental duplicate work,
and locks held across uncontrolled callbacks.

For unsafe/FFI: minimize unsafe surface, document safety invariants, prevent unwinding across C ABI,
and verify ownership/lifetime/error translation.

Generated/vendor code should normally be changed through its source generator/configuration.
