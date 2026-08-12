# Scenario contract: `dependency_outage_backlog_and_recovery`

- Scenario revision: `1`
- Status/trust: `IMPLEMENTED_BUT_EMULATION_VERIFICATION_PENDING`
- AnySystem revision: `74613a368c73fb12f25778ce33ca11c9a833da96`
- Controller seed semantics: unsigned 64-bit seed; same scenario, revision, parameters, and barrier outcomes replay the same logical action order.
- Controller time: AnySystem logical phase ticks.
- Application-visible clock: `REAL_UNINJECTED`.

## Purpose

Make one declared required dependency unavailable, accumulate identified work, then recover it.

## Consuming context-specific business tests

- `test_health_endpoint_does_not_report_ready_when_postgres_is_unreachable`
- `test_health_endpoint_does_not_report_ready_when_required_pubsub_is_unavailable`
- `test_liveness_endpoint_stays_successful_during_recoverable_downstream_outage`
- `test_pubsub_backlog_recovers_after_downstream_pause`
- `test_quilkin_backend_unavailable_returns_no_false_success`
- `test_readiness_endpoint_fails_while_required_dependency_prevents_new_business_work`

Context-independent atomic tests are selected from `classification.json` and are scheduled in every implemented scenario without duplicating that list here.

## Real deployment

Components:

- `encore-backend`
- `settlement-worker`
- `trade-settlement`
- `postgres`
- `nsqd`

- Namespace: unique per run, labelled with the exact run/scenario identity and explicitly rejected when its context resembles production.
- Artifact inputs: source commit, built image digests, rendered manifest digest, configuration digest, schema version, dependency/tool pins.
- External dependencies: encore-backend, settlement-worker, trade-settlement, postgres, nsqd.
- Workload actors/input schema: `identified workload plus readiness/liveness probes` with exact request, actor, entity, scenario, seed, and action identities.

## Initial state

The scenario runner creates only the minimal fixture requested by the consuming test, proves every required entity exists through real application/database/broker observations, records the selected real process/pod identities, and rejects empty or stale fixture evidence.

## Deterministic controller and action order

1. `deploy-real-components` — `DAGGER_VERIFY_REAL_DEPLOYMENT`; barrier `deployment-ready`.
2. `initialize-scenario-fixture` — `DAGGER_INITIALIZE_FIXTURE`; barrier `initial-state-witnessed`.
3. `establish-controlled-prerequisite` — `DAGGER_SCALE_DEPENDENCY_TO_ZERO`; barrier `controlled-prerequisite-witnessed`.
4. `execute-identified-workload` — `DAGGER_EXECUTE_WORKLOAD`; barrier `workload-path-witnessed`.
5. `release-controlled-condition` — `DAGGER_RELEASE_SCENARIO_ACTION`; barrier `recovery-witnessed`.
6. `collect-factual-observations` — `DAGGER_COLLECT_OBSERVATIONS`; barrier `observations-complete`.
7. `cleanup-scenario-owned-state` — `DAGGER_SCOPED_CLEANUP`; barrier `cleanup-complete`.

The controller cannot advance on elapsed wall-clock time. Each barrier has a stable name, bounded timeout, success witness, failure artifact, and deterministic failure transition.

## Scenario-specific controlled action

- Action: `DAGGER_SCALE_DEPENDENCY_TO_ZERO`
- Exact target: `scenario-declared postgres, nsqd, or settlement dependency`

- Affected path/resource: `declared service dependency edge`
- Activation barrier: clean dependency and service-health baselines are observed.
- Litmus definition/reference: None; the declared Dagger/application control action establishes the prerequisite..
- Control-plane acknowledgement: action/scenario/run-bound resource identity and status.
- Independent effect witness: Kubernetes zero-ready state and independent path probe prove unavailability.
- Negative control: the prerequisite witness is false before activation and whenever either control-plane acknowledgement or the independent condition is absent.
- Release/recovery barrier: dependency and application readiness recover and scenario-owned backlog drains.

## Prerequisite witness

The facts-only witness contains `scenario_id`, `scenario_revision`, `AnySystem_seed`, relevant entity IDs, established conditions, controller state, logical tick/phase, physical observation timestamp, trace position, control-plane evidence references, and independent-effect evidence references.

## Exported facts and provenance

Facts may include scenario inputs, request/entity IDs, real initial/final rows, message IDs, pod/process identities, observed status, controller phase, physical timestamps, fault state, and raw probe measurements. Every field records one of the allowed provenance classes from the registry. No `expected_*`, `should_*`, `business_valid`, `invariant_satisfied`, or pass/fail business field is emitted.

## Completion, recovery, and cleanup

Completion requires recovery, factual observation collection, and run-scoped cleanup barriers. Cleanup removes only exact scenario/run-labelled Kubernetes/Litmus resources, scenario-owned broker messages/fixtures, and controller state. Failure artifacts are retained outside the reset boundary.

## Residual nondeterminism

Linux/process scheduling, Kubernetes reconciliation, packet timing, database/broker scheduling, startup latency, and wall-clock duration remain physical observations. They do not choose the next controller action.

## Behavioral clauses

- `deployment.components` — The real deployed components exactly match the registered scenario component set.
- `deployment.revisions` — Source, images, manifests, schema, dependencies, Dagger, Litmus, and AnySystem revisions are recorded.
- `deployment.namespace` — The run uses a unique non-production chaos-safe namespace and exact run identity.
- `initial_state.fixture` — Every material fixture entity and initial observation is present before controlled actions begin.
- `workload.identity` — Every workload action carries exact scenario, request, actor, and entity identities.
- `controller.seed` — The AnySystem seed and canonical scenario parameters determine one replayable logical plan.
- `controller.transitions` — The controller emits only declared actions in declared logical order.
- `controller.barrier_gating` — The controller cannot advance until the current action's declared barrier outcome is supplied.
- `dagger.execution` — Dagger executes the exact controller command and preserves scenario IDs, seed, parameters, and order.
- `prerequisite.witness` — The structured prerequisite witness is true only after every declared condition and exact entity is observed.
- `prerequisite.negative_control` — The prerequisite witness remains false when any critical declared component is absent.
- `workload.phase` — The identified workload begins only in the declared scenario phase and targets the registered deployment.
- `time.logical_ticks` — Controller logical ticks order phases and never claim to control physical infrastructure time.
- `time.physical` — Wall-clock durations are recorded as physical observations and do not choose controller transitions.
- `recovery.condition` — dependency and application readiness recover and scenario-owned backlog drains.
- `termination.condition` — Completion is emitted only after observations and scoped cleanup barriers succeed.
- `reset.isolation` — Scenario-owned Kubernetes, broker, database, controller, and evidence state cannot bleed into another run.
- `observations.completeness` — Every observation required by consuming oracles is present and bound to exact entities.
- `observations.provenance` — Every exported fact has one allowed provenance class and source reference.
- `observations.no_business_answers` — Scenario outputs contain facts and never derived expected business answers or pass/fail fields.

## Current blocker

None. Implementation remains unverified by the intentionally future test catalog.
