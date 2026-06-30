# V8 test-reliability remediation change log

Date: 2026-06-30

Branch: `experimental`

Scope: remediation of every finding recorded in `changes/v8/flaws.md`. The audit file was used as the input and was not edited during remediation. No checkout, commit, merge, reset, or write was performed on `main`.

## Finding-by-finding resolution

| Finding | Resolution |
|---|---|
| C-001 | Replaced the frontend-wide identity assertion with a server-side key-ID-to-capsuleer keyring. The API gateway now verifies a principal-specific HMAC and rejects any actor field that does not match the authenticated capsuleer. Added unit and full UDP E2E impersonation tests. Local/integration actors 1001, 2002, and 3003 now each have distinct credentials. |
| C-002 | Added 17 Rust tests across checksum, command conversion/validation, error mapping, operation mapping, overflow boundaries, and property-generated arithmetic/quantity inputs. Rust CI now runs all targets/features, coverage, clippy, and audit. |
| C-003 | Added the canonical Quilkin and simulator services to Dagger integration, wired every required URL/credential, enabled production-gate mode, and made skipped E2E tests fail. |
| C-004 | Made every failed GUI check and severe health/log finding terminate the runner nonzero. Removed unconditional integrity claims and emit them only when the corresponding checks pass. |
| H-001 | Changed hostile-fact helpers to put conflicting price, item type, station, and seller fields on the wire; the tests now prove those supplied facts are ignored in favor of canonical trade state. |
| H-002 | Removed misleading “cannot receive more” names and replaced them with exact transfer/arithmetic assertions plus direct low-level over-release and over-payment rejection tests. |
| H-003 | Renamed SQL-backed tests to describe persisted state rather than player-visible behavior. No SQL assertion is presented as UI/discovery evidence. |
| H-004 | Added barriers to force overlap, required the losing result to be `RpcFailure` with the precise code, and asserted exact quantities, balances, escrow, and trade state. CI repeats concurrency/load-sensitive cases three times. |
| H-005 | Every `expect_rpc_error` call now requires and checks the expected error code and relevant message. Rollback tests also inspect failed batch/idempotency diagnostics where applicable. |
| H-006 | Replaced truthiness defaults with `is None` handling so `0`, blank strings, and other malformed falsey values actually reach production code. |
| H-007 | Added `EVE_TRADE_E2E_PRODUCTION_GATE`; in that mode any skip fails the session. GitHub, Dagger, and integration Compose enable it. |
| H-008 | Removed the duplicate `test_gateway_*_end_to_end` block and replaced the stale hand-maintained README list with the authoritative collection command and an explicit warning that test count is not independent-risk count. |
| H-009 | Added real UDP socket/listener tests, worker shutdown/drain, oversized-packet rejection, queue overflow, startup/config validation, signed response checks, and authenticated-principal rate-limit tests. |
| H-010 | Added simultaneous duplicate, replay conflict, cache expiry, lost response write, safe transient retry, and durable Market replay tests. E2E verifies the durable settlement count and response identity on retries. |
| H-011 | Added RabbitMQ client/session/reply/config tests and a required live-broker contract covering success, business rejection, malformed delivery, publisher confirms, unroutable reply handling, acknowledgement, correlation metadata, and graceful worker shutdown. |
| H-012 | Made `Ping` mandatory on `SettlementExecutor`; readiness can no longer silently pass for an executor whose dependency health cannot be established. |
| H-013 | Added a restricted runtime PostgreSQL role, changed production services to use it, retained the privileged role only for migration/fixtures, and added an E2E assertion that runtime SQL works but schema creation is denied. |
| H-014 | Added symmetric update/delete immutability tests for both item and wallet ledgers in addition to remaining-quantity drift checks. |
| H-015 | Added an upgrade/rerun migration harness that applies the migration, writes a preservation marker, reapplies it, verifies the marker, and establishes/rechecks runtime grants. Compose and Dagger use this harness. |
| H-016 | Added a pull-request `buf breaking` gate against the target branch while retaining lint, format, generation, build, and generated-code drift checks. |
| H-017 | Removed `-ignore-missing-schemas`; both CI implementations validate production, local, observability, and chaos renders strictly against Kubernetes schemas plus a commit-pinned CRD catalog. |
| H-018 | Added Litmus HTTP probes and a required authenticated trade probe before, continuously during, and after chaos. Chaos now affects 50% of selected pods, requires Litmus pass verdicts, and verifies recovery. |
| H-019 | Production deployment now requires an authenticated external trade smoke after rollout, performs three accepted requests, and rolls back every application deployment if the smoke fails. |
| H-020 | Added a gating `gui:test` command and GitHub browser job that starts the canonical topology, installs pinned Chromium/Playwright tooling, fails on any runner check, and uploads evidence. |
| H-021 | Replaced “production-identical” claims with versioned repository-protocol conformance. Added a JSON Schema and a cross-language golden packet consumed by Django and Go tests. |
| H-022 | API gateway UDP responses are now canonicalized and HMAC-signed. The simulator verifies signature, key ID, expected source endpoint, envelope shape, and interaction ID. Added forged-signature and wrong-source tests. |
| H-023 | Added a concurrent authenticated UDP burst/SLO gate with at least 10 requests, configurable p95 budget, exact acceptance count, and item/ISK conservation checks. |
| H-024 | Added a Go fuzz target for authenticated-principal binding, retained a generated regression corpus case, and added Rust `proptest` properties including signed-64-bit boundaries. CI fuzzes for a bounded duration. |
| M-001 | Added Go, Rust, simulator, and observability coverage thresholds and artifacts. Current local direct coverage is API gateway 59.1%, Market 33.9%, messaging 28.1%, settlement worker 42.9%, Go observability 44.4%, simulator 80% branch coverage, and Python observability 35% branch coverage. |
| M-002 | Both GitHub and Dagger now execute all 14 observability tests with strict coverage; collection is not accepted as execution. |
| M-003 | Expanded simulator retry coverage to final decoded response, response authentication, retry exhaustion, and unexpected source behavior while asserting identical retry payloads. |
| M-004 | The GUI refresh check now requires exactly one committed interaction; zero completed requests no longer passes as idempotent success. |
| M-005 | Expanded fatal scanning to panic/fatal, OOM, unhandled exceptions, stack traces, container health, exit state, and restart counts; any severe finding fails the runner. |
| M-006 | Replaced marker-only boundary checks with Python AST inspection for live test functions/assertions and rendered-resource checks for simulator exclusion and image references. |
| M-007 | Replaced literal mutable-tag scanning with structured rendered-image validation requiring valid `@sha256:` references in production deployment paths. |
| M-008 | Pinned GitHub Actions to commits, runners to Ubuntu 24.04, Go/Rust/Python/tool versions, Python direct requirements, base images to digests, Terraform provider constraints to exact versions, and Rust to `Cargo.lock`. Added a checked-in Go workspace vendor tree and vendor-drift gates; production Go image builds are offline with `-mod=vendor`. |
| M-009 | GitHub and GitLab/Dagger now scan complete Git history with Gitleaks, fail on unfixed HIGH/CRITICAL findings, audit every Python requirements set, prove the ignored Rust advisory is unreachable, scan final service images, and export CycloneDX SBOMs. |
| M-010 | Brought Dagger gates to parity: Go vet/race/staticcheck/govulncheck/fuzz/coverage/live RabbitMQ, Rust all-target/all-feature clippy/test/coverage/audit, executable Python coverage/audits, strict Kubernetes validation, Terraform tests, final-image scanning, and SBOMs. |
| M-011 | Added explicit GitHub job timeouts and a three-pass repeat gate for concurrency/load-sensitive E2E tests; removed `--maxfail=1` from the full suite. |
| M-012 | Added `GatewayClient.close()` and fixture teardown so each function-scoped `httpx.Client` is closed. |
| M-013 | Removed the stale static E2E catalog. GUI evidence now records commit, dirty state, CI identity, lock hash, image identities, Node, and Playwright versions; old checked-in failed artifacts are explicitly labeled historical and non-gating. |
| M-014 | Added representative `terraform test` plan assertions with mocked providers for EKS, GKE, and Talos/Omni, and run them in both CI implementations in addition to format/init/validate. |
| M-015 | Added strict validation for production overlays, digest-only deployment, authenticated post-deploy smoke, and external authenticated chaos probes so release gates cross the production ingress/policy boundary. |
| M-016 | Added Rust maximum/minimum `int64`, overflow, invalid UUID, operation consistency, checksum, and command-boundary tests plus E2E low-level over-release/over-payment and ledger mutation tests. |
| M-017 | Tightened worker readiness to require executor ping and nil-dependency failure, added readiness/config tests, and made post-deploy/chaos gates require a real authenticated trade rather than accepting rollout/TCP readiness as functional evidence. |
| M-018 | Added real API UDP listener cancellation with queued-work drain and a live RabbitMQ worker cancellation/drain assertion. |

