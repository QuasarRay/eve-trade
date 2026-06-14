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
kubectl apply -k distributed-backend\orchestration\kubernetes\overlay\prod
```

Before deployment, patch these production values:

- Images in `kustomization.yaml`, usually by CI with `kustomize edit set image`.
- `api.eve-trade.example.com` in `httproute.yaml` and the platform Gateway.
- The ACME email in `platform/gateway/prod/clusterissuer-letsencrypt-prod.yaml`.
- The `trade-settlement-database` secret.
- The optional `trade-settlement-observability` secret with a `HONEYCOMB_API_KEY`
  key when Honeycomb trace export is enabled.

The base placeholder database secret is intentionally deleted by this overlay.
Create `trade-settlement-database` out of band with a `DATABASE_URL` key using
your production secret manager, External Secrets, Sealed Secrets, or another
approved mechanism.

Create `trade-settlement-observability` out of band with a `HONEYCOMB_API_KEY`
key to export Rust settlement traces to Honeycomb. The deployment still starts
without that secret, and falls back to structured JSON logs only when no key is
present.
