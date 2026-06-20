# Production Overlay

This overlay is the production application layer for Eve Trade.

Render it with:

```powershell
kubectl kustomize distributed-backend\orchestration\kubernetes\overlay\prod
```

Apply the shared Gateway API platform first when using the included
`HTTPRoute`:

```powershell
kubectl apply -k distributed-backend\orchestration\kubernetes\platform\gateway\prod
kubectl apply -k distributed-backend\orchestration\kubernetes\base\observability
kubectl apply -k distributed-backend\orchestration\kubernetes\overlay\prod
```

Before deployment, patch these production values:

- Images in `kustomization.yaml`, usually by CI with `kustomize edit set image`.
- `api.eve-trade.example.com` in `httproute.yaml` and the platform Gateway.
- The ACME email in `platform/gateway/prod/clusterissuer-letsencrypt-prod.yaml`.
- The `trade-settlement-database` secret.
- The `observability-backends` secret in the `eve-trade-observability` namespace
  with Honeycomb and Sentry credentials.

The base placeholder database secret is intentionally deleted by this overlay.
Create `trade-settlement-database` with a `DATABASE_URL` key using Terraform,
your production secret manager, External Secrets, Sealed Secrets, or another
approved mechanism.

Create `observability-backends` out of band with `HONEYCOMB_API_KEY`,
`SENTRY_AUTH_TOKEN`, `SENTRY_ORG_SLUG`, and `SENTRY_URL` keys. Application pods
send OTLP telemetry only to the OpenTelemetry Collector; Honeycomb, Sentry, and
Prometheus routing is handled by the collector. See `distributed-backend/OBSERVABILITY.md`.