## Additional defects found while validating the fixes

- The simulator returned HTTP 202 even when the authenticated UDP response contained a business error. It now maps error codes to non-success HTTP statuses, persists the interaction as failed, returns the precise code/message, and has unit/E2E coverage. The E2E rejection test now requires HTTP 400 for `invalid_argument` and compares settlement-batch count to its per-test baseline.
- Browser validation then exposed that the QA parser only understood nested success payloads and the page overwrote its specific malformed-JSON message with a generic exception. The parser now accepts top-level HTTP error payloads, and the page preserves the actionable client-validation message.
- The first load implementation reused a mutating stack while claiming its original quantity in every concurrent packet. It now seeds one canonical one-unit stack per request, so concurrency does not manufacture stale client facts.
- Negative and contention E2E paths use capsuleer 3003. That actor now has its own edge credential so those requests reach the business rule being tested instead of failing early due to missing simulator configuration.
- Dagger’s Gitleaks command still used `--no-git`. It now mounts `.git`, scans history, and GitLab fetches with `GIT_DEPTH=0`.

## File-by-file implementation inventory

### CI, release, and evidence

- `.github/workflows/verify.yaml`: pinned actions/runners/tools; added timeouts, Buf breaking, coverage gates/artifacts, Go fuzz/static/security checks, Rust coverage/audit, Terraform behavior tests, strict Kubernetes/CRD validation, executable Python suites, live RabbitMQ, strict E2E repetition, gating browser tests, full-history secret scanning, and unfixed vulnerability failure.
- `ci-cd/pipeline.py`: implemented GitHub-equivalent checks, strict schema validation, canonical Dagger integration topology, runtime DB role, migration-upgrade harness, final-image scans/SBOMs, digest publication/rendering/deployment, post-deploy smoke/rollback, and continuous chaos probes.
- `ci-cd/gitlab/eve-trade.gitlab-ci.yml`: enabled full Git history, transferred digest artifacts through deploy stages, added the chaos stage/job, and retained all gates as required.
- `ci-cd/README.md`: corrected gate descriptions and documented digest, smoke, rollback, and chaos-probe requirements.
- `package.json`: added the gating `gui:test` entry point.
- `scripts/gui-simulator-demo.cjs`: made failures fatal, tightened refresh/health assertions, conditioned integrity claims, added source/build provenance, and added `GUI_ARTIFACT_ROOT` so validation can write evidence outside the tracked historical bundle.
- `artifacts/gui-simulator-demo/README.md` and `artifacts/gui-simulator-demo/report.md`: marked the old failed run as historical, non-gating evidence rather than current reliability proof.
- `Architecture/ISO-42010/00-architecture-description.md`, `08-development-validation-view.md`, `09-correspondences-rationale.md`, `18-evidence-manifest.md`, and `19-architecture-facts.md`: removed overstated production-identity/test claims and aligned documented evidence with the new executable gates.
- `README.md`: replaced the production-identical claim with repository-protocol conformance and documented authenticated-principal edge controls.

