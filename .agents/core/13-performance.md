# Performance and Resource Integrity

Correctness and compatibility precede optimization. Measure representative baseline before claiming
improvement. Compare equivalent build modes/workloads and record variance where meaningful.

Never improve benchmark numbers by removing required work, changing semantics, hiding valid slow
cases, or weakening correctness checks.

Use focused profiling before repeated broad benchmark suites. Watch asymptotic scans, repeated
serialization/parsing, unnecessary clones, unbounded caches/queues, lock contention, wakeup storms,
and repeated network/filesystem reads.
