# Delegation and independent-verification gates

## Do not delegate by default

The following normally stay in the root thread:

- trivial formatting;
- deterministic renames;
- one-line mechanical edits;
- already-proven fixes with obvious local tests;
- synthesis across several validated findings;
- final architecture decisions.

A real subagent is not a reward for task size.

It is an accuracy tool with a real token cost.

## Spawn `evidence_investigator` when

At least one condition holds:

- the codebase area is unfamiliar or broad enough to pollute the root context;
- primary evidence is incomplete;
- several plausible causes exist;
- a fresh context can reduce anchoring;
- the task crosses a correctness/security boundary;
- the user explicitly asks for exhaustive audit behavior;
- a high-risk area reports no defect and a targeted negative search is warranted.

For a nontrivial multi-step task, one initial bounded investigator is usually appropriate.

Do not spawn multiple investigators over overlapping scope.

## Spawn `adversarial_verifier` when

At least one condition holds:

- a P0/critical/high-severity defect is claimed;
- authorization, authentication, cryptography, settlement correctness, idempotency, replay, concurrency, durability, or production health is involved;
- observability evidence is being used to claim root cause;
- CI/E2E may be false-green;
- a finding would cause a major architectural rewrite;
- a false positive would be expensive;
- a false negative would be dangerous;
- a material fix has been implemented in a high-risk area;
- the first investigator reports "no flaw" in a high-risk area and the root identifies a specific assumption worth attacking.

The verifier must be a fresh real subagent.

Persona switching inside the original thread is not sufficient independent verification.

## Spawn `implementation_worker` when

All of the following hold:

1. the defect and required invariant are evidence-backed;
2. the root has decided the design;
3. the write scope is bounded;
4. only one writer will exist;
5. delegation is likely to reduce context pollution or improve implementation focus.

Do not delegate implementation merely to increase agent count.

For tightly coupled cross-cutting refactors, the root may be the better writer.

## Spawn `final_auditor` when

Required for:

- nontrivial high-risk changes;
- large multi-stage hardening tasks;
- repository-wide audits followed by implementation;
- tasks with many mandatory requirements;
- any completion claim where omission risk is significant.

Usually unnecessary for a trivial local edit.

The final auditor must inspect the final exact tree and must not receive the implementation narrative or prior confidence claims.

## Risk classification

### Low risk

Examples:

- typo;
- comment correction;
- obvious local refactor with unchanged behavior.

Default:

```text
root only
```

### Medium risk

Examples:

- bounded feature;
- ordinary bug;
- non-security refactor.

Default:

```text
one investigator if needed
→ root/one writer
→ tests
```

Add a verifier only when evidence is ambiguous or the change becomes cross-cutting.

### High risk

Includes:

- actor authority;
- authentication/HMAC;
- cryptographic canonicalization;
- replay/idempotency;
- transaction semantics;
- concurrency;
- async durability/outbox;
- crash recovery;
- schema ownership;
- production readiness;
- UDP path correctness;
- artifact provenance;
- CI/E2E execution truth;
- observability causality/provenance;
- secret handling;
- architecture-boundary enforcement.

Default:

```text
fresh investigator
→ root evidence review
→ implementation
→ fresh adversarial verifier
```

## Cost-control gates

Before an independent verifier, ask:

- Is the claim actually high risk?
- Is there already independent external execution evidence?
- Did the relevant source change after that evidence?
- Can one targeted verification question replace a broad second audit?

Prefer:

```text
verify this exact claim against these exact invariants
```

over:

```text
audit the entire repository again
```

Never duplicate full scans without a concrete reason.
