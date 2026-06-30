# Test Reliability and CI/CD Flaws Audit

Audit date: 2026-06-29

Scope: all tracked unit, integration, E2E, GUI/demo, architecture-guard, infrastructure-validation, and CI/CD test surfaces in the repository. This is a test audit, not a general implementation audit. No intent is inferred from misleading names or evidence; “misleading” below means the check proves materially less than its name, documentation, or success presentation implies.

## Executive verdict

The repository has a large-looking E2E suite and broad-looking CI workflow, but it is not a sufficient production-readiness gate.

The most serious problems are:

- the “authorization” E2E tests validate caller-supplied IDs rather than authenticated identity;
- the correctness-critical Rust settlement crate reports a successful `cargo test` while running **zero tests**;
- several E2E test names claim adversarial protections that the test never attempts;
- the GitLab/Dagger “integration” topology cannot run the checked-in simulator-driven E2E suite and does not contain the canonical simulator/Quilkin path it claims to test;
- the Playwright QA runner records failed checks but still exits successfully and prints unconditional integrity claims;
- there is no coverage gate, mutation testing, fuzzing, load/soak testing, or production post-deploy smoke test.

The passing checks do provide useful evidence for ordinary single-process happy paths and many PostgreSQL invariants. They do not justify a production-ready or production-reliable conclusion.

## Audit measurements

The following diagnostic commands were run without changing project source:

- `go test -count=1 -cover ./...` in every Go module;
- `go tool cover -func` for API Gateway, Market, and messaging;
- `cargo test --locked -- --list` in `trade-settlement`;
- `python -m pytest distributed-backend/tests/e2e --collect-only -q`;
- `python -m unittest discover -s observability/tests -v`.

Observed direct unit-test coverage:

| Module | Statement coverage observed | Important qualification |
|---|---:|---|
| `src/messaging` | 5.0% | RabbitMQ client, worker delivery handling, confirms, topology, ack/nack, and reconnect paths are at 0%. |
| `src/market` | 33.9% module-wide | Command startup, real repository methods, real transport clients, server probes, cancellation handling, and several validation paths are at 0%. |
| `src/api-gateway` | 38.9% module-wide | Real listener/worker/queue, startup/config, HTTP probes, and real Market client are at 0%. |
| `src/settlement-worker` | 0.0% | Its package has no direct tests. |
| Go observability module | 0.0% | No direct Go tests. |
| generated proto module | 0.0% | CI calls `go test`, but there are no tests in these packages. |
| Rust `trade-settlement` | 0 tests | `lib.rs`: 0; `main.rs`: 0; doc tests: 0. |

The E2E package collects 109 tests. The E2E README lists only 92 names and is stale. The Python observability suite has 14 passing tests, but neither checked-in CI pipeline runs it.

These coverage numbers measure direct Go unit tests only; the live E2E path exercises additional code without instrumentation. Their significance is that CI has no coverage threshold and can keep reporting success after direct coverage falls to these levels or lower.

## Critical findings

### C-001: “Authorization” tests do not test authenticated authorization

Severity: Critical

The E2E client sends `issuedByCapsuleerId`, `buyerCapsuleerId`, and `cancelledByCapsuleerId` as ordinary request data (`helpers.py:758-772`, `818-825`, `847-852`). Tests such as:

- `test_player_cannot_offer_item_stack_owned_by_another_player`;
- `test_player_cannot_cancel_trade_created_by_another_player`;
- `test_cancelling_trade_rejects_non_seller_caller`;
- `test_accepting_trade_rejects_when_buyer_is_seller_if_self_purchase_is_disallowed`;

only prove that one self-asserted ID is compared with another ID or a database owner. They never authenticate a player, bind the actor ID to a token/session/connection claim, or attempt the real attack: submit the victim’s capsuleer ID.

The shared HMAC authenticates possession of a frontend-wide edge secret, not the individual capsuleer. A caller that can submit a valid game packet can assert the owner’s ID. Presenting these tests as identity or authorization evidence materially overstates what is enforced.

Production gate required: authenticated principal injection at the real edge, negative tests where a validly authenticated player asserts another capsuleer ID, and tests through the production mesh/JWT policy—not just request-field consistency tests.

