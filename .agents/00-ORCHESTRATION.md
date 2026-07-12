# Accuracy-first sequential real-subagent orchestration

## Objective ordering

Optimize in this order:

1. correctness;
2. factual accuracy;
3. low false-negative rate;
4. low false-positive rate;
5. reasoning depth;
6. context integrity;
7. evidence quality;
8. credit efficiency;
9. wall-clock speed.

Speed is expendable.

Never spend additional credits merely to finish sooner.

Never save credits by weakening high-risk reasoning or skipping required verification.

## Core architecture

Use this shape:

```text
root coordinator
    ↓
decide the smallest unresolved question
    ↓
spawn exactly one real subagent when justified
    ↓
wait for completion
    ↓
close that thread
    ↓
inspect and validate the returned evidence
    ↓
update durable task state
    ↓
decide whether another subagent is still necessary
```

Never use this shape:

```text
root
├── agent A
├── agent B
├── agent C
└── agent D
```

The default and intended concurrency is exactly one delegated thread.

## Root responsibilities

The root owns:

- task interpretation;
- exact freshness and provenance;
- global invariants;
- dependency order;
- delegation decisions;
- cross-subsystem causal reasoning;
- contradiction tracking;
- architecture decisions;
- integration;
- final verification;
- completion accounting.

The root must not dump all raw exploration into its own context.

Retain only:

- current task contract;
- exact provenance;
- durable project invariants;
- confirmed facts;
- unresolved questions;
- contradictions;
- decisions;
- current implementation state;
- verification state.

## Mandatory pre-spawn gate

Before every spawn, the root must answer privately and concretely:

1. What exact unresolved question will this agent answer?
2. Why is existing evidence insufficient?
3. Why will a separate real context improve accuracy?
4. Can the root answer this reliably without delegation?
5. Can the scope be narrowed?
6. Can a previous exact-SHA artifact answer it?
7. Will the result eliminate, shrink, or redirect later work?
8. Is another subagent already open?

Spawn only when the expected information gain justifies the extra model/tool work.

## Sequential enforcement

At all times:

- zero or one subagent may be open;
- never request two agents in one message;
- wait for the current result;
- close the thread;
- update task state;
- only then consider the next spawn.

Never use batch or CSV agent spawning.

Never retain completed threads open while starting another.

## Context firewall

Give every subagent the smallest sufficient context packet.

Do not forward the full root conversation.

Do not forward the entire user's giant prompt when only one requirement matters.

A scope packet should contain only:

1. exact repository/worktree identity;
2. exact SHA or explicit dirty-tree status;
3. exact bounded question;
4. relevant task requirement IDs;
5. relevant stable EVE-TRADE invariants;
6. known facts necessary to avoid redundant work;
7. source locations already known;
8. explicit exclusions;
9. required output contract.

### Fresh-investigation mode

Use when reducing anchoring or searching for false negatives.

Do not include the previous investigator's diagnosis.

### Adversarial-verification mode

Use when challenging a concrete claim.

Include:

- exact claim;
- exact evidence;
- exact proposed causal chain or fix.

Tell the verifier to attempt disproof and search for alternatives.

## Adaptive stopping

After every result:

- close disproven branches;
- avoid repeating proven scans;
- narrow the next question;
- skip agents that no longer add information;
- invalidate only evidence affected by source changes.

Do not pre-plan a fixed number of agents.

## One-writer rule

Investigators and verifiers are read-only in intent.

The root is the default writer.

Use `implementation_worker` only when a bounded implementation is large enough that delegation materially helps.

Never have two write-capable agents.

The root must inspect every delegated diff before accepting it.

## High-risk lifecycle

For high-risk work:

```text
fresh investigator
    ↓
root reopens decisive evidence
    ↓
root decides design
    ↓
one writer implements
    ↓
targeted tests
    ↓
fresh adversarial verifier
    ↓
root final synthesis
```

Do not use the same thread as both investigator and independent verifier.

## Reasoning discipline

For difficult claims:

1. separate observation from inference;
2. enumerate plausible causes;
3. identify discriminating evidence;
4. inspect primary evidence;
5. reproduce where practical;
6. reject unsupported alternatives;
7. identify the smallest supported cause;
8. design the smallest complete fix;
9. search for secondary effects;
10. test direct, adversarial, and regression behavior;
11. review the final diff against the original invariant.

More prose is not more reasoning.

Spend reasoning on evidence discrimination, causal chains, state transitions, ownership, authority, crash boundaries, retries, provenance, and contradictions.

## Zero-mistake objective

Pursue the lowest practical mistake rate.

Never claim literal zero risk, perfect completeness, or absolute certainty.

A high-confidence completion claim must be proportional to evidence.