### Reproducible builds and image policy

- `vendor/`: generated the complete Go workspace vendor tree (`go work vendor`; 1,928 files including `vendor/modules.txt`) so production Go builds do not download modules.
- `distributed-backend/docker/api-gateway.Dockerfile`, `market.Dockerfile`, and `settlement-worker.Dockerfile`: copy all workspace module metadata/vendor content and build with `-mod=vendor`; base/runtime images are digest pinned.
- `distributed-backend/docker/quilkin.Dockerfile` and `trade-settlement.Dockerfile`: digest-pinned Rust/Debian images and locked builds.
- `simulator/Dockerfile`: digest-pinned Python image.
- `compose.yaml` and `docker-compose.integration.yml`: digest-pinned infrastructure/runtime images, principal keyrings, restricted runtime DB credentials, migration upgrade checks, production-gate E2E configuration, and load settings.
- `go_modules_test.go`: validates structured digest-only production image references/deployment templates rather than searching for three mutable tag strings.

### API gateway and authenticated UDP

- `distributed-backend/src/api-gateway/cmd/api-gateway/main.go`: treats configuration errors as fatal startup errors.
- `distributed-backend/src/api-gateway/distributed-backend/config.go`: validates required response signing and parses the principal keyring.
- `distributed-backend/src/api-gateway/distributed-backend/config_test.go`: covers missing/invalid/valid auth and keyring configuration.
- `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp.go`: verifies principal-bound requests, validates actor binding, rate-limits the authenticated principal, signs canonical responses, exposes real listener seams, handles bounded queue/packet behavior, and drains workers on shutdown.
- `distributed-backend/src/api-gateway/distributed-backend/quilkin_udp_test.go`: adds socket, shutdown, overflow, oversized packet, impersonation, signatures, concurrent replay, expiry, lost response, retry, principal-rate-limit, golden protocol, and fuzz coverage.
- `distributed-backend/src/api-gateway/distributed-backend/testdata/fuzz/FuzzAuthenticatedPayloadNeverAcceptsAnUnboundPrincipal/7294cf71745d21fe`: retained fuzz-discovered regression corpus input.
- `distributed-backend/protocol/eve-trade-gui-v1.schema.json` and `protocol/fixtures/sell-order.packet.json`: added the versioned protocol oracle and shared golden packet.