### C-002: `cargo test` is a green empty test suite for the correctness-critical settlement service

Severity: Critical

`distributed-backend/src/trade-settlement` contains no `#[test]` or async test functions. `cargo test --locked -- --list` reported:

- `src/lib.rs`: 0 tests;
- `src/main.rs`: 0 tests;
- doc tests: 0.

Nevertheless, `.github/workflows/verify.yaml:248-249`, `ci-cd/pipeline.py:367-379`, the architecture validation view, and the evidence manifest all present “Rust tests” as a passing gate. This is command success, not test evidence.

The untested direct surface includes protobuf-to-command conversion, request validation, transaction/savepoint behavior, idempotency conflict and replay reconstruction, checksums, all operation handlers, ledger writes, overflow handling, error mapping, and failure recovery. E2E covers selected sequences but cannot replace focused branch and fault tests for the financial state machine.

### C-003: The GitLab/Dagger E2E gate cannot execute the suite it claims to run

Severity: Critical

`ci-cd/pipeline.py:847-1015` creates PostgreSQL, RabbitMQ, trade-settlement, settlement-worker, Market, and API Gateway. It creates neither the simulator nor Quilkin. It also never sets `EVE_TRADE_SIMULATOR_URL`.

The E2E session fixture requires all of `EVE_TRADE_API_GATEWAY_URL`, `EVE_TRADE_SIMULATOR_URL`, and `EVE_TRADE_DATABASE_URL` (`conftest.py:28-37`). Every test therefore skips in this isolated Dagger container, and the all-skipped guard makes the job fail. Even if the missing URL were added, there is no simulator or Quilkin service to receive it.

This contradicts `ci-cd/README.md`, which says Dagger integration runs the message-driven E2E suite, and contradicts the documented canonical path. Because build/publish/deploy depend on this GitLab job, the checked-in GitLab release pipeline is not demonstrated to be runnable as written.

The Dagger test container also sets `EVE_TRADE_MARKET_GRPC`, `EVE_TRADE_GATEWAY_GRPC`, and `EVE_TRADE_E2E_PRODUCTION_GATE`, but the E2E code reads none of them. Those variables create an appearance of stricter/direct endpoint coverage without affecting a test.

### C-004: The Playwright QA runner can report failed checks and still return success

Severity: Critical

`scripts/gui-simulator-demo.cjs:104-107` records a false check but does not throw or set a nonzero exit code. `main()` completes normally regardless of the number of recorded failures. The checked-in run demonstrates this behavior: 53 pass, 4 fail, yet the runner produced a “complete” evidence bundle.

Worse, its final HTML unconditionally says “Total wallet ISK conserved,” “Total item quantity conserved,” and “No duplicate settlements observed” (`scripts/gui-simulator-demo.cjs:634-643`) instead of conditioning those statements on the recorded results. Its severe-log scan only writes matching lines and never fails (`346-351`).

This script is named a demo/manual-QA draft rather than a CI test, but its screenshots, video, report, and “coverage report” wording are used as reliability evidence. Any automated consumer sees process success even when checks fail. It must not be treated as a production gate.

## High-severity test flaws

### H-001: Four “client-supplied fact” tests never send the hostile facts in their names

Severity: High

The following tests execute an ordinary happy-path acceptance:

- `test_trade_acceptance_uses_trade_price_not_client_supplied_price`;
- `test_trade_acceptance_uses_trade_item_type_not_client_supplied_item_type`;
- `test_trade_acceptance_uses_trade_station_not_client_supplied_station`;
- `test_trade_acceptance_uses_trade_seller_not_client_supplied_seller`.

They do not submit a conflicting price, item type, station, or seller (`test_trade_lifecycle.py:1000-1035`). The `accept_payload` helper explicitly accepts arbitrary `**_ignored_client_facts` and discards them before the request goes over the wire (`helpers.py:805-826`). The equivalent cancel helper does the same (`838-852`).

A decoder that accidentally began trusting an unknown hostile field could regress while these tests stayed green. These names claim a negative security property that is not exercised.

### H-002: Two “cannot receive more” tests only prove the normal amount was received

Severity: High

