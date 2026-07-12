# Subagent evidence packet contract

Every real subagent must return a compact result in this shape.

```text
## Scope

## Repository state inspected
- branch/ref:
- exact SHA:
- dirty-tree details, if relevant:

## Evidence inspected
- files:
- commands/tests:
- CI/deployment/o11y artifacts:
- external primary docs, if any:

## Result
One of:
- CONFIRMED
- PARTIALLY_CONFIRMED
- DISPROVEN
- NO_DEFECT_FOUND_IN_SCOPE
- INSUFFICIENT_EVIDENCE

## Findings
For each finding:
- stable short ID;
- observation;
- evidence;
- reproduction;
- supported causal claim;
- alternative explanations considered;
- severity/risk;
- confidence class;
- exact affected locations.

## Disproved hypotheses

## Unresolved uncertainty

## Recommended next action

## Evidence the root must reopen directly
```

## Confidence classes

### OBSERVED

A symptom or suspicious condition exists.

### REPRODUCED

The condition reproduces against the stated exact tree.

### CAUSALLY_SUPPORTED

Evidence supports the causal explanation, but plausible alternatives may remain.

### CONFIRMED

The claim has decisive evidence appropriate to its severity or survives required independent validation.

### DISPROVEN

Evidence contradicts the claim.

### INSUFFICIENT_EVIDENCE

Available evidence cannot justify a stronger claim.

Never promote a failed job, error log, or suspicious code pattern directly to `CONFIRMED` root cause.

## Output economy

Do not return:

- hidden chain-of-thought;
- huge narratives;
- entire command logs;
- entire source files;
- repeated architecture summaries.

Store large evidence outside model context when possible and return:

- path;
- digest when useful;
- exact relevant range;
- concise interpretation.
