# Istio Production Platform

This directory defines the production Istio control plane profile expected by
the Eve Trade production Kubernetes overlays.

Install or upgrade it with `istioctl`:

```powershell
istioctl install -f distributed-backend\orchestration\kubernetes\platform\istio\prod\istio-operator.yaml
```

Then apply the namespace metadata if it is not already managed elsewhere:

```powershell
kubectl apply -k distributed-backend\orchestration\kubernetes\platform\istio\prod
```

Production assumptions:

- Gateway API support is enabled by the Istio control plane.
- The mesh trust domain is `cluster.local`.
- The OpenTelemetry Collector is reachable at
  `otel-collector.eve-trade-observability.svc.cluster.local:4317`.
- Application namespaces use sidecar injection and STRICT mTLS.
- Outbound traffic remains `ALLOW_ANY` because the settlement database host is
  environment-specific and not modeled as an Istio `ServiceEntry` yet.