`test_buyer_cannot_receive_more_items_than_requested` requests 3 and confirms 3 arrived. It does not attempt an over-credit, malicious destination, duplicate operation, or corrupted settlement request (`test_trade_lifecycle.py:982-988`).

`test_seller_cannot_receive_more_isk_than_trade_price_requires` performs a normal 4 × 25 trade and checks the expected balance (`991-997`). It does not attempt to overpay the seller.

Both are happy-path arithmetic checks presented as adversarial invariant tests.

### H-003: SQL inspection is mislabeled as player-visible behavior

Severity: High

`test_creating_trade_offer_exposes_trade_as_outstanding_to_other_players` never uses another player or a read/list API; it queries `trade_instance` directly (`test_trade_lifecycle.py:52-59`). `test_accepting_partial_trade_updates_visible_available_quantity` also reads SQL directly (`217-223`). Cancellation “unavailable to buyers” checks likewise inspect internal state.

These tests prove persistence, not discoverability, visibility, access filtering, serialization, caching, or client rendering. There is no player-facing market listing path under test. The names give the audience stronger product evidence than the assertions provide.

### H-004: Concurrent tests accept arbitrary infrastructure/software exceptions as the desired loser

Severity: High

Both concurrency tests catch broad `Exception` and count it as a valid failed competitor (`test_trade_lifecycle.py:758-834`). They do not require `RpcFailure`, an expected status code, or a contention-specific error. A timeout, JSON decode failure, simulator error, connection reset, process crash, or unrelated 500 can satisfy the one-success/one-failure assertion.

The first test then uses weak inequalities (`owned delta <= 10`, escrow `>= 0`) instead of the exact committed outcome, exact conservation, exact remaining quantity, exact ledger entries, and expected failed request state. Neither test uses a barrier to ensure overlap, repeats the race, varies concurrency, or runs under load. They are single-shot race demonstrations, not concurrency assurance.

### H-005: Many rollback/rejection tests pass on the wrong failure

Severity: High

`expect_rpc_error` only checks code/message when the caller supplies them (`helpers.py:864-880`). A large block of tests omits both, including failed-create tests, failed-accept balance/item/state tests, failed-cancel tests, and some “end-to-end rejection” duplicates (`test_trade_lifecycle.py:593-758`, `1210-1245`).

If Market, RabbitMQ, settlement-worker, trade-settlement, or the database were unavailable, the request would fail and state would remain unchanged—the same observations these tests require. Such an outage can therefore make tests for business rollback pass. Every negative test must assert the precise failure family/code and, where relevant, the failed batch/attempt record.

`test_rejected_request_does_not_return_success_status` is especially weak: after a helper has already required failure, it merely asserts the error code is not one of three success-like strings (`1122-1129`). A 500, timeout, parser failure, or unrelated rejection passes.

### H-006: Test helpers prevent important malformed values from reaching production code

Severity: High

Payload helpers use truthiness fallbacks such as `value or world.seller_id`, `wallet_id or default`, `item_stack_id or default`, and `idempotency_key or fresh_key` (`helpers.py:744-852`). Consequently, tests cannot send `0`, `""`, or other falsey values for several required IDs and keys through these helpers.

This silently removes missing/blank actor, wallet, stack, and idempotency boundary cases from the 109-test suite. It also makes a test author believe an override was sent when the helper replaced it.

### H-007: Partial skips can still produce a green “E2E” run

Severity: High

The session hook fails only when every collected test is skipped (`conftest.py:15-25`). The six direct settlement contract tests skip independently when `EVE_TRADE_SETTLEMENT_GRPC` is missing (`65-74`). A run can therefore pass 103 tests, skip all privileged settlement contract tests, and still be green.

`EVE_TRADE_E2E_PRODUCTION_GATE=true` is set by Dagger but never read. There is no production mode that forbids any skipped critical test or verifies the expected test count/marker set.

### H-008: The E2E count is inflated by duplicates and relabeled copies

Severity: High

The `test_gateway_*_end_to_end` block (`test_trade_lifecycle.py:1132-1245`) uses the same `db` and `gateway` fixtures and same helper path as every preceding lifecycle test. It does not add a new gateway layer. Many earlier tests also split one transaction into separate single-assert tests using identical setup—for example partial-accept state, escrow quantity, visible quantity, and consistency.

