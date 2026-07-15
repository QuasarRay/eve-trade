# Minimal context packet template

Use this template when spawning a real subagent.

Do not fill irrelevant sections.

```text
ROLE
Use the named project role: <evidence_investigator | adversarial_verifier | implementation_worker | final_auditor>.

REPOSITORY STATE
Repository: <path/name>
Branch/ref: <branch/ref>
Exact base SHA: <sha>
Working tree: <clean | dirty>
Relevant uncommitted changes: <paths or none>

BOUNDED OBJECTIVE
Answer exactly:
<one concrete question>

TASK REQUIREMENT
<only the relevant user requirement or requirement IDs>

RELEVANT EVE-TRADE INVARIANTS
<copy only the necessary invariants from 05-EVE-TRADE-INVARIANTS.md>

KNOWN FACTS
<only facts needed to avoid redundant work>

DO NOT ASSUME
<previous hypotheses that must not be inherited, or other exclusions>

PRIMARY SOURCES TO START WITH
<paths, commands, run IDs, artifacts, docs>

SCOPE EXCLUSIONS
<adjacent work this subagent must not expand into>

REQUIRED EVIDENCE
<what would prove or disprove the question>

OUTPUT
Follow .agents/03-EVIDENCE-PACKET.md.
Do not return hidden chain-of-thought.
Return evidence, concise reasoning conclusions, uncertainty, and next recommended action.
```

## Freshness rule

For a fresh investigator:

- omit earlier diagnoses;
- include only necessary facts and invariants.

For an adversarial verifier:

- include the exact claim and evidence;
- explicitly request alternative explanations and attempted disproof.

## Context-size rule

If the packet is becoming large, stop and narrow the question.

Do not compensate for an unclear scope by sending the entire root history.
