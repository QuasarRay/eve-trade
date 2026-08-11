# Evidence and Claims

Important claims should map to one or more of:

- source path + symbol/range/digest;
- exact command + exit/result;
- named test/law + outcome;
- observed runtime behavior;
- versioned authoritative source;
- explicit inference with confidence and falsifier.

Prefer direct evidence over agent summaries. Prefer runtime/test evidence over comments when they
conflict. Prefer primary/versioned documentation for current external facts.

Evidence should be immutable append-only records where practical. Never mutate an old failed
result into a pass; add a new evidence record.

Acceptance gates describe what must be true. Evidence proves or disproves them. A gate may be
WAIVED only by an explicit reason consistent with higher-priority instructions; waiver is not a
pass.
