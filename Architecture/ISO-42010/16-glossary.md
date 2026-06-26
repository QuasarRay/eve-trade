# Glossary

## ISO 42010 Terms As Used Here

| Term | Meaning in this document set |
| --- | --- |
| Architecture description | The complete set of documents under `Architecture/ISO-42010` that describes the `eve-trade` architecture. |
| Entity or system of interest | The `eve-trade` distributed trade backend. |
| Stakeholder | A role or group with concerns about the system architecture. |
| Concern | An interest in the system architecture that must be addressed by one or more views. |
| Stakeholder perspective | A grouping of concerns used to organize viewpoints. |
| Architecture aspect | A cross-cutting topic that appears in multiple views, such as idempotency or trust boundaries. |
| Architecture viewpoint | A specification for constructing, interpreting, and analyzing one or more views. |
| Architecture view | A representation of the architecture governed by one or more viewpoints. |
| View component | A separable part of a view, such as a diagram, table, matrix, state model, register, or runbook fragment, that can be referenced by ID. |
| Model kind | A type of architecture model, such as a context diagram, sequence diagram, invariant catalog, or risk register. |
| Correspondence | A recorded relationship between architecture description elements in different views. |
| Rationale | The reasoning, alternatives, and consequences behind architecture decisions. |
| Architecture Description Framework | The project-specific framework that defines stakeholders, concerns, viewpoints, model kinds, and governance rules. |
| Architecture Description Language | The Markdown, Mermaid, and table conventions used to express the architecture description. |
| Evidence level | A documented strength of support for a claim, from no evidence through path, symbol, test, current pass, and stakeholder acceptance. |
| Residual risk | Risk that remains after current controls and mitigations are considered. |
| Release gate | A required validation or approval condition that must pass before a release or production-readiness claim. |

## Project Terms

| Term | Meaning |
| --- | --- |
| API Gateway | Go UDP edge and UDP-to-gRPC forwarder. It validates transport safety and forwards raw game GUI payloads to Market; it does not expose production direct trade command RPCs. |
| Quilkin | UDP proxy/routing component between game frontend traffic and the API Gateway UDP listener. |
| Edge envelope | Signed `eve-trade-edge.v1` UDP JSON envelope carrying the raw game GUI payload and HMAC authentication data. |
| GUI interaction payload | Production game packet payload with `schema_version`, `interaction_id`, `ui`, and `input` fields. The local simulator and real frontend use the same shape. |
| Market | Go service that interprets game GUI interactions, performs trade mechanics, validation, settlement planning, and settlement command publication. |
| settlement-worker | Go worker that consumes RabbitMQ settlement commands and calls trade-settlement. |
| trade-settlement | Rust service that atomically executes requested settlement batches and applies durable PostgreSQL mutations plus settlement metadata. |
| Settlement batch | A set of ordered settlement operations executed under one idempotency key. |
| Settlement step | One operation within a settlement batch, recorded for audit and diagnosis. |
| Settlement operation | A protobuf-defined mutation command handled by trade-settlement. |
| Idempotency key | Caller-supplied key used to prevent duplicate settlement effects and support replay. |
| Request fingerprint | Hash or derived representation of request material used to detect key reuse for different payloads. |
| External request ID | Upstream or boundary-generated correlation identifier. |
| Capsuleer | Game-domain actor/player identity used by trade, wallet, and item ownership rules. |
| Item stack | A quantity of a specific item type owned by a capsuleer at a station/location. |
| Wallet | A capsuleer's ISK balance container. |
| Escrow | A temporary holding record for items or ISK during trade settlement. |
| Ledger | Append-only audit record for item, wallet, or trade state changes. |
| DLQ | Dead-letter queue for RabbitMQ settlement messages that cannot be processed normally. |
| DLX | Dead-letter exchange used by RabbitMQ to route dead-lettered settlement messages. |
| Quorum queue | RabbitMQ replicated queue type used for durable settlement command and dead-letter queues. |
| RTO | Recovery Time Objective: the target time to restore service or data after an outage. |
| RPO | Recovery Point Objective: the maximum acceptable data-loss window after a failure. |
| h2c | HTTP/2 without TLS, acceptable only in trusted local or mesh-protected paths where explicitly allowed. |
| OTEL | OpenTelemetry instrumentation and collector/export pipeline. |
| mTLS | Mutual TLS, where both client and server identities are authenticated through TLS certificates. |
| Service account principal | Mesh or Kubernetes identity representing a workload for authorization decisions. |
| AuthorizationPolicy | Istio resource that allows or denies traffic based on workload identity, request properties, and paths. |
| PeerAuthentication | Istio resource that configures peer authentication mode, including strict mTLS. |
| Break-glass access | Emergency operational access path that bypasses normal flow only with approval and audit. |
| Redrive | Operational act of moving a dead-lettered message back to a processing path after it is classified as safe. |
| Placeholder gate | Release validation that blocks example hosts, issuer values, emails, zero image digests, and missing secrets. |