This is not inherently invalid, but “109 E2E tests” is not 109 independent paths or risks. It materially exaggerates breadth when used as a readiness metric. Parameterized scenario matrices or invariant-oriented tests would communicate coverage more honestly.

### H-009: API Gateway unit tests bypass the reliability mechanisms named in architecture evidence

Severity: High

Most gateway tests call `handlePacket` or `allowRemote` directly with in-memory fakes (`quilkin_udp_test.go:121-290`). They bypass:

- `ListenAndServe` and real UDP sockets;
- worker goroutines and shutdown;
- bounded queue behavior and queue-full responses;
- max-packet enforcement;
- real downstream HTTP/Connect behavior and timeouts;
- actual Quilkin source-address behavior;
- startup/config parsing and readiness/liveness endpoints.

The test named `TestQuilkinUDPServerRateLimitsRemoteAddress` injects a public client address directly. In the deployed topology, the gateway receives packets through Quilkin; no test proves that the address used by the limiter remains a per-player address rather than a shared proxy address. The architecture view claims bounded concurrency, queue overflow, rate limit, HMAC, replay, timeout, and compact responses are covered by gateway tests, but several of those are not.

### H-010: Edge idempotency tests only prove sequential, single-process cache behavior

Severity: High

Lost-response, replay, and transient-retry unit tests use one server instance, one in-memory replay cache, sequential calls, and fake Market clients (`quilkin_udp_test.go:182-277`). They do not cover simultaneous duplicate packets, process restart, cache expiry, eviction, multiple gateway replicas, load-balancer redistribution, or a committed settlement followed by a lost RabbitMQ/UDP reply.

The E2E retries are also sequential and do not kill/restart components at the commit/reply boundary. Production idempotency claims need crash-window and redelivery tests against the durable layers, not only a warm in-memory cache.

### H-011: The RabbitMQ reliability implementation has effectively no direct behavioral tests

Severity: High

Messaging unit coverage is 5.0%. Tests cover only error-type recognition and executor readiness polling. At 0% direct coverage are the RPC client, session creation/invalidation, reply consumer/dispatch, pending-call failure, publisher confirms, returned messages, topology, worker session, delivery decode, executor call, success/error reply, ack/nack/requeue, concurrency, and reconnect loops.

The Compose E2E proves ordinary brokered requests. It does not restart RabbitMQ or the worker, force redelivery, drop reply queues, send malformed messages, produce confirm NACKs/returns, interrupt after commit before ACK, test duplicate delivery, validate DLQ behavior, or verify pending callers on connection loss.

Running `go test -race` does not compensate: the concurrent RabbitMQ code at 0% coverage is not executed by the race detector.

### H-012: Readiness deliberately fails open for executors without `Ping`

Severity: High

`TestWaitForExecutorReadySkipsExecutorWithoutPing` codifies that any `SettlementExecutor` lacking the optional readiness interface is considered immediately ready (`worker_test.go:48-52`, `worker.go:153-157`). The core interface itself does not require readiness.

The current production executor may implement `Ping`, but a future or alternate executor can silently remove dependency readiness and still pass this test. A production worker should not report readiness for an executor whose readiness cannot be established.

### H-013: Database/repository integration is broad but production database privilege is not tested

Severity: High

E2E uses PostgreSQL superuser credentials and directly inserts/truncates canonical tables (`helpers.py:214-267`, `359-743`; Compose uses `postgres/postgres`). This bypasses production database roles, grants, row ownership, migration-user/runtime-user separation, and least privilege.

The Market repository has no direct integration tests for query shape, no-row mapping, malformed UUIDs, duplicate rows, pool failure, or permission errors. Ordinary E2E calls exercise it, but they cannot establish that production credentials are restricted or sufficient.

### H-014: Database invariant tests cover only three hand-picked mutations

Severity: High

The direct database tests check remaining-quantity drift, one item-ledger update, and one wallet-ledger delete (`test_trade_lifecycle.py:1399-1438`). They do not test the symmetric item-ledger delete/wallet-ledger update cases, forged inserts, broken hash-chain inputs, projection/ledger divergence across every operation, active escrow uniqueness, cross-trade escrow linkage, version/checksum drift, deferred-constraint behavior at commit, or concurrent tampering.

