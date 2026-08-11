# AGENTS.md

## Purpose

This file defines the project-specific operating rules for AI agents working on **eve-trade**.

Reusable agent infrastructure belongs in `.agents/`.

This file must remain focused on:

* eve-trade architecture
* repository-specific constraints
* development priorities
* testing expectations
* acceptable implementation behavior
* boundaries agents must not cross

Do not duplicate the generic agent framework here.

---

# 1. Primary Objective

Develop eve-trade into a reliable, maintainable, production-oriented distributed trading system while prioritizing **implementation progress over premature perfection**.

The preferred development order is:

1. Build the difficult architecture and substantial implementation.
2. Establish the infrastructure required to test important behaviors.
3. Add tests capable of creating the prerequisites and failure conditions they require.
4. Analyze correctness and reliability.
5. Harden behavior iteratively.

Agents must not stall large implementation work by attempting to prove every possible correctness property before the necessary system exists.

---

# 2. Architecture

The system is approximately:

```text
Client / GUI
    |
    v
Quilkin
    |
    v
API Gateway
    |
    v
Market Service
    |
    v
Settlement Service
    |
    v
PostgreSQL
```

Primary technologies include:

```text
API Gateway       Go
Market Service    Go
Settlement        Rust
Database          PostgreSQL
Transport/APIs    gRPC / Protobuf where applicable
Proxying           Quilkin
CI/CD              GitHub Actions + Dagger
Testing            pytest + Hypothesis + service-native tests
Chaos testing      Litmus
Observability      OpenTelemetry-compatible infrastructure
```

Agents must inspect the repository before assuming this description is exhaustive or completely current.

The repository itself is the final source of truth.

---

# 3. Project Priorities

When priorities conflict, prefer them approximately in this order:

1. Preserve architectural direction.
2. Complete difficult or high-volume implementation.
3. Avoid destructive or irreversible changes.
4. Maintain component boundaries.
5. Keep build and development workflows usable.
6. Build test infrastructure capable of reproducing required states.
7. Add meaningful behavioral tests.
8. Improve correctness.
9. Improve reliability and failure handling.
10. Optimize implementation details.
11. Improve stylistic cleanliness.

Do not sacrifice substantial development progress merely to produce an aesthetically perfect CI pipeline or theoretically complete test suite.

---

# 4. Development Philosophy

## 4.1 Build before over-hardening

Agents should distinguish between:

* architectural defects that block further development
* implementation defects that prevent the system from functioning
* correctness gaps that can be investigated later
* reliability improvements
* optional hardening
* aesthetic or theoretical improvements

Only the first two categories should normally interrupt major implementation work.

A possible future false-green or false-red path is not automatically a reason to stop development.

Treat such issues according to their actual risk.

---

## 4.2 Prefer enabling infrastructure

When a test cannot currently establish its prerequisites, prefer building the infrastructure that allows the prerequisite to be created.

Example:

```text
Bad approach:

Test assumes a network partition can somehow occur.
Test cannot create one.
Agent spends large effort approximating the condition.

Preferred approach:

Hypothesis generates the scenario.
Dagger constructs the environment.
Litmus creates the network partition.
The test observes the resulting behavior.
```

Testing infrastructure should increasingly allow tests to control reality rather than merely infer it.

---

# 5. Testing Strategy

Testing should favor **behavioral evidence** over superficial test count.

Use the appropriate testing layer.

```text
Pure logic
    -> unit tests / property tests

Data invariants
    -> Hypothesis property tests

Service contracts
    -> integration tests

Cross-service behavior
    -> end-to-end tests

Infrastructure failures
    -> Dagger + Litmus

Distributed-system assumptions
    -> controlled failure injection

Regression
    -> smallest test that permanently reproduces the defect
```

---

# 6. Hypothesis

Use Hypothesis where the tested behavior has meaningful input or state spaces.

Prefer properties such as:

```text
idempotency
ordering
serialization round-trips
state-machine invariants
duplicate handling
retry behavior
transaction invariants
boundary behavior
protocol compatibility
failure recovery
```

Avoid replacing meaningful scenario tests with arbitrary generated input merely to claim property-based testing.

Property tests should describe actual invariants.

Test names should communicate the property being proven without requiring explanatory comments.

