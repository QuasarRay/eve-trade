# Litmus Chaos Pipelines

This directory contains Eve Trade's reusable Litmus chaos resources. The
engines are safe to apply because they default to `spec.engineState: stop`; the
CI/CD pipeline is responsible for deliberately activating them.

## Prerequisites

- Litmus Chaos Operator and CRDs are installed in the production cluster.
- The `pod-delete` `ChaosExperiment` is installed in the `eve-trade` namespace.
- The CI deploy identity can manage `ChaosEngine` and `ChaosResult` resources in
  `eve-trade`.

## Production Suite

The production overlay renders three stopped `pod-delete` engines:

- `eve-trade-api-gateway`
- `eve-trade-market`
- `eve-trade-trade-settlement`

Each engine gracefully deletes 50% of the selected pods, runs a continuous
API-gateway readiness probe, and is wrapped by the Dagger pipeline's
authenticated trade smoke before, throughout, and after the experiment. Any
failed business probe fails the chaos gate even if Litmus reports that fault
injection itself succeeded.

Render locally with:

```powershell
kubectl kustomize distributed-backend\orchestration\kubernetes\chaos\litmus\overlays\prod
```