The test suite therefore should not be read as comprehensive ledger immutability or audit-integrity proof.

### H-015: Migrations are tested only as fresh-schema installation

Severity: High

Both Compose and Dagger drop the public schema and apply `0001_settlement_schema.sql` from scratch. `TestKubernetesMigrationCopiesMatchSource` only compares file bytes (`go_modules_test.go:40-61`). No test covers applying the migration to an existing supported schema/data set, rerunning it, partial failure, locking, rollback, mixed service/schema versions, data preservation, backup restore, or Kubernetes migration-job retry.

A fresh install passing is not production migration safety.

### H-016: Protobuf CI has no breaking-change gate

Severity: High

CI runs `buf build`, lint, format, generate, and generated-code drift checks, but never runs `buf breaking` against `main`, a tag, or a registry. A wire-incompatible field/service change can pass all protobuf jobs. Compiling generated code is presented as “proto tests” despite 0% test coverage.

### H-017: Kubernetes “strict” validation deliberately skips the most production-specific schemas

Severity: High

GitHub invokes kubeconform with both `-strict` and `-ignore-missing-schemas` (`verify.yaml:321-327`). Resources without a bundled schema—commonly Istio, cert-manager, OpenTelemetry, Gateway API extensions, and Litmus CRDs—are skipped rather than validated. Those are a large part of the production platform surface.

GitLab/Dagger is weaker still: `validate_kubernetes_render` and `validate_chaos_render` only call Kustomize and read the output (`pipeline.py:605-635`); they perform no schema validation at all.

Neither pipeline performs server-side dry-run against supported Kubernetes/CRD versions, policy tests, NetworkPolicy reachability tests, or a cluster deployment test before production.

### H-018: The chaos test does not test service continuity

Severity: High

The ChaosEngine annotations claim service recovery/continuity, but the engines define no Litmus probes (`pod-delete-engines.yaml`). A Litmus `Pass` establishes that pod deletion executed, not that a trade request succeeded, latency stayed within budget, no duplicate settlement occurred, or data remained consistent.

After chaos, the pipeline only waits for Deployment rollout status (`pipeline.py:817-822`). It does not run the business E2E path or inspect ledger/conservation invariants. RabbitMQ, settlement-worker, PostgreSQL, network partitions, latency, disk, and in-flight commit/reply failures are not in the suite. `PODS_AFFECTED_PERC` is configured as `0`, with no assertion of how many pods were actually disrupted.

### H-019: Production deploy has no post-deploy functional verification

Severity: High

The GitLab deploy step applies manifests and waits for StatefulSet/Deployment rollout (`pipeline.py:637-679`). It performs no DNS/TLS check, external Gateway request, authenticated game packet, trade lifecycle smoke, database invariant query, telemetry check, rollback verification, or canary analysis.

Kubernetes “Ready” is not evidence that the end-to-end trade path works. This is especially weak because trade-settlement readiness is TCP-only and other readiness checks do not execute a settlement.

### H-020: Browser/UI behavior is not a CI gate

Severity: High

`package.json` exposes only `gui:demo`; there is no Playwright test command and neither CI pipeline installs/runs browser tests. The Django suite checks packet construction/retries and looks for three pieces of HTML text. It does not test form mapping, extra-payload precedence, client validation, double clicks, refresh, accessibility, visual regressions, disabled buttons, nested error display, or role visibility.

The only broad browser exercise is the non-gating demo runner described in C-004. Its checked-in report explicitly contains four failures.

### H-021: The simulator’s “production-identical” assertion has no independent protocol oracle

Severity: High

`test_button_press_sends_production_identical_signed_game_packet` compares the simulator packet with expectations authored in the same repository. There is no external protocol specification, captured real-client golden packet, schema validator, compatibility corpus, or cross-version contract test. The architecture guard merely checks that the test name and marker strings exist.

The test proves self-consistency with the current invented packet shape. It cannot prove production identity. Its forbidden-term scan is useful for leakage detection but is not protocol conformance.

