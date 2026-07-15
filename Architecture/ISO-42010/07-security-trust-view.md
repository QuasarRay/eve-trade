# Security and Trust View

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical current state |
| Last reviewed | 2026-07-08 |
| Governing viewpoint | VP-06 Security And Trust |
| Evidence baseline | v9 experimental refactor; branch delta recorded in `changes/v9/changes.md` |

Governed by: [VP-06 Security And Trust Viewpoint](./02-viewpoints.md#vp-06-security-and-trust-viewpoint)

## Concerns Addressed

This view addresses CON-17, CON-18, CON-19, CON-20, CON-21, and CON-22.

## Trust Boundary Model

Model ID: `MODEL-SEC-01`; view component ID: `VC-SEC-01`.

```mermaid
flowchart LR
  Caller["Game frontend / local simulator"] -->|signed UDP edge envelope| Quilkin["Quilkin UDP"]
  Quilkin -->|UDP forward| Gateway["Encore gateway UDP edge"]
  subgraph InternalServices["Internal service boundary"]
    Gateway -->|raw GUI payload only| Market["Market"]
    Market --> PubSub["Encore Pub/Sub"]
    PubSub --> Worker["settlement worker"]
    Worker --> Settlement["trade-settlement"]
  end
  Settlement --> Database["PostgreSQL"]
  Market --> Database
  Secrets["Secrets Store / Kubernetes Secrets"] -. supplies .-> Gateway
  Secrets -. supplies .-> Market
  Secrets -. supplies .-> Worker
  Secrets -. supplies .-> Settlement
```

## Control And Gap Table

| Security concern | Current control | Residual gap or risk |
| --- | --- | --- |
| UDP edge exposure | Quilkin is the UDP entry point; Encore gateway validates packet size, empty packets, a domain-separated HMAC over all envelope metadata and canonical payload, replay, rate limits, queue capacity, and downstream timeout before forwarding. | Edge replay cache is process-local and provides replica-local suppression only; Market and settlement idempotency are the cross-replica correctness boundary. External DDoS controls are outside this repo. |
| Internal service exposure | Encore gateway forwards only to Market; Kubernetes network policies restrict internal ingress paths. | Local development publishes more ports for convenience; production ingress depends on correct Quilkin/NetworkPolicy/Istio deployment. |
| Market policy ownership | Encore gateway forwards raw GUI payload only; settlement operations are constructed by Market. | Direct access to Market or settlement APIs bypasses the intended packet entry point. |
| Settlement operation privilege | Requests carry typed parent intent and authenticated actor context. Rust independently enforces intent grammar, actor ownership, authoritative resource relationships, and legal state before its transaction executor runs. | Workload identity outside strict-mTLS deployments remains an operator responsibility; direct non-mesh exposure is unsupported. |
| Actor identity trust | Signed packets protect payload integrity; Market validates ownership against database snapshots. | HMAC does not bind account identity to capsuleer IDs; client-supplied actor fields remain a trust assumption. |
| Gateway metadata isolation | Remote address/transport metadata is gateway-internal log/trace data and not sent to Market's business request. | Logs/traces must avoid full payload logging and must be reviewed before production. |
| Network reachability precision | Production overlay includes default deny and service-specific network policy paths. | Policies have not been rendered/applied in a target cluster during this update; database egress remains broad TCP `5432`. |
| Secret placement | ConfigMaps and Secrets are used for runtime configuration. | Secret rotation, external secret provider integration, and production credential lifecycle are outside the service code and not fully defined in repo. |
| Transport security | Gateway/Istio manifests define production ingress and service security resources. | h2c/plaintext service communication exists inside local/internal paths; mTLS enforcement is not verified here. |
| Auditability | Settlement metadata and append-only ledgers record durable effects and failures; item-stack ledgers are hash-chained and current stack rows must match the latest item ledger row. | Cross-service request correlation depends on consistent request IDs, trace propagation, and telemetry export availability. |

## Security Control Layers

| Layer | Implemented or documented controls | Current gaps |
| --- | --- | --- |
| Application layer | Encore gateway validates signed UDP envelope and forwards raw GUI payload; Market validates protobuf-modeled trade input and ownership against database snapshots; trade-settlement validates settlement protobuf requests with protovalidate and enforces row-level operation preconditions. | Application code does not yet bind request actor IDs to authenticated identity claims. |
| Mesh layer | Production overlay defines strict mTLS, default-deny `AuthorizationPolicy`, service-account principals, and allowed internal RPC paths. | External UDP authentication is HMAC at the edge; account identity provider integration is not implemented. |
| Kubernetes network layer | Default deny, service-specific ingress/egress, telemetry egress, DNS egress, and Istio control-plane egress are defined. | Database egress is broad TCP `5432` without destination selector in current manifests. |
| Broker layer | Encore Pub/Sub credentials, internal service reachability, command queue, and dead-letter queue exist. | Per-service broker credentials, credential rotation, and broker-level authorization policy are not fully documented. |
| Secret layer | Kubernetes Secrets are referenced for database, Encore Pub/Sub, and observability credentials. | External secret provider, rotation, break-glass access, and ownership are not defined. |
| Operations layer | CI/CD and Kubernetes overlay docs describe deployment preconditions. | No formal security sign-off, threat-model review cadence, or incident runbook is defined. |

## Current Actor Identity Binding Gap

Market currently validates ownership using actor fields carried in requests.
The repository does not implement a complete mapping from authenticated identity
claims to those actor fields.

| Request field | Current trust gap |
| --- | --- |
| `issued_by_capsuleer_id` | Accepted from request data and checked against item ownership snapshots. |
| `buyer_capsuleer_id` | Accepted from request data and checked against buyer wallet/destination ownership snapshots. |
| `cancelled_by_capsuleer_id` | Accepted from request data and checked against trade issuer snapshots. |
| `interaction_id` | Required in the GUI packet for edge replay and Market request correlation. |
| `external_request_id` | Defaults from the interaction ID when not supplied in player input. |
| `idempotency_key` | Defaults from the interaction ID when not supplied in player input; actor-scoped key policy is not fully specified. |

This is recorded as a production-readiness gap, not as an implemented control.

## Settlement API Access And Gaps

View component ID: `VC-SEC-02`.

| Topic | Current state | Status |
| --- | --- | --- |
| Checked-in production-like caller path | NetworkPolicy allows settlement worker to reach trade-settlement on `9092`; Market has no trade-settlement egress in the production overlay. | Partially enforced by manifests; render/apply/negative tests not recorded |
| Service identity | Mesh principal policy exists in production overlay. | Partially enforced; depends on mesh deployment and verification. |
| Settlement operation payloads | Proto annotations define required fields, UUID/positive/state/timestamp rules, oneof requirements, and cross-field merge distinctness; Go and Rust call protovalidate before command execution. Rust conversion and operation handlers retain type parsing and row-level preconditions. | Enforced by proto and code; v9 test results recorded in `18-evidence-manifest.md` |
| Market compromise impact | Market can construct privileged generic settlement operations for the broker/direct executor path. | Gap |
| Broker publishers | Network policy and broker credentials restrict the normal path. | Partially enforced; per-service broker authorization not documented. |
| Settlement call audit data | Settlement metadata records batch, request attempt, step, service, and actor fields. | Enforced, with observability gaps |

## Current Settlement Access Model

| Caller identity | Current modeled capability | Current gap |
| --- | --- | --- |
| settlement worker service account | Production overlay allows RPC to trade-settlement. | Broker provenance and operation-level policy are not implemented in trade-settlement. |
| Market service account | Production overlay allows Encore Pub/Sub publish and database reads; direct trade-settlement RPC is not allowed by NetworkPolicy. | Market binary still supports direct/connect transport outside this configured topology. |
| Encore gateway service account | No direct settlement command publication or trade-settlement RPC is modeled. | Negative-path tests are not implemented. |
| Human/operator | No normal direct mutation path is modeled. | Break-glass procedure and audit trail are not defined in repo. |
| Any other workload | Default-deny policy is present. | Render/apply and negative-path evidence is not recorded. |

## Trust Boundary Responsibilities

| Boundary | Current behavior or documented gap |
| --- | --- |
| External to Quilkin/Encore gateway | HMAC integrity and replay/rate controls exist; identity-to-actor binding is not complete in application code. |
| gateway to Market | Forward raw GUI payload only; keep source transport/address metadata internal; do not reinterpret trade policy. |
| Market to Encore Pub/Sub | Market publishes protovalidate-checked settlement commands with deterministic IDs and idempotency data in the Encore Pub/Sub configuration. |
| Encore Pub/Sub to settlement worker | Queue and DLQ topology exist; per-service broker authorization is not fully documented. |
| settlement worker to trade-settlement | Production NetworkPolicy models worker-originated settlement execution. |
| Market to PostgreSQL | Uses configured read-only `MARKET_DATABASE_URL`; manifests keep the settlement writer secret out of the Encore backend workload. |
| trade-settlement to PostgreSQL | Uses configured settlement writer `DATABASE_URL`; migration uses a separate migration `DATABASE_URL`. |
| Operators to runtime | Deployment and secret-management approval flows are not fully defined in repo. |

## Misuse Cases

| Misuse case | Current architectural response | Current gap |
| --- | --- | --- |
| Caller claims another capsuleer ID. | Market validates resource ownership where data is available. | HMAC proves packet integrity, not account ownership; actor IDs are not bound to authenticated claims in application code. |
| Caller replays a successful packet. | Encore gateway rejects immediate duplicate interaction IDs; Market/trade-settlement idempotency prevents duplicate durable settlement. | Edge replay cache is process-local. |
| Attacker reaches trade-settlement directly. | Production NetworkPolicy models no direct external access. | Mesh authorization render/apply and negative tests are not recorded. |
| Attacker injects Encore Pub/Sub messages. | Broker credentials and network policy restrict publishers in the modeled path. | Per-service Encore Pub/Sub authorization and DLQ monitoring are not fully documented. |
| Operator deploys mismatched protobuf and service versions. | CI and generated code validation are documented as validation paths. | Independent deployment compatibility gates are not implemented here. |
| Market service is compromised. | Mesh and network policy isolate direct database writes to trade-settlement, but Market can send powerful settlement operations. | Operation-provenance and operation-allow policy are not implemented in trade-settlement. |
| JWT placeholders are deployed unchanged. | Istio policy structure exists but validates against example issuer values. | Placeholder rejection is not implemented as a repository gate. |

The detailed threat model is maintained in
[Threat Model View](./14-threat-model-view.md).

## Security View Assertions

| Assertion | Enforcement tag | Evidence or gap |
| --- | --- | --- |
| Network isolation is the current main control around the settlement API in manifests. | Partially enforced | Mesh/service identity verification and payload provenance are not fully evidenced. |
| Request actor fields are not bound to authenticated identity claims in application code. | Gap | Actor identity binding is not implemented end to end. |
| The settlement API is a privileged internal API. | Partially enforced | Mesh and network controls restrict access; operation-level authorization is not implemented in trade-settlement. |
| Local development exposure differs from production-like ingress. | Enforced by manifest | Local exposures are loopback-only in local Encore/Kubernetes; production trade ingress is Quilkin UDP. |
| HMAC and mTLS controls exist at different layers. | Partially enforced | Edge packet integrity is implemented with HMAC; internal service mTLS depends on production mesh deployment verification. |

## Security Verification Gaps

| Verification | Evidence needed to close the gap | Current status |
| --- | --- | --- |
| Rendered production mesh policy | Rendered `PeerAuthentication` and `AuthorizationPolicy` for internal service paths. | Not run; placeholder gate open |
| Negative service-call tests | Tests showing Encore gateway and Market cannot call trade-settlement directly in production topology. | Not implemented |
| Actor spoofing tests | Unit/e2e tests proving request actor fields must match authenticated claims. | Not implemented |
| Broker permission tests | Evidence that only intended publishers/consumers can use settlement exchange/queues. | Not implemented |
| Secret lifecycle review | Owner, rotation, access, audit, and break-glass records for each secret. | Not verified |

## Concern Satisfaction

| Concern | How this view satisfies it | Evidence or gap |
| --- | --- | --- |
| CON-17 | Identifies Encore gateway as external boundary and settlement as internal. | Trust Boundary Model. |
| CON-18 | Separates network policy and mesh controls. | Security Control Layers. |
| CON-19 | Records current actor-to-claim binding gap. | Current Actor Identity Binding Gap. |
| CON-20 | Records settlement API access model and gaps. | Settlement API Access And Gaps. |
| CON-21 | Lists secret-management gaps. | Security Control Layers and Deployment view. |
| CON-22 | Documents edge HMAC, strict mTLS expectations, and h2c/local assumptions. | Security Control Layers. |