Prefer:

```python
test_duplicate_settlement_requests_do_not_create_duplicate_transfers()
```

over:

```python
test_settlement_3()
```

---

# 7. Litmus

Litmus is the preferred mechanism for deliberately constructing infrastructure failure conditions where appropriate.

Examples include:

```text
network delay
packet loss
network partitions
pod termination
service unavailability
resource pressure
dependency disruption
DNS disruption
```

Litmus experiments should cooperate with the testing layer rather than operate as disconnected demonstrations.

The intended model is:

```text
Hypothesis
    |
    | generates scenario/state
    v
Dagger
    |
    | constructs controlled environment
    v
Litmus
    |
    | creates required failure condition
    v
System under test
    |
    v
Assertions / observations
```

---

# 8. Dagger

Dagger is the preferred orchestration layer for CI/CD behavior that can reasonably live outside GitHub Actions.

Keep Dagger programs:

* modular
* independently understandable
* composable
* locally executable where practical
* separated by responsibility

Prefer structures resembling:

```text
ci/
    build.py
    unit.py
    integration.py
    e2e.py
    chaos.py
    security.py
    publish.py
```

Exact organization may differ where the repository already establishes another convention.

Do not create a single enormous Dagger script when responsibilities can be separated cleanly.

---

# 9. GitHub Actions

GitHub Actions should be a thin execution layer.

Prefer:

```text
GitHub Actions
    |
    v
invoke Dagger program
    |
    v
Dagger performs workflow
```

Avoid embedding large amounts of build, test, deployment, orchestration, or validation logic directly in workflow YAML.

GitHub Actions may still perform unavoidable platform-level operations such as:

* repository checkout
* authentication setup where platform integration requires it
* selecting the Dagger entry point
* minimal environment bootstrap

Business logic and substantial CI/CD logic belong outside the workflow YAML.

---

# 10. CI/CD Priorities

Do not optimize the CI/CD system for theoretical flawlessness at the cost of development velocity.

During active architecture development:

```text
required:
- builds can execute
- important tests can execute
- failures are observable
- artifacts are not silently corrupted
- secrets are not exposed
- publishing cannot obviously publish unintended artifacts

important but deferrable:
- exhaustive false-green elimination
- exhaustive false-red elimination
- perfect evidence provenance
- every possible adversarial CI state
- maximal workflow hardening
```

Security-critical credential leakage or destructive publishing behavior remains high priority.

---

# 11. Reliability

For this project, reliability should primarily mean:

> The system continues providing useful behavior despite faults, partial failures, and component defects.

Reliability work should therefore focus heavily on:

* failure containment
* graceful degradation
* retry behavior
* idempotency
* recovery
* isolation
* redundancy where justified
* durable state
* observability
* restart behavior

A system with zero known bugs but catastrophic failure propagation is not considered highly reliable.

---

# 12. Distributed-System Rules

Agents must take distributed-system failure modes seriously.

Do not casually assume:

```text
exactly-once delivery
reliable networks
ordered delivery
instantaneous clocks
globally consistent state
successful retries
process survival
database availability
atomic cross-service operations
```

Where correctness depends on one of these assumptions, make the assumption explicit in code, tests, documentation, or architecture.

Prefer designs tolerant of:

```text
duplicate requests
timeouts
partial failures
message reordering
restarts
stale observations
retry storms
dependency loss
```

---

# 13. Idempotency

Financial or settlement-like operations require particular care.

Repeated execution of the same logical request must not silently produce multiple financial effects unless explicitly intended.

Where applicable, inspect:

* idempotency keys
* unique constraints
* transaction boundaries
* retry paths
* replay behavior
* timeout ambiguity
* failure between persistence and acknowledgement

Do not implement "retry" without considering duplicate execution.

---

# 14. Database Changes

Database modifications must consider:

```text
schema compatibility
migration order
existing data
rollback behavior
transaction boundaries
unique constraints
concurrency
locking
indexes
failure during migration
```

Do not casually modify persistent schemas merely to simplify application code.

Prefer backward-compatible migration sequences when feasible.

---

# 15. Go Code

For Go services:

