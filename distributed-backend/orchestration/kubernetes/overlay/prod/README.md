# Production Overlay

This overlay is the production application layer for Eve Trade.

Render it with:

```powershell
kubectl kustomize distributed-backend\orchestration\kubernetes\overlay\prod
```

Apply the shared Istio platform, Gateway API platform, observability stack, and
application overlay in that order:

```powershell
istioctl install -f distributed-backend\orchestration\kubernetes\platform\istio\prod\istio-operator.yaml
kubectl apply -k distributed-backend\orchestration\kubernetes\platform\gateway\prod
kubectl apply -k distributed-backend\orchestration\kubernetes\base\observability
kubectl apply -k distributed-backend\orchestration\kubernetes\overlay\prod
```

Before deployment, patch these production values:

- Image digests in `kustomization.yaml`. The checked-in zero digests are
  placeholders so production renders are immutable and fail closed until CI
  injects real `sha256:` digests for the built images.
- `api.eve-trade.example.com` in the HTTP redirect route and the platform
  Gateway, if the HTTP redirect endpoint is used.
- The ACME email in `platform/gateway/prod/clusterissuer-letsencrypt-prod.yaml`.
- The `trade-settlement-database` secret.
- The `rabbitmq` secret.
- The `api-gateway-edge-auth` secret with `GAME_PACKET_HMAC_SECRET`, supplied
  out of band by the production secret manager.
- The `observability-backends` secret in the `eve-trade-observability` namespace
  with Honeycomb and Sentry credentials.

The production overlay enables Istio sidecar injection, STRICT mTLS,
deny-by-default mesh authorization, a Quilkin UDP edge, service-account allow
rules for the internal API Gateway -> Market -> RabbitMQ -> settlement-worker
-> trade-settlement path, and an HTTP-to-HTTPS redirect endpoint when the
platform Gateway is installed. Trade traffic enters through Quilkin UDP and API
Gateway forwards only raw GUI payloads to Market.

The base kustomization intentionally does not create database or RabbitMQ
secrets. Create `trade-settlement-database` with a `DATABASE_URL` key and
`rabbitmq` with `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS`, and
`RABBITMQ_URL` keys using Terraform, your production secret manager, External
Secrets, Sealed Secrets, or another approved mechanism. The
`settlement-db-migrate` Job uses the same `DATABASE_URL` secret and the
checked-in settlement SQL migrations before the application pods become useful.

Create `observability-backends` out of band with `HONEYCOMB_API_KEY`,
`SENTRY_AUTH_TOKEN`, `SENTRY_ORG_SLUG`, and `SENTRY_URL` keys. Application pods
send OTLP telemetry only to the OpenTelemetry Collector; Honeycomb, Sentry, and
Prometheus routing is handled by the collector. See `distributed-backend/OBSERVABILITY.md`.
