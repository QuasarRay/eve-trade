# Scenario contract: `grpc_connection_failure_window`

- Scenario revision: `1`
- Status/trust: `PLANNED`
- AnySystem revision: `74613a368c73fb12f25778ce33ca11c9a833da96`
- Controller seed semantics: unsigned 64-bit seed; same scenario, revision, parameters, and barrier outcomes replay the same logical action order.
- Controller time: AnySystem logical phase ticks.
- Application-visible clock: `REAL_UNINJECTED`.

## Purpose

Disrupt a real worker-to-settlement gRPC connection at a declared write/commit uncertainty phase.

## Consuming context-specific business tests

- `test_grpc_deadline_exceeded_after_possible_commit_uses_status_recovery_before_reexecution`
- `test_grpc_deadline_exceeded_with_unknown_commit_outcome_maps_to_status_recovery_path`
- `test_grpc_unavailable_before_request_write_maps_to_retryable_worker_failure`
- `test_half_open_grpc_connection_is_detected_without_blocking_worker_forever`
- `test_transient_grpc_unavailable_is_retried_with_same_idempotency_key`
- `test_worker_grpc_connection_failure_before_request_write_retries_without_creating_settlement_batch`
- `test_worker_loses_grpc_connection_after_commit_and_before_response_does_not_duplicate_settlement`
- `test_worker_loses_grpc_connection_during_request_and_recovers_by_idempotency_key`

Context-independent atomic tests are selected from `classification.json` and are scheduled in every implemented scenario without duplicating that list here.

## Real deployment

Components:

- `settlement-worker`
- `trade-settlement`
- `postgres`
- `nsqd`

- Namespace: unique per run, labelled with the exact run/scenario identity and explicitly rejected when its context resembles production.
- Artifact inputs: source commit, built image digests, rendered manifest digest, configuration digest, schema version, dependency/tool pins.
- External dependencies: settlement-worker, trade-settlement, postgres, nsqd.
- Workload actors/input schema: `identified gRPC request and status recovery` with exact request, actor, entity, scenario, seed, and action identities.

## Initial state

The scenario runner creates only the minimal fixture requested by the consuming test, proves every required entity exists through real application/database/broker observations, records the selected real process/pod identities, and rejects empty or stale fixture evidence.

## Deterministic controller and action order

1. `deploy-real-components` — `DAGGER_VERIFY_REAL_DEPLOYMENT`; barrier `deployment-ready`.
2. `initialize-scenario-fixture` — `DAGGER_INITIALIZE_FIXTURE`; barrier `initial-state-witnessed`.
3. `activate-scenario-chaos` — `LITMUS_DIRECTIONAL_NETWORK_PARTITION_AT_GRPC_BARRIER`; barrier `chaos-control-plane-acknowledged`.
4. `witness-scenario-effect` — `WAIT_FOR_INDEPENDENT_EFFECT_WITNESS`; barrier `independent-effect-witnessed`.
5. `execute-identified-workload` — `DAGGER_EXECUTE_WORKLOAD`; barrier `workload-path-witnessed`.
6. `release-controlled-condition` — `DAGGER_RELEASE_SCENARIO_ACTION`; barrier `recovery-witnessed`.
7. `collect-factual-observations` — `DAGGER_COLLECT_OBSERVATIONS`; barrier `observations-complete`.
8. `cleanup-scenario-owned-state` — `DAGGER_SCOPED_CLEANUP`; barrier `cleanup-complete`.

The controller cannot advance on elapsed wall-clock time. Each barrier has a stable name, bounded timeout, success witness, failure artifact, and deterministic failure transition.

## Scenario-specific controlled action

- Action: `LITMUS_DIRECTIONAL_NETWORK_PARTITION_AT_GRPC_BARRIER`
- Exact target: `worker egress to trade-settlement`
- Runtime-enforced Kubernetes target policy: `{"kind":"deployment","selector":"app.kubernetes.io/name=encore-backend"}`.
- Runtime-enforced fixed Litmus environment: `{"NAMESPACE_SELECTOR":"kubernetes.io/metadata.name=${RUN_NAMESPACE}","POD_SELECTOR":"app.kubernetes.io/name=trade-settlement","POLICY_TYPES":"egress"}`.
- Affected path/resource: `worker-to-settlement`
- Activation barrier: gRPC transport failpoint distinguishes before-write from possible-commit phase.
- Litmus definition/reference: `pod-network-partition` against `worker egress to trade-settlement` on `worker-to-settlement`.
- Control-plane acknowledgement: action/scenario/run-bound resource identity and status.
- Independent effect witness: Litmus acknowledgement and independent worker-origin gRPC probe failure are required.
- Negative control: the prerequisite witness is false before activation and whenever either control-plane acknowledgement or the independent condition is absent.
- Release/recovery barrier: partition is absent and idempotency status lookup succeeds.

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
- `recovery.condition` — partition is absent and idempotency status lookup succeeds.
- `termination.condition` — Completion is emitted only after observations and scoped cleanup barriers succeed.
- `reset.isolation` — Scenario-owned Kubernetes, broker, database, controller, and evidence state cannot bleed into another run.
- `observations.completeness` — Every observation required by consuming oracles is present and bound to exact entities.
- `observations.provenance` — Every exported fact has one allowed provenance class and source reference.
- `observations.no_business_answers` — Scenario outputs contain facts and never derived expected business answers or pass/fail fields.
- `chaos.target` — Litmus targets exactly worker egress to trade-settlement and the affected path is worker-to-settlement.
- `chaos.control_plane` — A scenario/action-bound Litmus control-plane acknowledgement is required but is not sufficient.
- `chaos.independent_effect` — Litmus acknowledgement and independent worker-origin gRPC probe failure are required.
- `chaos.activation` — gRPC transport failpoint distinguishes before-write from possible-commit phase.
- `chaos.release` — The scenario-specific Litmus effect is removed before recovery can be witnessed.
- `chaos.unaffected_control` — Declared unaffected control resources remain independently observable when the scenario requires them.

## Current blocker

No transport barrier exposes before-write versus possible-commit phases to the scenario controller.