* keep package boundaries meaningful
* propagate cancellation through `context.Context`
* do not silently discard errors
* avoid unnecessary global mutable state
* keep transport logic separated from domain behavior where practical
* preserve protocol compatibility
* use concurrency deliberately rather than automatically

Do not introduce goroutines without understanding ownership, lifecycle, and shutdown behavior.

---

# 16. Rust Code

For Rust components:

* prefer explicit ownership
* avoid unnecessary cloning to bypass design problems
* avoid `unsafe` unless justified by a concrete requirement
* treat panic behavior in service paths carefully
* model important states with types when practical
* maintain clear async ownership and cancellation semantics

Do not transform understandable Rust into excessively abstract type machinery merely for elegance.

---

# 17. APIs and Protocols

Changes to externally visible APIs require more scrutiny than internal refactoring.

Before modifying:

```text
protobuf definitions
request formats
response formats
event schemas
database contracts used across services
network protocols
CLI interfaces used by automation
```

inspect all known consumers.

Prefer additive compatibility where feasible.

---

# 18. Observability

Observability must assist debugging rather than merely generate telemetry.

Where appropriate, preserve correlation across:

```text
gateway request
market operation
settlement request
database action
retry
failure
```

Logs should contain useful context without leaking secrets.

Metrics should answer operational questions.

Traces should help locate distributed latency and failure boundaries.

Do not add telemetry purely to increase instrumentation count.

---

# 19. Secrets

Never commit:

```text
API keys
registry credentials
tokens
passwords
private keys
cloud credentials
database secrets
```

Do not expose secrets through:

```text
logs
test output
build artifacts
telemetry
container layers
generated source files
```

Credential use should be scoped to the smallest step that requires it.

---

# 20. Generated Files

Before manually editing generated files, identify their generator.

Examples may include:

```text
protobuf output
OpenAPI output
generated clients
generated configuration
build artifacts
coverage output
compiled assets
```

Modify the source definition or generator whenever possible.

Do not hand-maintain generated output unless the repository explicitly requires it.

---

# 21. Refactoring

Refactor when it supports a concrete objective.

Good reasons include:

```text
required architecture change
eliminating duplication blocking development
making behavior testable
separating orchestration concerns
removing dangerous coupling
enabling a new feature
```

Bad reasons include:

```text
personal stylistic preference
theoretical purity
rewriting functioning code without measurable benefit
introducing abstractions for hypothetical future requirements
```

Large refactors must preserve behavior unless behavior change is intentional.

---

# 22. Scope Discipline

Do not expand the task unnecessarily.

When asked to implement feature X:

```text
implement X
test X
fix defects directly preventing X
document important implications
```

Do not automatically redesign unrelated subsystems.

Record unrelated flaws rather than opportunistically rewriting the repository.

---

# 23. Existing Tests

Do not modify tests merely to make implementation failures disappear.

A failing test may be changed when:

* the requirement changed
* the test itself is incorrect
* the test encoded obsolete architecture
* the test is nondeterministic for an identified reason
* a stronger test replaces it

When changing a test because it is wrong, make the reason clear in the change.

Never weaken assertions solely to obtain green CI.

---

# 24. Tests During Rapid Development

Not every conceivable test must exist immediately.

Prioritize tests for:

1. catastrophic data corruption
2. duplicate financial effects
3. destructive operations
4. architecture-critical contracts
5. difficult distributed behavior
6. major regressions
7. high-value properties
8. ordinary edge cases
9. exhaustive hardening

Thousands of lines of difficult implementation may legitimately precede exhaustive correctness analysis when doing so makes the system testable in the first place.

---

# 25. False Greens and False Reds

False-green and false-red analysis is valuable, but its priority depends on development stage.

Do not treat every theoretical false-green path as release-blocking during early infrastructure construction.

Immediately prioritize a false green when it could conceal:

```text
credential exposure
data corruption
incorrect publishing
destructive deployment
major financial duplication
security boundary violation
fundamentally broken architecture
```

Otherwise, document it and address it according to project priorities.

---

# 26. Verification

Before declaring substantial work complete, perform the strongest practical verification available.

This may include:

```text
formatting
static analysis
compilation
unit tests
property tests
integration tests
Dagger pipelines
Litmus experiments
service startup
protocol compatibility
repository-wide tests
```

