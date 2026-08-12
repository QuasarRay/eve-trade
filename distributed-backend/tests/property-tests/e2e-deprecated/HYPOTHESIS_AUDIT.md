# Hypothesis Suite Audit

This audit is generated from the authoritative catalog, the two source-audit manifests, and the canonical execution requirements. The JSON companion contains every exact affected test name; this Markdown intentionally does not duplicate thousand-name arrays.

## Current result

- Audited contracts: 1484
- Finding records: 164
- Fixed findings: 72
- Remaining or partially mitigated findings: 92
- Fail-closed/unimplemented contracts: 1087

A green implemented test is evidence-bearing. Contracts without a named independent oracle are deliberately marked `eve_unimplemented` and fail in strict full-catalog execution. That is a mitigation, not completion.

## Findings

| ID | Severity | Status | Affected | Mechanism |
|---|---:|---|---:|---|
| CAT-001 | HIGH | FIXED | 1 | authoritative contract identity was silently rewritten |
| CLEAN-001 | HIGH | FIXED | 857 | adapter cleanup suppressed every exception |
| CONC-001 | HIGH | MITIGATED_FAIL_CLOSED | 47 | thread barrier synchronized call start but did not prove the target database/race boundary |
| COUNT-001 | MEDIUM | FIXED | 1484 | tests and documentation trusted hard-coded catalog counts |
| COVERAGE-001 | LOW | FIXED | 1484 | there was no exhaustive per-name audit/requirement join |
| EVID-001 | CRITICAL | FIXED | 100 | protocol-v2 evidence was replayable and only case-hash bound |
| EVID-002 | CRITICAL | FIXED | 74 | fault booleans and ChaosResult verdict were trusted as physical proof |
| EVID-003 | CRITICAL | MITIGATED_FAIL_CLOSED | 1061 | name-derived generic predicates substituted for contract-specific business oracles |
| EVID-004 | CRITICAL | FIXED | 74 | ChaosResult lookup and validation allowed a name-prefix fallback without exact ChaosEngine UID binding |
| EXT-001 | CRITICAL | FIXED | 26 | an arbitrary configured command could bypass EXTERNAL_CAPABILITY_REQUIRED and reach name-derived predicates |
| HYPO-001 | MEDIUM | FIXED | 131 | native wrappers varied only PYTHONHASHSEED |
| HYPO-002 | MEDIUM | FIXED | 1484 | blanket deadline=None and health-check suppression hid route-specific problems |
| HYPO-003 | HIGH | FIXED | 2 | valid-sequence conservation strategies could shrink to retry/invalid-only no-op examples |
| ISO-001 | HIGH | FIXED | 857 | full-schema TRUNCATE had no cross-process exclusion |
| LEGACY-ALLOWLIST-SEMANTICS-NOT-TESTED | CRITICAL | FIXED | 5 | allowlist_semantics_not_tested |
| LEGACY-ANY-NOT-EVERY | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | any_not_every |
| LEGACY-ARROW-PRESENCE-ONLY | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | arrow_presence_only |
| LEGACY-ATOMICITY-NOT-TESTED | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | atomicity_not_tested |
| LEGACY-AUDIT-IDENTITY-NOT-COMPARED | HIGH | MITIGATED_FAIL_CLOSED | 1 | audit_identity_not_compared |
| LEGACY-BUILD-CONTEXT-PROXY | HIGH | MITIGATED_FAIL_CLOSED | 1 | build_context_proxy |
| LEGACY-CACHE-CONFOUNDED-DETERMINISM | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | cache_confounded_determinism |
| LEGACY-COMMAND-PRESENCE-ONLY | HIGH | FIXED | 2 | command_presence_only |
| LEGACY-CONCURRENCY-NOT-EXERCISED | CRITICAL | MITIGATED_FAIL_CLOSED | 9 | concurrency_not_exercised |
| LEGACY-CONFIGURATION-REJECTION-NOT-EXERCISED | HIGH | FIXED | 2 | configuration_rejection_not_exercised |
| LEGACY-CONFOUNDED-AUTHORIZATION | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | confounded_authorization |
| LEGACY-CONFOUNDED-FAILURE | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | confounded_failure |
| LEGACY-CONFOUNDED-INPUT | HIGH | MITIGATED_FAIL_CLOSED | 1 | confounded_input |
| LEGACY-CONFOUNDED-PAYLOAD | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | confounded_payload |
| LEGACY-CONSTRAINT-DEFINITION-WRONG | CRITICAL | FIXED | 1 | constraint_definition_wrong |
| LEGACY-CONSTRAINT-NOT-TESTED | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | constraint_not_tested |
| LEGACY-CONTRADICTORY-CONTRACT | CRITICAL | PARTIAL | 2 | contradictory_contract |
| LEGACY-COVERAGE-NOT-ENUMERATED | CRITICAL | FIXED | 5 | coverage_not_enumerated |
| LEGACY-DATABASE-BOUNDARY-NOT-OBSERVED | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | database_boundary_not_observed |
| LEGACY-DOCKERFILE-TEXT-ONLY | HIGH | MITIGATED_FAIL_CLOSED | 3 | dockerfile_text_only |
| LEGACY-EXACTLY-ONCE-NOT-TESTED | HIGH | MITIGATED_FAIL_CLOSED | 1 | exactly_once_not_tested |
| LEGACY-EXACTLY-ONE-NOT-TESTED | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | exactly_one_not_tested |
| LEGACY-EXISTENCE-NOT-TRACEABILITY | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | existence_not_traceability |
| LEGACY-EXISTENCE-ONLY | CRITICAL | FIXED | 4 | existence_only |
| LEGACY-FAILURE-NOT-INJECTED | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | failure_not_injected |
| LEGACY-FAILURE-PATH-NOT-PROVEN | CRITICAL | FIXED | 1 | failure_path_not_proven |
| LEGACY-GENERATION-EQUIVALENCE-NOT-TESTED | CRITICAL | PARTIAL | 2 | generation_equivalence_not_tested |
| LEGACY-GENERIC-PRESENCE-BRANCH | CRITICAL | PARTIAL | 11 | generic_presence_branch |
| LEGACY-GLOBAL-DUPLICATE-TEST-PROXY | CRITICAL | FIXED | 1 | global_duplicate_test_proxy |
| LEGACY-GLOBAL-RESERVED-PROXY | HIGH | MITIGATED_FAIL_CLOSED | 2 | global_reserved_proxy |
| LEGACY-GLOBAL-SHA-PROXY | HIGH | MITIGATED_FAIL_CLOSED | 1 | global_sha_proxy |
| LEGACY-GLOBAL-TEXT-PROXY | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | global_text_proxy |
| LEGACY-GLOBAL-VALIDATION-TOKEN-PROXY | CRITICAL | FIXED | 3 | global_validation_token_proxy |
| LEGACY-HALF-ASSERTION | CRITICAL | FIXED | 1 | half_assertion |
| LEGACY-HARDCODED-INITIAL-STATE | HIGH | MITIGATED_FAIL_CLOSED | 1 | hardcoded_initial_state |
| LEGACY-HEURISTIC-NOT-SEMANTIC | HIGH | MITIGATED_FAIL_CLOSED | 4 | heuristic_not_semantic |
| LEGACY-HYPOTHESIS-STRATEGY-PRECEDENCE | CRITICAL | FIXED | 3 | hypothesis_strategy_precedence |
| LEGACY-IMPLEMENTATION-PRESENCE-NOT-TEST-TRACEABILITY | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | implementation_presence_not_test_traceability |
| LEGACY-INDEX-EXISTENCE-NOT-USAGE | CRITICAL | MITIGATED_FAIL_CLOSED | 6 | index_existence_not_usage |
| LEGACY-INTERNAL-CONSISTENCY-ONLY | HIGH | MITIGATED_FAIL_CLOSED | 4 | internal_consistency_only |
| LEGACY-JOB-OVERRIDE-NOT-CHECKED | HIGH | FIXED | 1 | job_override_not_checked |
| LEGACY-KEYWORD-PRESENCE-PROXY | HIGH | PARTIAL | 27 | keyword_presence_proxy |
| LEGACY-LINE-BASED-HCL-PROXY | HIGH | FIXED | 4 | line_based_hcl_proxy |
| LEGACY-LOCKFILE-NONEMPTY-PROXY | HIGH | PARTIAL | 5 | lockfile_nonempty_proxy |
| LEGACY-MISSING-CONTRACT-LOGIC | CRITICAL | PARTIAL | 869 | missing_contract_logic |
| LEGACY-MISSING-FAULT-LOGIC | CRITICAL | PARTIAL | 148 | missing_fault_logic |
| LEGACY-MISSING-POSTCONDITION | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | missing_postcondition |
| LEGACY-MULTI-STAGE-SCOPE-ERROR | HIGH | MITIGATED_FAIL_CLOSED | 2 | multi_stage_scope_error |
| LEGACY-MULTIPLE-INVALID-FIELDS | CRITICAL | MITIGATED_FAIL_CLOSED | 6 | multiple_invalid_fields |
| LEGACY-MUTATION-NOT-ATTEMPTED | HIGH | MITIGATED_FAIL_CLOSED | 2 | mutation_not_attempted |
| LEGACY-NAME-STRONGER-THAN-LOGIC | HIGH | PARTIAL | 2 | name_stronger_than_logic |
| LEGACY-NARROW-PATTERN | HIGH | MITIGATED_FAIL_CLOSED | 1 | narrow_pattern |
| LEGACY-NONEXISTENT-SELLER-NOT-ISOLATED | HIGH | MITIGATED_FAIL_CLOSED | 1 | nonexistent_seller_not_isolated |
| LEGACY-ONLY-EXISTENCE-CHECKED | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | only_existence_checked |
| LEGACY-ORDERING-NOT-OBSERVED | HIGH | MITIGATED_FAIL_CLOSED | 1 | ordering_not_observed |
| LEGACY-ORPHAN-POSTCONDITION-NOT-CHECKED | HIGH | MITIGATED_FAIL_CLOSED | 1 | orphan_postcondition_not_checked |
| LEGACY-OUTBOX-NOT-REQUIRED | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | outbox_not_required |
| LEGACY-OVERLAY-COMPARISON-NOT-PERFORMED | HIGH | FIXED | 2 | overlay_comparison_not_performed |
| LEGACY-PANIC-NOT-PROVEN | HIGH | MITIGATED_FAIL_CLOSED | 5 | panic_not_proven |
| LEGACY-PARTIAL-ASSERTION | HIGH | MITIGATED_FAIL_CLOSED | 1 | partial_assertion |
| LEGACY-PARTIAL-INFORMATION-DISCLOSURE-CHECK | HIGH | MITIGATED_FAIL_CLOSED | 1 | partial_information_disclosure_check |
| LEGACY-PARTIAL-STATE | HIGH | MITIGATED_FAIL_CLOSED | 1 | partial_state |
| LEGACY-PAYLOAD-NOT-HELD-CONSTANT | HIGH | MITIGATED_FAIL_CLOSED | 1 | payload_not_held_constant |
| LEGACY-PLATFORM-NOT-CHECKED | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | platform_not_checked |
| LEGACY-POSTCONDITION-INCOMPLETE | HIGH | MITIGATED_FAIL_CLOSED | 1 | postcondition_incomplete |
| LEGACY-PRESENCE-NOT-CARDINALITY | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | presence_not_cardinality |
| LEGACY-PRINCIPAL-FINGERPRINT-NOT-ISOLATED | HIGH | MITIGATED_FAIL_CLOSED | 1 | principal_fingerprint_not_isolated |
| LEGACY-RACE-FLAG-PRESENCE-ONLY | HIGH | MITIGATED_FAIL_CLOSED | 2 | race_flag_presence_only |
| LEGACY-RACE-TARGET-NOT-RESOLVED | HIGH | MITIGATED_FAIL_CLOSED | 1 | race_target_not_resolved |
| LEGACY-REGEX-INCOMPLETE | HIGH | MITIGATED_FAIL_CLOSED | 1 | regex_incomplete |
| LEGACY-REGEX-TAINT-PROXY | HIGH | MITIGATED_FAIL_CLOSED | 1 | regex_taint_proxy |
| LEGACY-REQUIRED-SET-NOT-PROVEN | HIGH | MITIGATED_FAIL_CLOSED | 1 | required_set_not_proven |
| LEGACY-RESPONSE-CARDINALITY-NOT-CHECKED | HIGH | MITIGATED_FAIL_CLOSED | 1 | response_cardinality_not_checked |
| LEGACY-RETRY-COUNT-AMBIGUOUS | HIGH | MITIGATED_FAIL_CLOSED | 1 | retry_count_ambiguous |
| LEGACY-RETRYABILITY-NOT-TESTED | CRITICAL | MITIGATED_FAIL_CLOSED | 3 | retryability_not_tested |
| LEGACY-SCALE-AND-RANDOMNESS-MISSING | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | scale_and_randomness_missing |
| LEGACY-SCOPE-NOT-CHECKED | CRITICAL | FIXED | 2 | scope_not_checked |
| LEGACY-SCOPE-OVERBROAD | HIGH | FIXED | 1 | scope_overbroad |
| LEGACY-SCOPE-WEAKER-THAN-NAME | HIGH | MITIGATED_FAIL_CLOSED | 1 | scope_weaker_than_name |
| LEGACY-SEARCH-PATH-NOT-BOUND-TO-ROLE | HIGH | MITIGATED_FAIL_CLOSED | 1 | search_path_not_bound_to_role |
| LEGACY-SECURITY-DEFINER-SEARCH-PATH-NOT-CHECKED | HIGH | MITIGATED_FAIL_CLOSED | 1 | security_definer_search_path_not_checked |
| LEGACY-SHA-PRESENCE-NOT-EQUALITY | CRITICAL | MITIGATED_FAIL_CLOSED | 4 | sha_presence_not_equality |
| LEGACY-SOME-PROVIDER-VS-EVERY-PROVIDER | HIGH | FIXED | 1 | some_provider_vs_every_provider |
| LEGACY-SOURCE-STRING-PRESENCE | CRITICAL | FIXED | 4 | source_string_presence |
| LEGACY-START-AT-ONE-NOT-TESTED | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | start_at_one_not_tested |
| LEGACY-STATIC-PRESENCE-VS-RUNTIME-BEHAVIOR | CRITICAL | MITIGATED_FAIL_CLOSED | 3 | static_presence_vs_runtime_behavior |
| LEGACY-SUBSTRING-PROXY | HIGH | MITIGATED_FAIL_CLOSED | 1 | substring_proxy |
| LEGACY-SUCCESS-COUNT-NOT-REQUIRED | HIGH | MITIGATED_FAIL_CLOSED | 1 | success_count_not_required |
| LEGACY-TERMINAL-RESPONSE-NOT-COMPARED | HIGH | MITIGATED_FAIL_CLOSED | 1 | terminal_response_not_compared |
| LEGACY-TOKEN-PRESENCE-NOT-EQUALITY | CRITICAL | FIXED | 2 | token_presence_not_equality |
| LEGACY-TOKEN-PRESENCE-PROXY | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | token_presence_proxy |
| LEGACY-TRIGGER-NOT-CHECKED | CRITICAL | FIXED | 1 | trigger_not_checked |
| LEGACY-UNRELATED-ASSERTION | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | unrelated_assertion |
| LEGACY-UNRENDERED-MANIFEST-SCOPE | HIGH | FIXED | 1 | unrendered_manifest_scope |
| LEGACY-VACUOUS-CONTAINER-NAME-FILTER | HIGH | FIXED | 2 | vacuous_container_name_filter |
| LEGACY-VACUOUS-PASS | CRITICAL | MITIGATED_FAIL_CLOSED | 3 | vacuous_pass |
| LEGACY-VACUOUS-RESOURCE-LOOKUP | HIGH | FIXED | 3 | vacuous_resource_lookup |
| LEGACY-VACUOUS-SCHEMA-GUARD | HIGH | MITIGATED_FAIL_CLOSED | 1 | vacuous_schema_guard |
| LEGACY-WEAK-RELATION | HIGH | MITIGATED_FAIL_CLOSED | 1 | weak_relation |
| LEGACY-WORKFLOW-ONLY-SCOPE | HIGH | FIXED | 1 | workflow_only_scope |
| LEGACY-WRONG-ASSERTION | CRITICAL | FIXED | 1 | wrong_assertion |
| LEGACY-WRONG-ENUM-FIELD-EXERCISED | HIGH | MITIGATED_FAIL_CLOSED | 1 | wrong_enum_field_exercised |
| LEGACY-WRONG-FAILURE-PATH | HIGH | MITIGATED_FAIL_CLOSED | 2 | wrong_failure_path |
| LEGACY-WRONG-GENERATOR | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | wrong_generator |
| LEGACY-WRONG-INPUT | CRITICAL | MITIGATED_FAIL_CLOSED | 2 | wrong_input |
| LEGACY-WRONG-MEASUREMENT | HIGH | MITIGATED_FAIL_CLOSED | 1 | wrong_measurement |
| LEGACY-WRONG-OBJECT-TYPE | CRITICAL | FIXED | 1 | wrong_object_type |
| LEGACY-WRONG-RELATION | HIGH | MITIGATED_FAIL_CLOSED | 1 | wrong_relation |
| LEGACY-WRONG-SECURITY-BOUNDARY | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | wrong_security_boundary |
| LEGACY-WRONG-STATE-MUTATION | CRITICAL | MITIGATED_FAIL_CLOSED | 1 | wrong_state_mutation |
| LITMUS-001 | CRITICAL | PARTIAL | 74 | repository contained stopped pod-delete examples but no case-bound execution pipeline |
| LITMUS-002 | MEDIUM | FIXED | 74 | operator readiness assumed one Helm label dialect |
| LITMUS-003 | CRITICAL | FIXED | 2 | network effect probes used a host kubectl port-forward that could bypass the target pod interface |
| LITMUS-004 | HIGH | FIXED | 1 | twenty random probes had to measure at least the exact configured packet-loss percentage |
| LITMUS-005 | CRITICAL | FIXED | 4 | workload overlap used a broad activation-to-cleanup timestamp without binding requests to physical-effect observations |
| MAP-001 | HIGH | FIXED | 4 | implemented infrastructure contracts were described as business/database workloads and annotationCheck disagreed with the executable engine |
| META-001 | MEDIUM | FIXED | 176 | route-integrity meta-test allowed the repository adapter only for categories 26 and 50 |
| META-002 | HIGH | FIXED | 584 | standalone catalog verifier retained obsolete runner vocabulary and excluded direct repository/fault adapters |
| NATIVE-001 | CRITICAL | FIXED | 131 | native wrapper assigned the None return from require_repo as a path and passed unsupported context to assert_ok |
| NEG-001 | CRITICAL | FIXED | 4 | evidence protocol had no hostile testing |
| ORACLE-001 | MEDIUM | FIXED | 2 | naming policy oracles included their own necessarily self-referential contract identities |
| ORACLE-002 | HIGH | FIXED | 1 | test-reference oracle treated every test_* prose token as an executable pytest node selector |
| ORACLE-003 | HIGH | MITIGATED_FAIL_CLOSED | 1 | retry naming oracle used an incomplete token whitelist as a semantic business-effect classifier |
| ORACLE-004 | HIGH | MITIGATED_FAIL_CLOSED | 1 | crash naming oracle used a token whitelist as a semantic window/recovery classifier |
| ORACLE-005 | HIGH | MITIGATED_FAIL_CLOSED | 1 | enum traceability oracle treated one textual token occurrence as proof of both success and rejection coverage |
| ORACLE-006 | CRITICAL | MITIGATED_FAIL_CLOSED | 29 | database rejection contracts delegated exact invalid-row construction to generic external evidence |
| ORACLE-007 | HIGH | FIXED | 2 | merge-safety properties greened on rejection of deliberately invalid explicit destination IDs |
| ORACLE-008 | HIGH | FIXED | 6 | implemented plan oracles fell back to generic external evidence when the required database column was absent |
| ORACLE-009 | HIGH | FIXED | 2 | public-response checks omitted nested/camelized primary keys and did not place an actual transport secret near the error path |
| ORACLE-010 | HIGH | FIXED | 3 | terminal-state properties checked zero quantities or failed-step count without asserting the named terminal state |
| ORACLE-011 | HIGH | FIXED | 1 | command-identity uniqueness scanned a flat list without proving every required verification job supplied one |
| ORACLE-012 | HIGH | FIXED | 2 | durable-channel oracles searched one repository-wide blob for both channel names and the absence of #ephemeral |
| ORACLE-013 | CRITICAL | FIXED | 4 | cleanup evidence asserted target_effect_active=false after deleting resources without independently probing that the physical effect was absent |
| ORACLE-014 | CRITICAL | FIXED | 4 | independent validation did not bind the raw ChaosEngine target or physical pod transition to the declared selected resources |
| ORACLE-015 | HIGH | FIXED | 4 | final chaos evidence accepted a Litmus phase of Running |
| ORACLE-016 | CRITICAL | FIXED | 4 | generated Litmus parameters were compared only with collector-authored applied_parameters, not the raw ChaosEngine environment |
| ORACLE-017 | HIGH | FIXED | 4 | recovery deadline evidence was not derived from the generated recovery_deadline_seconds value |
| PIN-001 | HIGH | FIXED | 74 | Litmus manifest recorded only the 3.31.0 release while the pinned checkout's installed litmus-agent chart and operator images are 3.30.0 |
| PIPE-001 | HIGH | FIXED | 1484 | failed stages raised before being recorded and Dagger aborted before exporting failure artifacts |
| PIPE-002 | CRITICAL | FIXED | 957 | integration credentials were forwarded as ordinary Dagger environment values |
| PIPE-003 | HIGH | FIXED | 1484 | supplied-cluster mode unnecessarily depended on a host Unix Docker socket and nested Docker |
| PIPE-004 | HIGH | FIXED | 1484 | Dagger source root was derived from the caller's current working directory |
| PIPE-005 | HIGH | FIXED | 1484 | Kind mode validated a Dagger source snapshot but executed tests from a second mutable host bind mount inside nested Docker |
| ROUTE-001 | HIGH | FIXED | 12 | category-78 parser fuzz contracts were marked implemented without a parser fuzz runner |
| ROUTE-002 | HIGH | FIXED | 25 | execution ignored the canonical per-contract runner and re-derived dispatch from category |
| ROUTE-003 | CRITICAL | FIXED | 29 | direct live database contracts were assigned the trade adapter, which has no category-26/50 handler |
| ROUTE-004 | CRITICAL | FIXED | 14 | direct-live fault categories were assigned the trade adapter instead of the fault adapter |
| SCOPE-001 | HIGH | FIXED | 394 | recursive repository scans traversed ignored generated dependency trees |
| SCOPE-002 | CRITICAL | FIXED | 44 | Kubernetes properties omitted the production orchestration tree and allowed an empty workload set |
| STRAT-001 | CRITICAL | PARTIAL | 74 | generated fault values were serialized but not proven applied |

## Exhaustive details

See `HYPOTHESIS_AUDIT.json` for each finding's full affected-name set, source files, proof, repair, verification, and final status. See `../infra/test-requirements.json` for every remaining exact contract and physical blocker.
