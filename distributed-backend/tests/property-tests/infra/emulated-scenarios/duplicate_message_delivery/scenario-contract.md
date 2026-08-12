# Scenario contract: `duplicate_message_delivery`

- Scenario revision: `1`
- Status/trust: `IMPLEMENTED_BUT_EMULATION_VERIFICATION_PENDING`
- AnySystem revision: `74613a368c73fb12f25778ce33ca11c9a833da96`
- Controller seed semantics: unsigned 64-bit seed; same scenario, revision, parameters, and barrier outcomes replay the same logical action order.
- Controller time: AnySystem logical phase ticks.
- Application-visible clock: `REAL_UNINJECTED`.

## Purpose

Publish the same logical work/result identity more than once through the real broker.

## Consuming context-specific business tests

- `test_chaos_experiment_leaves_zero_duplicate_terminal_trade_state_changes`
- `test_database_unique_constraint_prevents_duplicate_settlement_outbox_event_id`
- `test_deadlock_retry_cannot_duplicate_settlement`
- `test_different_message_ids_with_same_idempotency_key_produce_one_business_effect`
- `test_duplicate_delivery_spans_link_to_original_message_trace_without_creating_false_parentage`
- `test_duplicate_redelivery_after_process_restart_produces_one_business_effect`
- `test_duplicate_settlement_result_delivery_changes_projection_once`
- `test_duplicate_settlement_work_delivery_executes_business_settlement_once`
- `test_duplicate_terminal_result_with_different_message_id_is_projection_idempotent`
- `test_every_pubsub_handler_has_duplicate_delivery_test_reference`
- `test_idempotency_cleanup_never_deletes_record_before_all_duplicate_delivery_windows_expire`
- `test_pubsub_redelivery_preserves_idempotency_key`
- `test_random_duplicate_message_injection_during_mixed_trade_workload_preserves_projection_consistency`
- `test_same_message_id_with_different_payload_is_rejected_as_integrity_violation`
- `test_serialization_failure_retry_cannot_duplicate_settlement`

Context-independent atomic tests are selected from `classification.json` and are scheduled in every implemented scenario without duplicating that list here.

## Real deployment

Components:

- `nsqd`
- `settlement-worker`
- `trade-settlement`
- `encore-backend`
- `postgres`

- Namespace: unique per run, labelled with the exact run/scenario identity and explicitly rejected when its context resembles production.
- Artifact inputs: source commit, built image digests, rendered manifest digest, configuration digest, schema version, dependency/tool pins.
- External dependencies: nsqd, settlement-worker, trade-settlement, encore-backend, postgres.
- Workload actors/input schema: `duplicate identified work or result message` with exact request, actor, entity, scenario, seed, and action identities.

## Initial state

The scenario runner creates only the minimal fixture requested by the consuming test, proves every required entity exists through real application/database/broker observations, records the selected real process/pod identities, and rejects empty or stale fixture evidence.

## Deterministic controller and action order

1. `deploy-real-components` — `DAGGER_VERIFY_REAL_DEPLOYMENT`; barrier `deployment-ready`.
2. `initialize-scenario-fixture` — `DAGGER_INITIALIZE_FIXTURE`; barrier `initial-state-witnessed`.
3. `establish-controlled-prerequisite` — `BROKER_PUBLISH_DUPLICATE`; barrier `controlled-prerequisite-witnessed`.
4. `execute-identified-workload` — `DAGGER_EXECUTE_WORKLOAD`; barrier `workload-path-witnessed`.
5. `release-controlled-condition` — `DAGGER_RELEASE_SCENARIO_ACTION`; barrier `recovery-witnessed`.
6. `collect-factual-observations` — `DAGGER_COLLECT_OBSERVATIONS`; barrier `observations-complete`.
7. `cleanup-scenario-owned-state` — `DAGGER_SCOPED_CLEANUP`; barrier `cleanup-complete`.

The controller cannot advance on elapsed wall-clock time. Each barrier has a stable name, bounded timeout, success witness, failure artifact, and deterministic failure transition.

## Scenario-specific controlled action

- Action: `BROKER_PUBLISH_DUPLICATE`
- Exact target: `declared NSQ topic and channel`

- Affected path/resource: `publisher-to-nsqd-to-consumer`
- Activation barrier: first delivery identity is observed at the declared consumer boundary.
- Litmus definition/reference: None; the declared Dagger/application control action establishes the prerequisite..
- Control-plane acknowledgement: action/scenario/run-bound resource identity and status.
- Independent effect witness: NSQ stats and application message observations record at least two deliveries of the same logical identity.
- Negative control: the prerequisite witness is false before activation and whenever either control-plane acknowledgement or the independent condition is absent.
- Release/recovery barrier: all deliveries reach terminal handling and channel in-flight count returns to baseline.

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
- `recovery.condition` — all deliveries reach terminal handling and channel in-flight count returns to baseline.
- `termination.condition` — Completion is emitted only after observations and scoped cleanup barriers succeed.
- `reset.isolation` — Scenario-owned Kubernetes, broker, database, controller, and evidence state cannot bleed into another run.
- `observations.completeness` — Every observation required by consuming oracles is present and bound to exact entities.
- `observations.provenance` — Every exported fact has one allowed provenance class and source reference.
- `observations.no_business_answers` — Scenario outputs contain facts and never derived expected business answers or pass/fail fields.

## Current blocker

None. Implementation remains unverified by the intentionally future test catalog.
