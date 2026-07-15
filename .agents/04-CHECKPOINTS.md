# Durable checkpoint and resume protocol

Use for multi-step tasks, long audits, and work likely to survive compaction or session resumption.

## Local state directory

Create:

```text
.codex-task-state/
```

Prefer excluding it locally with `.git/info/exclude` unless the user explicitly wants task state committed.

Maintain:

```text
.codex-task-state/
├── provenance.md
├── scope.md
├── coverage.md
├── findings.jsonl
├── rejected-hypotheses.md
├── contradictions.md
├── decisions.md
├── implementation-status.md
├── verification-status.md
└── next-step.md
```

## What to record

### provenance.md

- repository;
- branch/ref;
- remote HEAD when relevant;
- working/base SHA;
- dirty-tree state;
- inspection timestamps;
- CI run IDs/attempts and artifact provenance when relevant.

### coverage.md

For each mandatory task requirement:

- not started;
- investigating;
- defect confirmed;
- already correct with evidence;
- implementing;
- implemented;
- independently verified;
- blocked.

### findings.jsonl

One record per finding:

- finding ID;
- task requirement;
- exact source state;
- subsystem;
- observation;
- evidence;
- causal status;
- severity;
- investigator role;
- independent verifier when required;
- implementation status;
- verification status.

### rejected-hypotheses.md

Preserve disproven theories to prevent repeated investigation.

### contradictions.md

Record unresolved disagreements between:

- source;
- tests;
- CI;
- manifests;
- docs;
- telemetry;
- agent conclusions.

### next-step.md

Exactly one current next action and why it is the highest-value next step.

## Stage boundary

Before moving to the next stage:

1. record exact current source state;
2. update coverage;
3. update findings;
4. record rejected hypotheses;
5. record contradictions;
6. record reusable commands/artifacts;
7. record the next exact action.

## Resume

On resume:

1. re-check source freshness;
2. read provenance;
3. read coverage;
4. read contradictions;
5. read next-step;
6. reopen decisive evidence for the next step;
7. continue without restarting the whole audit.

## Diff-based invalidation

If relevant source changed:

- invalidate only affected findings and verification.

If relevant source did not change:

- preserve exact-SHA evidence where still applicable.

Do not re-run expensive work merely because a session resumed.