### Simulator and GUI

- `simulator/eve_trade_simulator/settings.py`: added capsuleer-specific signing-key configuration.
- `simulator/trade_gui/udp_client.py`: selects principal credentials, authenticates responses, verifies response source and interaction correlation, and tightens retry behavior.
- `simulator/trade_gui/views.py`: preserves external request identity and propagates UDP errors as precise non-success HTTP outcomes.
- `simulator/trade_gui/tests.py`: validates schema/golden conformance, signed requests/responses, retry completion/exhaustion, forged responses, wrong sources, error-to-HTTP mapping, interaction state, and UI automation contracts.
- `simulator/trade_gui/templates/trade_gui/index.html`: uses real keyboard-accessible buttons, prevents double submission while in flight, propagates external request IDs, and removes the false role-visibility claim.
- `simulator/requirements.txt`, `requirements-test.txt`, and `.coveragerc`: pin runtime/test dependencies and define strict branch coverage.

### Messaging, readiness, and observability

- `distributed-backend/src/messaging/rabbitmqsettlement/worker.go`: requires executor readiness instead of failing open.
- `worker_test.go`, `client_test.go`, `config_test.go`, `reply_test.go`, and `rabbitmq_integration_test.go`: cover readiness, pending dispatch/failure, metadata, confirms/NACKs, returns, reply decoding, live delivery/rejection/error/ack paths, and shutdown.
- `distributed-backend/src/settlement-worker/cmd/settlement-worker/health.go`: reports nil/unready dependencies as HTTP 503.
- `config_test.go` and `health_test.go`: add direct worker configuration and probe coverage.
- `distributed-backend/src/observability/observability_test.go`: adds direct Go tracing/metrics/resource coverage.
- `observability/requirements.txt`, `requirements-test.txt`, and `.coveragerc`: pin deployed/test dependencies and define branch-coverage execution.

### Rust settlement

