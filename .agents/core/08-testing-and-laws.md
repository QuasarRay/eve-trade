# Testing, TDD, and law integrity

Tests and laws are executable contracts, not obstacles. They must predate corresponding behavioral implementation and remain frozen between baseline and GREEN.

Never weaken assertions, alter expected output, broaden assumptions to exclude a failure, downgrade a gate, replace an oracle, filter discovery, detect test names, fabricate counters, corrupt fixtures, mock away the production boundary, or add test-only production branches. If a contract is wrong, abort that cycle explicitly and restart with a new digest and preserved baseline.

Use Hypothesis for broad input/state exploration and `RuleBasedStateMachine` for temporal authority. Strategies must target malformed, boundary, aliased, repeated, reordered, conflicting, stale, and cross-boundary cases without excessive `assume()` filtering. Keep focused, standard, and stress profiles. Preserve minimized counterexamples and add deterministic `@example`/regressions when useful.

Law execution is source-authoritative during framework development and deployment-authoritative in a clean installed project. Results always go to `.aegis`. Exact method outcomes—not module presence or sibling success—bind constitutional claims. Definition digests before/after execution detect transient mutation. Capability skips remain explicit limitations.

Validation proceeds from focused reproducer through neighboring regression, standard/stress properties, full deterministic suite, behavioral/constitutional laws, adversarial mutation/fault injection, source audit, deterministic package, clean-room audit, and final workspace/governance digest comparison.