Do not claim verification that was not executed.

Clearly distinguish:

```text
implemented
compiled
tested
integration-tested
chaos-tested
reviewed
inferred
not verified
```

---

# 27. Failure Handling

If verification fails:

1. identify whether the failure is caused by the change
2. inspect the underlying error
3. fix defects within task scope
4. rerun the relevant test
5. avoid hiding the failure

Do not replace failed execution with speculative reasoning when execution is available.

---

# 28. Dependencies

Adding dependencies is acceptable when they materially reduce complexity or provide well-established functionality.

Before adding one, consider:

```text
maintenance
security
runtime cost
build cost
ecosystem maturity
license
whether the dependency replaces substantial custom code
```

Do not reimplement mature infrastructure merely to minimize dependency count.

Likewise, do not introduce frameworks for trivial tasks.

---

# 29. Performance

Do not optimize blindly.

Prioritize:

```text
measured bottlenecks
algorithmic problems
network round trips
database access
serialization overhead
contention
unnecessary copies
avoidable allocations
```

Performance changes should preserve correctness unless the tradeoff is intentional and documented.

---

# 30. Documentation

Document decisions that are difficult to infer from code.

Especially document:

```text
distributed-system assumptions
failure semantics
idempotency guarantees
cross-service contracts
unusual architectural constraints
CI/CD architecture
chaos-test prerequisites
intentional compromises
```

Avoid commentary that merely restates syntax.

---

# 31. Repository Hygiene

Do not commit temporary artifacts unless intentional.

Examples:

```text
coverage output
debug logs
temporary databases
editor files
local credentials
build directories
temporary generated files
agent scratch state
```

Respect `.gitignore`.

---

# 32. Agent Infrastructure Boundary

`.agents/` contains reusable agent infrastructure.

Project work must not casually alter it.

Unless the task explicitly concerns the agent framework:

```text
DO NOT modify .agents/
DO NOT rewrite its policies
DO NOT delete it
DO NOT move it
DO NOT duplicate it
```

Project-specific instructions belong in this `AGENTS.md` or appropriate project documentation.

---

# 33. Agent Scratch State

Project-specific temporary agent state must not become part of product architecture.

Directories such as:

```text
.codex/
.codex-task-state/
temporary agent worktrees
temporary reports
scratch outputs
```

should be treated as disposable unless the repository explicitly adopts them.

Do not make production behavior depend on agent-local state.

---

# 34. Autogenerated Project State

Directories containing autogenerated test, experiment, or agent records must not be treated as hand-maintained source code unless explicitly documented.

Before copying infrastructure from another repository, remove project-specific state that does not belong to eve-trade.

Reusable mechanism and project-generated history are separate concepts.

---

# 35. Working Procedure

For substantial tasks, agents should generally follow:

```text
1. Inspect
2. Understand architecture
3. Identify task boundary
4. Locate relevant tests
5. Implement
6. Add or update tests where useful
7. Execute verification
8. Fix directly relevant failures
9. Report remaining limitations precisely
```

Do not spend excessive time constructing an enormous preliminary audit when repository inspection is already sufficient to begin implementation.

---

# 36. Large Tasks

For large implementation requests, prefer making concrete progress over repeatedly stopping for clarification.

When reasonable assumptions can be inferred from:

* repository architecture
* existing code
* tests
* documentation
* this file

make those assumptions and proceed.

Record important assumptions in the final report.

---

# 37. Definition of Done

A task is complete when the requested behavior exists and the strongest practical relevant verification has been performed.

Completion does **not** require proving that the entire repository is flawless.

For development tasks, a useful hierarchy is:

```text
Implemented
    ↓
Builds
    ↓
Relevant tests pass
    ↓
Integration verified
    ↓
Failure behavior tested
    ↓
Broader correctness analysis
    ↓
Hardening
```

Do not unnecessarily block an earlier stage on exhaustive completion of every later stage.

---

# 38. Guiding Principle

The project should steadily move toward:

> a distributed system whose difficult behavior can be deliberately constructed, observed, tested, and improved.

Build the machinery required to reason about the system.

Then use that machinery to make the system correct and resilient.

Do not demand perfect knowledge of the system before constructing the system that makes such knowledge possible.