- `distributed-backend/src/trade-settlement/Cargo.toml` and `Cargo.lock`: add exact `proptest` test dependency and lock its transitive graph.
- `src/checksum.rs`: checksum determinism/domain/tamper tests.
- `src/commands.rs`: conversion, required-field, UUID, operation-kind, invalid input, overflow, and property tests.
- `src/error.rs`: stable gRPC/code mapping tests.
- `src/operations.rs`: operation-name/payload and boundary tests.

### E2E, database, and migrations

- `distributed-backend/tests/e2e/conftest.py`: requires all production-gate dependencies, fails on any skip, closes HTTP clients, and exposes the authenticated edge fixture.
- `helpers.py`: sends falsey and hostile values faithfully, closes clients, signs direct UDP packets, and verifies signed/source-bound responses.
- `test_trade_lifecycle.py`: removes duplicate cases, corrects misleading names, sends adversarial facts, requires exact negative outcomes, strengthens races, adds authenticated impersonation/runtime-role/symmetric-ledger/boundary assertions, and verifies HTTP failure propagation.
- `test_load_slo.py`: adds concurrent authenticated UDP p95 and conservation gating over independent canonical stacks.
- `requirements.txt`: pins pytest, repeat, HTTP, PostgreSQL, and gRPC dependencies exactly.
- `README.md`: replaces the stale test-name catalog with collection instructions and honest scope language.
- `distributed-backend/tests/migrations/verify_upgrade.sh`: verifies existing-data preservation, rerun safety, and runtime-role grants.

### Kubernetes and chaos

- `distributed-backend/orchestration/kubernetes/base/api-gateway.yaml`: supplies principal keyring configuration.
- `base/migrate.yaml` and `base/rabbitmq.yaml`: use digest-pinned images.
- `overlay/local/postgres.yaml`, `local-dev-world-seed.yaml`, `secrets.yaml`, and `simulator.yaml`: use pinned images, runtime credentials, and distinct actor keyrings.
- `chaos/litmus/base/pod-delete-engines.yaml`: adds continuous authenticated HTTP probes and changes disruption from zero to 50%.
- `chaos/litmus/README.md`: documents functional continuity prerequisites and semantics.

### Terraform

- `distributed-backend/terraform/eks/versions.tf`, `gke/versions.tf`, and `talos-omni/versions.tf`: replace provider ranges with exact versions.
- Each root’s `tests/production.tftest.hcl`: adds mocked-provider production-plan assertions for naming, topology, protection, and deployment inputs.

### Architecture guard

- `scripts/verify_architecture_boundaries.py`: parses Python/YAML structures, verifies executable assertions and production render constraints, and rejects simulator leakage/digest violations semantically.

## Validation performed

- Branch check: `experimental`.
- Fresh-volume production-gate Compose E2E: **104 passed, 0 failed, 0 skipped**.
- Three-pass concurrency/load repeat gate: **9 passed, 303 deselected**.
- Django simulator: **9 passed**.
- Headless Chromium GUI reliability gate: **57 passed, 0 failed**; fatal/OOM/container-health scan clean.
- Rust: **17 passed** with `cargo test --locked --all-features`; formatting passed.
- Python observability: **14 passed**.
- Root/workspace Go tests: passed; per-module coverage exceeded every configured threshold.
- API gateway fuzz target: passed two bounded runs with more than 240,000 executions per run; retained corpus case passes.
- Protocol collection: 104 E2E tests collected; every `expect_rpc_error` call supplies an expected code.
- Python compilation, Node syntax, architecture guard, both Compose renders, Terraform recursive format, and workflow YAML parse: passed.
- Production-like Go service images built successfully from the checked-in vendor tree with no Go module download.

## Environment-limited validation

- Terraform provider lock generation and provider-backed `terraform test` could not complete locally because `releases.hashicorp.com` returned 404 for provider checksum requests. Exact provider constraints and mocked test files are checked in; CI is configured to run init/validate/test and will fail if providers cannot be resolved. No fabricated lock hashes were added.
- Local kubeconform execution was blocked by external registry/schema access. Both CI implementations contain strict validation with no missing-schema bypass and a commit-pinned CRD catalog.
- Post-deploy and chaos probes require production kubeconfig, ingress URLs, and bearer tokens; their code paths were not executed against an external cluster in this workspace.