### H-022: UDP responses are unauthenticated and their source is not verified by tests

Severity: High

`simulator/trade_gui/udp_client.py:50` discards the source address returned by `recvfrom`, and `decode_udp_response` accepts an unsigned JSON response. Tests only return a response from the expected fake address and never attempt a forged response, unexpected source, malformed oversized response, replayed response, or response for another interaction.

The request-signature tests can therefore pass while the client accepts spoofed or mismatched UDP replies. Request authenticity is not end-to-end outcome authenticity.

### H-023: No load, soak, capacity, or SLO test exists

Severity: High

There is no test for sustained throughput, queue saturation, RabbitMQ backlog, database pool exhaustion, HPA scale-out, hot-wallet/hot-stack contention, p95/p99 latency, memory/file-descriptor leaks, retry storms, or recovery time. The one two-thread race and one two-tab race are not capacity tests.

Production reliability claims about bounded queues, autoscaling, disruption budgets, and resilience have no measured workload evidence or pass/fail SLO.

### H-024: No fuzzing, property testing, mutation testing, or model-based state-machine testing exists

Severity: High

No Go fuzz targets, Rust property tests, Hypothesis tests, mutation score, or reference-model comparison exists. This is especially consequential for raw JSON/UDP parsing, protobuf conversion, arithmetic, checksums, idempotency fingerprints, and multi-operation financial state transitions.

The suite uses fixed IDs, two item types, two stations, ordinary quantities, and a small number of hand-authored sequences. It has no automated evidence that assertions would fail under representative injected faults; the deceptive happy-path tests in H-001/H-002 are exactly the kind mutation testing would expose.

## Medium-severity strictness and CI flaws

### M-001: There is no coverage threshold or coverage artifact

Severity: Medium

Neither CI pipeline collects or gates Go, Rust, or Python coverage. Coverage can fall to zero while CI remains green. The GUI demo’s final page says artifacts include a “coverage report” (`gui-simulator-demo.cjs:642`), but the runner generates no coverage report.

### M-002: Python observability tests are not run in CI

Severity: Medium

The repository has 14 tests for failure classification, Docker/JUnit collection, reports, retries, redaction, run context, and storage. GitHub runs `observed_run.py integration` but never invokes its `test` command. GitLab’s Python test step only performs E2E collection. Thus observability behavior can regress while both CI systems stay green.

This matters because GitHub sets `OBSERVABILITY_STRICT=false`; artifact/evidence failures are explicitly non-gating, and artifact upload uses `if-no-files-found: warn`.

### M-003: Simulator retry tests assert packet repetition but not the complete contract

Severity: Medium

The two Django retry tests assert HTTP 202 and two identical sends (`tests.py:156-198`). They do not assert the final decoded response, interaction record status/payload, max-attempt exhaustion, non-transient no-retry behavior, backoff timing, `request_in_progress` delay, unexpected response source, or socket cleanup.

### M-004: The GUI refresh “idempotency” check treats total request loss as success

Severity: Medium

`Immediate refresh cannot duplicate an in-flight issue` passes when settlement count and trade delta are both zero because it asserts only `<= 1` (`gui-simulator-demo.cjs:553-560`). A completely dropped user action therefore passes a reliability check. At-most-once without an eventual outcome/status mechanism is not reliable request handling.

### M-005: The demo fatal scan is both narrow and non-gating

Severity: Medium

The scan looks only for `panic`/`fatal`-shaped lines (`gui-simulator-demo.cjs:346-351`). It ignores error-level logs, stack traces without those words, OOM kills, unhealthy/restarted containers, lost messages, SQL constraint errors, telemetry export failures, and data mismatches. Matches are written to a file but never fail the run.

The checked-in `fatal-scan.txt` statement “No panic/fatal ... signatures found” is not evidence that logs were healthy.

### M-006: The architecture boundary guard is marker/string checking, not semantic validation

Severity: Medium

`verify_architecture_boundaries.py:92-109` considers the simulator boundary test present when specific strings occur anywhere in `tests.py`; comments, dead code, a skipped test, or an assertion-free function can satisfy it. It does not execute the test or parse its AST.

