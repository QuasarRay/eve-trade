# Threat Model View

## View Metadata

| Field | Value |
| --- | --- |
| View status | Canonical |
| Last reviewed | 2026-06-23 |
| Governing viewpoint | VP-12 Threat Model |
| Evidence baseline | Repository commit `fe5c6af`; architecture file hashes are recorded in `18-evidence-manifest.md` |

Governed by: [VP-12 Threat Model Viewpoint](./02-viewpoints.md#vp-12-threat-model-viewpoint)

## Concerns Addressed

This view addresses CON-17, CON-18, CON-19, CON-20, CON-21, and CON-22.

## Asset Inventory

| Asset | Security value | Primary threat |
| --- | --- | --- |
| Capsuleer identity and actor fields | Controls who can issue, accept, or cancel trades. | Spoofing actor IDs. |
| Item stacks | Player inventory value. | Unauthorized transfer or quantity tampering. |
| Wallet balances | Player ISK value. | Unauthorized debit/credit. |
| Trade instances and escrow | Pending exchange state. | Unauthorized cancel/accept or escrow release. |
| Settlement API | High-privilege mutation interface. | Abuse by compromised internal caller. |
| RabbitMQ command queue | Settlement command delivery. | Message injection, replay, loss, or poison messages. |
| PostgreSQL | Source of truth. | Data tampering, data loss, credential compromise. |
| Secrets | Database, broker, telemetry, identity integration. | Credential leakage or stale credentials. |
| Telemetry and settlement metadata | Incident diagnosis and audit. | Missing or tampered diagnostic trail. |

## Attacker Model

| Attacker | Capability assumed |
| --- | --- |
| Untrusted external caller | Can send API Gateway requests unless blocked by ingress/JWT policy. |
| Malicious or buggy upstream game service | Can supply actor IDs, idempotency keys, and request payloads unless application validates claim binding. |
| Compromised API Gateway pod | Can call Market as API Gateway service identity. |
| Compromised Market pod | Can construct settlement commands and publish to RabbitMQ. |
| Compromised settlement-worker pod | Can call trade-settlement with worker service identity. |
| Broker credential holder | Can publish or consume RabbitMQ messages depending broker permissions. |
| Database credential holder | Can read or mutate database within credential privileges. |
| Misconfigured operator | Can deploy placeholder issuer/secrets/images or overly broad ingress. |

## STRIDE-Style Threat Table

Model ID: `MODEL-THR-01`; view component ID: `VC-THR-01`.

| Threat ID | Entry point | STRIDE category | Threat | Preconditions | Severity | Likelihood | Current control | Verification status | Residual risk |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| THR-001 | API Gateway public RPC | Spoofing | Caller claims another capsuleer ID. | Caller can submit actor-sensitive fields and app code does not bind them to claims. | Critical | High | Istio JWT policy intended in production overlay; Market ownership checks. | Not verified; actor claim binding absent. | Production blocker RISK-001. |
| THR-002 | API Gateway public RPC | Replay | Caller replays or duplicates command. | Caller has idempotency key or reuses key with modified request. | High | Medium | Idempotency keys and fingerprints. | Partially verified by Market replay conflict tests; settlement replay tests incomplete. | Actor-scoped idempotency key policy not fully specified. |
| THR-003 | Market service RPC | Elevation of privilege | Unauthorized internal caller reaches Market. | Workload can route to Market outside intended gateway path. | High | Medium | Network policy and Istio service-account policy. | Render/apply/negative tests not recorded. | Non-mesh/local environments rely on deployment discipline. |
| THR-004 | RabbitMQ publish | Tampering | Attacker injects or alters settlement command messages. | Broker credential or network path is compromised. | Critical | Medium | Broker credentials, network policy, internal topology. | Per-service broker authorization not verified. | Message signing/provenance not documented. |
| THR-005 | settlement-worker to trade-settlement | Elevation of privilege | Generic settlement operations are abused. | Worker, Market, broker path, or service identity is compromised. | Critical | Medium | Intended Istio allow policy and trade-settlement payload validation. | Negative service-call tests absent; no operation-provenance or operation-allow policy is implemented in trade-settlement. | Production blocker RISK-002. |
| THR-006 | PostgreSQL access | Tampering/disclosure | Database credential or egress path is abused. | Credential leak, broad egress, or compromised pod. | Critical | Medium | Secrets and network restriction. | DB destination restriction and credential scope not verified. | Broad DB egress and privilege model remain current gaps. |
| THR-007 | Secrets | Information disclosure | Database, broker, telemetry, or identity secrets leak or go stale. | Secret store access is too broad or rotation is missing. | High | Medium | Kubernetes Secret references. | Rotation, external manager, and access audit absent. | Secret lifecycle remains operational gap. |
| THR-008 | Observability | Repudiation | Incident cannot be reconstructed due to missing traces/logs. | Telemetry field propagation or dashboards are absent. | High | Medium | Settlement metadata and OTEL instrumentation. | Required fields, dashboards, and alerts not verified. | Incident response may depend on manual DB queries. |
| THR-009 | Production overlay | Misconfiguration | Example issuer/host/digests/secrets are deployed. | Release process does not reject placeholders. | High | High | README and deployment docs list required patches. | Release gate not implemented. | Production blocker RISK-008. |
| THR-010 | Settlement operation payload | Tampering | Valid RPC carries an operation sequence that is allowed by protobuf shape but unsafe for the intended trade flow. | Internal caller can assemble settlement operation variants. | Critical | Medium | Rust command conversion plus row-level operation preconditions. | No operation-provenance or operation-allow policy is implemented in trade-settlement. | Compromised Market/worker blast radius remains high. |

## Control Verification Table

| Control | Evidence needed to close gap | Status |
| --- | --- | --- |
| JWT issuer/JWKS/audience patched | Render production overlay and inspect `RequestAuthentication`. | Gap recorded. |
| Actor ID claim binding | Unit/e2e tests proving request actor IDs match verified claims. | Gap recorded. |
| Strict mTLS enabled | Render/apply Istio `PeerAuthentication` and verify mesh policy. | Partially documented. |
| Service-account allow rules | Render/apply Istio `AuthorizationPolicy`; test disallowed paths. | Partially documented. |
| Network default deny | Render/apply Kubernetes NetworkPolicy; test disallowed paths. | Partially documented. |
| Settlement API isolated | Verify only settlement-worker can call trade-settlement in production namespace. | Partially documented. |
| Broker access restricted | Verify per-service broker permissions. | Gap. |
| Secrets rotation | Verify external secret provider or documented rotation process. | Gap. |
| Operation provenance or allow policy | Evidence would need to show caller identity or command provenance constrains settlement operation families. | Not implemented in current trade-settlement code. |
| Placeholder release gate | Run CI/release validation that rejects example hosts, issuer values, emails, zero digests, and missing secrets. | Gap recorded. |

## Threat Model Assertions

| Assertion | Enforcement tag | Evidence or gap |
| --- | --- | --- |
| Actor spoofing is the highest-priority security gap. | Gap recorded | Risk register marks identity binding critical. |
| Mesh and network controls are the current documented settlement access controls. | Partially enforced | Application-level claim binding and operation-provenance controls are not implemented. |
| A compromised Market path is high impact. | Gap recorded | Market can construct generic settlement operations. |
| Production placeholder replacement is documented but not enforced by a checked-in gate. | Gap recorded | Deployment view lists current placeholders. |
