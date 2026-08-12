# Deterministic real-deployment property infrastructure

The active property-test path is:

```text
explicit pytest/Hypothesis business input
  -> Dagger builds and deploys real EVE Trade components
  -> AnySystem controller emits a seeded, barrier-gated action plan
  -> Dagger executes each declared adapter
  -> Litmus performs scenario-specific physical faults when requested
  -> independent application/database/broker/network/resource observations
  -> facts-only run-evidence.json
  -> reusable or inline business oracle
  -> pytest decides pass/fail
```

AnySystem is pinned to `74613a368c73fb12f25778ce33ca11c9a833da96`. It models one controller process only. It does not model EVE Trade services, networks, nodes, failures, or business correctness. Logical ticks order controller transitions; real wall-clock durations remain measured physical observations.

## Structural gate

GitHub Actions invokes `.github/dagger/property_tests.py`. The workflow remains a thin scheduler; Dagger compiles Python, checks generated manifests, runs deterministic validators, collects all explicit business-test identities, and checks the Rust controller.

The same source checks can be run directly:

```text
python distributed-backend/tests/property-tests/infra/emulated-scenarios/runtime/generate_manifests.py --check
python distributed-backend/tests/property-tests/infra/emulated-scenarios/runtime/validate.py
python -m pytest --collect-only -q -c distributed-backend/tests/property-tests/e2e/pytest.ini distributed-backend/tests/property-tests/e2e
```

## Real scenario execution

An invocation must conform to `emulated-scenarios/runtime/scenario-invocation.schema.json`. It names one registered implemented scenario, uses the exact `emu-<run-id>` namespace, records all fidelity revisions, supplies nonempty business property input, and declares exactly one adapter request for every controller action. Each generated action also carries a closed adapter policy: ordinary phases use an explicit `ACTION_SEQUENCE`; Litmus activation is bound to the scenario's exact primitive, Kubernetes target, and fixed direction/environment fields; and Litmus release is bound to the matching activation plus an independent recovery witness. There are no implicit adapter defaults. Real execution also requires a clean worktree so the recorded Git revision identifies the exact source snapshot supplied to Dagger.

Run a disposable Kind scenario through Dagger:

```text
python distributed-backend/tests/property-tests/infra/pipeline.py \
  --mode kind \
  --run-id pt-example \
  --invocation /absolute/path/to/invocation.json
```

Or target an already deployed, explicitly chaos-safe disposable cluster:

```text
EVE_TRADE_DISPOSABLE_CONTEXT=exact-current-context \
python distributed-backend/tests/property-tests/infra/pipeline.py \
  --mode supplied \
  --kubeconfig /absolute/path/to/kubeconfig \
  --run-id pt-example \
  --invocation /absolute/path/to/invocation.json
```

The runner fails if an action lacks real evidence, a seeded choice is not bound into the adapter request, Litmus lacks a scenario-bound control-plane acknowledgement, or a non-cleanup barrier lacks an independent effect reference. Failures and scoped-cleanup outcomes are preserved under `.o11y/runs/`.

## Litmus pins and semantics

`install_litmus.py` checks out the exact Litmus Helm and Chaos Charts commits recorded in `litmus-contracts.json`, verifies chart/operator pins, and installs only the primitive declared by the selected scenario. The scenario runner never treats Litmus status as a business oracle. A chaos barrier needs both the run/scenario/action-bound Litmus resource and an independently configured real-effect witness.

## Authoritative and generated state

The rule files and complete `tests-to-implement/` tree are read-only inputs. `classification.json`, `emulated-scenarios/scenario-registry.json`, scenario contracts, future verification-name specifications, and implementation state are deterministic generated outputs. The generator creates no pytest function. Future emulation-verification test functions intentionally remain at zero.
