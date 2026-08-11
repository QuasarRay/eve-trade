# Dagger and Litmus Property-Test Infrastructure

This directory owns environment orchestration and chaos prerequisite creation. Individual Hypothesis examples generate bounded scenario parameters; they do not install clusters or decide business correctness.

## Architecture

```text
Hypothesis generated case
  -> protocol-v3 challenge
  -> Dagger disposable Kind cluster (or explicitly supplied disposable kubeconfig)
  -> pinned Litmus ChaosEngine and real workload probes
  -> raw Kubernetes, target-effect, request-window, and recovery evidence
  -> independent Python integrity validator and contract oracle
```

`test-requirements.json` classifies every authoritative name into one route. `litmus-contracts.json` gives every chaos route its target, selector, safe generated domains, initial state, probes, experiment, physical effect proof, window crossing, raw observations, recovery deadline, post-invariant queries, and cleanup actions.

Only chaos contracts listed by `IMPLEMENTED_CHAOS_ORACLES` are executable. Other Litmus mappings are prerequisite-ready but fail closed until their exact business workload/oracle or production-disabled persistence-boundary hook exists.

## Safety

The driver refuses any context whose name resembles production, staging, or live. It also requires both:

- namespace labels `eve-trade.io/chaos-safe=true` and the exact current `eve-trade.io/run-id`;
- ConfigMap `eve-trade-chaos-safety` authorizing the same run and declaring a disposable cluster.

Kind mode creates a disposable cluster. Supplied mode requires a caller-provided kubeconfig, a pre-labelled chaos-safe namespace, and `EVE_TRADE_DISPOSABLE_CONTEXT` equal to `kubectl config current-context`. No kubeconfig or credential is stored in the repository.

Every wait and subprocess is bounded. Chaos resources carry execution labels and cleanup deletes only the exact engine UID/run labels. Cleanup failure is a pipeline failure.

## Pinned components

Tool and source pins are constants in `pipeline.py`, `install_litmus.py`, and `litmus-contracts.json`: Dagger SDK, Python/Go container digests, Kind, Kind node image, kubectl, Helm (including download SHA-256), the `litmus-core-3.31.0` release/commit, its installed `litmus-agent` 3.30.0 chart and operator/runner tags, the Chaos Charts commit, and the exact fault image references from that checkout. The installer checks the pinned `Chart.yaml` metadata before Helm can create resources.

## Commands

Install the Dagger SDK in an isolated environment, then run structural checks:

```bash
python -m pip install -r distributed-backend/tests/property-tests/infra/requirements.txt
python distributed-backend/tests/property-tests/infra/pipeline.py --mode structural
```

Run the implemented suite in a disposable Kind cluster:

```bash
python distributed-backend/tests/property-tests/infra/pipeline.py \
  --mode kind --suite implemented --chaos-examples 3
```

The implemented suite records separate meta, static, native-existing,
direct-live, and Litmus stages. A failed stage is written to `summary.json`
before orchestration aborts; unconditional cleanup is recorded separately.

Attempt the complete strict catalog (expected to fail while the exhaustive requirement manifest contains blockers):

```bash
python distributed-backend/tests/property-tests/infra/pipeline.py \
  --mode kind --suite full-strict --chaos-examples 1
```

Use a supplied disposable cluster:

```bash
export EVE_TRADE_DISPOSABLE_CONTEXT="kind-my-disposable-cluster"
export EVE_TRADE_APP_NAMESPACE="eve-trade-run-123"
python distributed-backend/tests/property-tests/infra/pipeline.py \
  --mode supplied --kubeconfig /secure/path/kubeconfig --suite implemented
```

The supplied deployment must already expose the same live E2E endpoints through environment variables. Artifacts are written under ignored `.o11y/runs/local-property-<run-id>/` directories and include JUnit, logs, per-invocation raw evidence, stage outcomes, and cleanup outcome.

## Local structural checks without Dagger

```bash
python -m compileall -q distributed-backend/tests/property-tests/e2e distributed-backend/tests/property-tests/infra
python distributed-backend/tests/property-tests/e2e/tools/sync_authoritative_catalog.py --check
python distributed-backend/tests/property-tests/infra/generate_contract_manifests.py --check
python distributed-backend/tests/property-tests/e2e/tools/generate_hypothesis_audit.py --check
python distributed-backend/tests/property-tests/e2e/tools/generate_delivery_integrity.py --check
python -m pytest -q distributed-backend/tests/property-tests/e2e/tests
python distributed-backend/tests/property-tests/infra/verify_collection.py
```
