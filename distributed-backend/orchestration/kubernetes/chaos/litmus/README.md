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

Each engine gracefully deletes one pod at a time, keeps the blast radius to one
replica, and lets the Dagger pipeline verify that all deployments are available
before and after the experiment.

Render locally with:

```powershell
kubectl kustomize distributed-backend\orchestration\kubernetes\chaos\litmus\overlays\prod
```