Other checks are similarly substring-based. The production-overlay simulator exclusion scans only files physically under `overlay/prod`; it does not inspect the rendered resource graph. The docs check requires the canonical sentence somewhere in all combined docs, not consistency in each document.

### M-007: The mutable-image test is an easily evaded text scan

Severity: Medium

`TestKubernetesManifestsAvoidMutableProductionTags` rejects only the literal substrings `:latest`, `newTag: latest`, and `newTag: prod` (`go_modules_test.go:63-86`). It does not parse YAML or rendered production manifests and misses tagless images, quoting/whitespace variants, variable/default expressions, mutable semantic tags such as `stable`, and invalid placeholder digests.

The checked-in production overlay contains zero SHA-256 placeholder digests and example registries/hosts while this “production tags” test passes. It proves only absence of three strings.

### M-008: CI dependency/tooling is not reproducible

Severity: Medium

Examples include:

- GitHub Actions referenced by mutable major tags rather than commit SHAs;
- `ubuntu-latest` and Rust `stable`;
- `staticcheck@latest`, `govulncheck@latest`, unversioned `cargo-audit`, and unversioned `pip-audit` installation;
- Python dependency ranges without lock/hashes;
- mutable Docker tags for PostgreSQL, RabbitMQ, Python, Debian, Go, and Rust;
- Terraform provider ranges with no tracked `.terraform.lock.hcl`.

The same commit can test different dependencies over time, and a green run does not identify the exact production dependency set.

### M-009: Security scans have explicit blind spots

Severity: Medium

GitLab Trivy uses `--ignore-unfixed`, so known HIGH/CRITICAL unfixed vulnerabilities do not fail. Gitleaks uses `--no-git`, so it does not scan repository history. GitHub `cargo audit` unconditionally ignores `RUSTSEC-2023-0071`; the comment says the vulnerable crate is unreachable, but CI does not assert `cargo tree -i rsa` remains empty. Python audit covers only simulator requirements, not E2E/observability requirements or a locked deployed environment.

No pipeline scans the final published image digests, generates/gates an SBOM, verifies signatures/provenance, or tests container runtime policy.

### M-010: GitHub and GitLab do not enforce equivalent gates

Severity: Medium

The GitHub Go matrix runs tidy, vet, race, staticcheck, govulncheck, and builds. Dagger `go_checks` runs only gofmt and `go test`. GitHub Rust runs all targets/features and audit; Dagger does fmt/clippy/test without all features or audit. GitHub runs Django tests and pip-audit; Dagger only collects E2E names. GitHub uses kubeconform; Dagger only renders.

A GitLab release can therefore be governed by a materially weaker check set even if C-003 is fixed. “CI parity” is not tested.

### M-011: CI has no explicit job timeouts or flaky-test/repeat policy

Severity: Medium

GitHub jobs have no `timeout-minutes`. Tests run once, in fixed order, without `-shuffle`, `-count`, stress/repeat, or flaky-test quarantine policy. The E2E job uses `--maxfail 1`, which is valid for fast failure but guarantees a run will not reveal the rest of the failure set.

Transient setup commands are retried in the observability wrapper, but behavioral tests are not repeated to detect nondeterminism.

### M-012: The E2E client leaks its HTTP client per test

Severity: Medium

The function-scoped `gateway` fixture returns `GatewayClient`, which owns `httpx.Client`, but neither class nor fixture closes it (`conftest.py:59-63`, `helpers.py:74-78`). Across 109 tests this can leak connection pools/file descriptors and makes long/stress runs less trustworthy.

### M-013: The test catalog/evidence artifacts are stale and not provenance-bound

Severity: Medium

The E2E README lists 92 test names while 109 collect. Several names differ from the implementation (idempotency key vs interaction ID), and the direct settlement/database tests are missing from the catalog.

The GUI artifact bundle records a run ID but not the tested Git commit, dirty state, image digests, dependency locks, or CI run. Its report says two Django tests passed while the current suite contains four. It also says the Docker integration suite was not rerun. These artifacts cannot serve as durable evidence for the current source without external provenance.

### M-014: Terraform validation is syntax/type validation, not infrastructure behavior

Severity: Medium

CI runs `terraform init -backend=false` and `terraform validate`; it does not run plans with representative production variables, policy-as-code, provider/cloud API checks, apply/destroy in ephemeral accounts, IAM tests, connectivity tests, drift detection, or upgrade tests. No provider lock file makes even validation resolution variable.

Calling these roots “production” or treating validate as deployability evidence is too strong.

### M-015: Production topology/security policy is outside the E2E boundary

Severity: Medium

The strongest E2E uses local Compose, static shared secrets, superuser PostgreSQL, plain in-network HTTP/gRPC, one replica per service, and no Istio, JWT, NetworkPolicy, Gateway/TLS, managed database, cloud IAM, HPA, PDB, or rolling update.

This is useful component integration evidence. It is not validation of the checked-in production Kubernetes/Terraform topology. No test bridges that gap.

### M-016: Important financial and lifecycle boundaries remain untested

Severity: Medium

The suite lacks focused tests for maximum `int64` quantity/price multiplication and version increments, checksum corruption, cross-trade/cross-owner low-level operation combinations, escrow funding/recipient identity binding, expiration cleanup/refund, clock boundaries, multiple primary wallets, missing/duplicate canonical rows, and trade lifecycle recovery after service restart.

Ordinary zero/negative and insufficient-balance cases do not cover these boundaries.

### M-017: Health/readiness tests do not prove a transaction can complete

Severity: Medium

E2E readiness checks database connectivity, API Gateway `/healthz`, simulator button listing, and open TCP ports. Compose service dependencies improve startup ordering, but no readiness probe performs a harmless end-to-end request/reply/DB transaction. Trade-settlement Kubernetes readiness is TCP-only.

A service can be “ready” while its database, broker, worker, or full settlement path is unusable. Deployment and chaos gates then rely on that incomplete readiness.

### M-018: No graceful-shutdown/in-flight request test exists

Severity: Medium

No unit or E2E test sends termination during an in-flight UDP, Market, RabbitMQ, or settlement operation and verifies drain, acknowledgement, retry, idempotent replay, and absence of partial state. This is a core rolling-deploy and pod-deletion reliability scenario.

## Documentation claims that currently overstate test evidence

The following claims should not be treated as proven by the present checks:

- `Architecture/ISO-42010/18-evidence-manifest.md` calls generated proto `go test` a test result even though those packages have 0 tests.
- The architecture development/validation view says Rust settlement variants require “Rust tests,” but the crate has none.
- The same view maps gateway bounded concurrency, queue overflow, rate limit, timeout, and other behavior to gateway tests even though listener/queue/worker paths have 0% direct coverage.
- `ci-cd/README.md` says Dagger `test` runs Go, Rust, and Python contract tests; its Python step is collect-only, and its Rust step runs an empty suite.
- `ci-cd/README.md` says Dagger integration runs the live message-driven E2E suite, but the required simulator/Quilkin topology is absent.
- The ChaosEngine annotations claim service continuity, but no functional probes measure continuity.
- The simulator test and architecture docs use “production-identical” without an independent production protocol oracle.
- The GUI evidence page says it contains a coverage report, but none is produced.

## Minimum production-readiness test gates missing

At minimum, a defensible production gate would need:

1. authenticated identity/authorization tests through the real edge and mesh policy;
2. direct Rust settlement unit/integration/property tests with nonzero enforced coverage or mutation expectations;
3. a fixed, runnable GitLab/Dagger topology matching the canonical path;
4. exact negative assertions and adversarial payloads for every security-named E2E test;
5. broker/process crash-window, redelivery, lost-reply, restart, and multi-replica idempotency tests;
6. migration upgrade/data-preservation tests, not only fresh installs;
7. `buf breaking` compatibility enforcement;
8. schema/policy/server-side validation for production Kubernetes CRDs and rendered manifests;
9. post-deploy authenticated smoke/E2E plus rollback/canary verification;
10. functional chaos probes and state-invariant checks during and after faults;
11. coverage, fuzz/property, mutation, load/soak, and SLO gates;
12. locked, provenance-bound dependencies and test artifacts.

Until those exist, passing CI means “the checked-in happy paths and selected invariants passed in this particular local/Compose toolchain,” not “the system is production ready.”
