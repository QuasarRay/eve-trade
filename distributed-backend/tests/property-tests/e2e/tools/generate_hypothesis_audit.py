#!/usr/bin/env python3
"""Generate the exhaustive Hypothesis audit from source findings and current routes."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


E2E_ROOT = Path(__file__).resolve().parents[1]
PROPERTY_ROOT = E2E_ROOT.parent
INFRA_ROOT = PROPERTY_ROOT / "infra"
LEGACY = E2E_ROOT / "audit_resolution_manifest.json"
SEMANTIC = E2E_ROOT / "semantic_override_manifest.json"
REQUIREMENTS = INFRA_ROOT / "test-requirements.json"
OUTPUT_JSON = E2E_ROOT / "HYPOTHESIS_AUDIT.json"
OUTPUT_MD = E2E_ROOT / "HYPOTHESIS_AUDIT.md"
SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _finding(
    finding_id: str,
    severity: str,
    names: list[str],
    files: list[str],
    mechanism: str,
    risk: str,
    proof: str,
    repair: str,
    verification: list[str],
    status: str,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "affected_test_names": sorted(set(names)),
        "affected_source_files": files,
        "failure_mechanism": mechanism,
        "false_green_or_false_red_risk": risk,
        "reproduction_or_proof": proof,
        "repair_performed": repair,
        "verification": verification,
        "final_status": status,
    }


def _slug(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", value.upper()).strip("-")


def generate() -> dict[str, Any]:
    legacy = json.loads(LEGACY.read_text(encoding="utf-8"))
    semantic = json.loads(SEMANTIC.read_text(encoding="utf-8"))
    requirements_doc = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    requirements = {record["name"]: record for record in requirements_doc["contracts"]}
    all_names = list(requirements)
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"names": set(), "severities": [], "resolutions": set()}
    )
    for record in legacy["records"]:
        name = str(record.get("original_name") or record["name"])
        for item in record.get("findings", []):
            grouped[item["kind"]]["names"].add(name)
            grouped[item["kind"]]["severities"].append(item["severity"])
            grouped[item["kind"]]["resolutions"].add(item.get("resolution", ""))
    for name, kinds in semantic.items():
        for kind in kinds:
            grouped[kind]["names"].add(name)
            grouped[kind]["severities"].append("HIGH")
            grouped[kind]["resolutions"].add("previous protocol-v2 semantic override")

    findings: list[dict[str, Any]] = []
    for kind in sorted(grouped):
        data = grouped[kind]
        severity = max(data["severities"], key=lambda item: SEVERITY_RANK[item])
        names = sorted(data["names"])
        if kind == "hypothesis_strategy_precedence":
            status = "FIXED"
            repair = (
                "Moved state-machine/fault category strategies ahead of broad token matching and added "
                "a property proving invalid sequences always contain the required invalid operation."
            )
        else:
            status = "MITIGATED_FAIL_CLOSED"
            repair = (
                "Removed the protocol-v2 generic-evidence claim of resolution. Every affected unresolved "
                "contract is recorded as SEMANTIC_ORACLE_REQUIRED and strict execution fails before the weak runner."
            )
        findings.append(
            _finding(
                "LEGACY-" + _slug(kind),
                severity,
                names,
                [
                    "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/",
                    "distributed-backend/tests/property-tests/e2e/semantic_override_manifest.json",
                ],
                kind,
                "The old generic/category implementation could remain green while the named invariant was false.",
                "Source audit inventory grouped every exact affected contract; old claimed resolutions were "
                + ", ".join(sorted(item for item in data["resolutions"] if item)),
                repair,
                [
                    "test_unresolved_semantic_overrides_are_fail_closed_in_canonical_requirements",
                    "test_every_unimplemented_contract_has_an_exhaustive_blocker_record",
                ],
                status,
            )
        )

    by_mechanism: dict[str, list[str]] = defaultdict(list)
    by_status: dict[str, list[str]] = defaultdict(list)
    for name, requirement in requirements.items():
        by_mechanism[requirement["mechanism"]].append(name)
        by_status[requirement["implementation_status"]].append(name)
    concurrency = [
        name
        for name in all_names
        if any(token in name for token in ("concurrent", "_race", "race_"))
        and requirements[name]["implementation_status"] == "SEMANTIC_ORACLE_REQUIRED"
    ]
    parser_fuzz_contracts = [
        name for name in all_names if requirements[name].get("category") == 78
    ]
    systemic = [
        _finding(
            "CAT-001",
            "HIGH",
            ["test_issue_rejects_item_stack_with_wrong_item_type_claim"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/catalog.json",
                "distributed-backend/tests/property-tests/e2e/tools/sync_authoritative_catalog.py",
            ],
            "authoritative contract identity was silently rewritten",
            "Collection tested a replacement name rather than the exact Markdown contract.",
            "The Markdown/catalog set difference exposed one missing authoritative name and one invented name.",
            "Restored the exact name, regenerated modules, retained the architecture contradiction as explicit NON_APPLICABLE, and added equality meta-tests.",
            ["sync_authoritative_catalog.py --check", "test_authoritative_catalog_registry_and_requirement_names_are_exactly_equal"],
            "FIXED",
        ),
        _finding(
            "EVID-001",
            "CRITICAL",
            by_mechanism["LITMUS_CHAOS"] + by_mechanism["PLATFORM_EXTERNAL"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/external.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py",
            ],
            "protocol-v2 evidence was replayable and only case-hash bound",
            "Stale evidence from another physical execution could be relabelled with the same generated case.",
            "The old protocol had no run ID, invocation ID, nonce, bounded collection time, or unique evidence ID.",
            "Protocol v3 requires an exact challenge envelope, current timestamps, unique evidence IDs, raw observation provenance, and rejects replays.",
            ["test_replayed_evidence_id_is_rejected_even_when_case_hash_matches", "test_evidence_generated_before_current_invocation_is_rejected"],
            "FIXED",
        ),
        _finding(
            "EVID-002",
            "CRITICAL",
            by_mechanism["LITMUS_CHAOS"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py",
                "distributed-backend/tests/property-tests/infra/litmus_driver.py",
            ],
            "fault booleans and ChaosResult verdict were trusted as physical proof",
            "A no-op driver could claim injected=true/Passed while the target remained unaffected.",
            "The previous validator accepted driver-authored injection/window booleans without raw target transitions.",
            "Require two evidence sources, a fault-family-specific raw physical transition, raw ChaosEngine/ChaosResult UIDs, and independently recomputed request overlap.",
            ["test_noop_fault_driver_cannot_make_implemented_chaos_property_green", "test_chaosresult_without_family_specific_target_effect_is_rejected"],
            "FIXED",
        ),
        _finding(
            "EVID-003",
            "CRITICAL",
            by_status["SEMANTIC_ORACLE_REQUIRED"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_specs.py",
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
            ],
            "name-derived generic predicates substituted for contract-specific business oracles",
            "A scenario-shaped response could satisfy parsed words without independently proving persisted business state.",
            "The exhaustive legacy and semantic-override manifests identify every affected exact name.",
            "Removed false claims of implementation and fail-closed every unresolved contract; exact blockers remain in test-requirements.json.",
            ["test_unresolved_semantic_overrides_are_fail_closed_in_canonical_requirements"],
            "MITIGATED_FAIL_CLOSED",
        ),
        _finding(
            "EVID-004",
            "CRITICAL",
            by_mechanism["LITMUS_CHAOS"],
            [
                "distributed-backend/tests/property-tests/infra/litmus_driver.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py",
            ],
            "ChaosResult lookup and validation allowed a name-prefix fallback without exact ChaosEngine UID binding",
            "A stale or unrelated result with a colliding prefix could be accepted as the current invocation's Litmus state.",
            "The driver accepted name.startswith(engine_name) and the validator compared resource UIDs without checking the ChaosResult chaosUID label.",
            "Require exact chaosUID selection and independently bind the raw ChaosResult label to the raw ChaosEngine UID; add a hostile wrong-UID mutation.",
            ["test_hostile_evidence_mutations_are_rejected"],
            "FIXED",
        ),
        _finding(
            "EXT-001",
            "CRITICAL",
            by_mechanism["PLATFORM_EXTERNAL"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/engine.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/external.py",
            ],
            "an arbitrary configured command could bypass EXTERNAL_CAPABILITY_REQUIRED and reach name-derived predicates",
            "Unimplemented managed-cloud/control-plane contracts could green when a driver returned scenario-shaped protocol-v3 evidence without an exact independent oracle.",
            "Engine gated only SEMANTIC_ORACLE_REQUIRED, while ExternalContractDriver gated EXTERNAL_CAPABILITY_REQUIRED only when no command was configured.",
            "Fail closed every unresolved implementation status regardless of command presence; only an IMPLEMENTED canonical requirement can invoke a driver.",
            ["test_every_external_route_declares_evidence_schema_and_fails_closed_without_driver"],
            "FIXED",
        ),
        _finding(
            "HYPO-001",
            "MEDIUM",
            by_mechanism["NATIVE_EXISTING"],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/registry.py"],
            "native wrappers varied only PYTHONHASHSEED",
            "Repeated examples did not vary a business dimension and inflated property counts.",
            "Every native wrapper executed the identical pytest node with only a hash-seed integer.",
            "Converted native wrappers to one ordinary pytest invocation preserving the original oracle.",
            ["pytest collection and registry source meta-tests"],
            "FIXED",
        ),
        _finding(
            "HYPO-002",
            "MEDIUM",
            all_names,
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/registry.py"],
            "blanket deadline=None and health-check suppression hid route-specific problems",
            "Hung live/external examples and fixture misuse could avoid Hypothesis feedback.",
            "The same settings object was applied to static, native, live, and external routes.",
            "Static/native tests are deterministic pytest; generated live/chaos/platform properties have bounded route-specific deadlines and one justified health suppression.",
            ["registry source inspection", "pytest --collect-only"],
            "FIXED",
        ),
        _finding(
            "STRAT-001",
            "CRITICAL",
            by_mechanism["LITMUS_CHAOS"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/strategies.py",
                "distributed-backend/tests/property-tests/infra/litmus_driver.py",
            ],
            "generated fault values were serialized but not proven applied",
            "Shrinking could change evidence labels without changing the physical experiment.",
            "The previous driver only checked case_sha256 and driver booleans.",
            "Litmus strategies are generated from the canonical safe domain; the driver applies every value to Litmus/workload configuration and the validator checks exact applied parameters. Unsupported business contracts remain fail-closed.",
            ["test_litmus_mapping_is_exact_and_has_complete_execution_evidence_model", "hostile applied-parameter mutation control"],
            "PARTIAL",
        ),
        _finding(
            "ISO-001",
            "HIGH",
            by_mechanism["DIRECT_LIVE"],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/live.py"],
            "full-schema TRUNCATE had no cross-process exclusion",
            "Parallel workers or runs could erase one another's examples and contaminate shrinking.",
            "Database.reset truncates correctness-critical tables on a shared database.",
            "A session advisory lock is acquired before any reset and held for the runtime lifetime; unavailable lock fails/skips before mutation.",
            ["source inspection; live execution requires a provisioned PostgreSQL environment"],
            "FIXED",
        ),
        _finding(
            "CLEAN-001",
            "HIGH",
            by_mechanism["DIRECT_LIVE"],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/live.py"],
            "adapter cleanup suppressed every exception",
            "Leaked sockets/database state could be reported as a green example.",
            "LiveAdapter.close wrapped every close call in contextlib.suppress(Exception).",
            "Cleanup now attempts all resources and raises an ExceptionGroup containing every failure.",
            ["source inspection and cleanup audit meta-record"],
            "FIXED",
        ),
        _finding(
            "CONC-001",
            "HIGH",
            concurrency,
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/live.py",
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
            ],
            "thread barrier synchronized call start but did not prove the target database/race boundary",
            "Scheduled contenders could execute sequentially while the test claimed a race invariant.",
            "LiveAdapter.concurrent only places a barrier immediately before invoking each function.",
            "Affected generated contracts are fail-closed until narrow production-disabled failpoints or authoritative database barrier observations are added.",
            ["affected names are exhaustive in this audit and test-requirements.json"],
            "MITIGATED_FAIL_CLOSED",
        ),
        _finding(
            "LITMUS-001",
            "CRITICAL",
            by_mechanism["LITMUS_CHAOS"],
            ["distributed-backend/tests/property-tests/infra/"],
            "repository contained stopped pod-delete examples but no case-bound execution pipeline",
            "Chaos-named tests could not create or prove their prerequisites.",
            "The old manifests had engineState=stop and no Hypothesis/Dagger execution path.",
            "Added pinned Dagger/Kind/supplied-cluster orchestration, pinned Litmus installation, "
            f"{len(by_mechanism['LITMUS_CHAOS'])} exact fault specifications, and executable independent oracles "
            "for pod-delete/loss/latency infrastructure contracts. Remaining named business oracles are fail-closed.",
            ["pipeline structural verification", "implemented Litmus family stages when a Docker/Kind runtime is available"],
            "PARTIAL",
        ),
        _finding(
            "LITMUS-002",
            "MEDIUM",
            by_mechanism["LITMUS_CHAOS"],
            ["distributed-backend/tests/property-tests/infra/install_litmus.py"],
            "operator readiness assumed one Helm label dialect",
            "A healthy pinned Litmus installation using the legacy app=chaos-operator label could false-red before experiments began.",
            "The pinned chart lineage has used both app and app.kubernetes.io/name conventions.",
            "Discover the actual deployment under either exact operator label and wait on each concrete deployment name with a bounded rollout deadline.",
            ["compile/source verification; cluster execution requires a disposable Kubernetes runtime"],
            "FIXED",
        ),
        _finding(
            "LITMUS-003",
            "CRITICAL",
            [
                "test_litmus_network_delay_experiment_records_injected_latency_range_in_test_evidence",
                "test_litmus_network_loss_experiment_proves_impairment_is_active_before_workload_assertions_begin",
            ],
            ["distributed-backend/tests/property-tests/infra/litmus_driver.py"],
            "network effect probes used a host kubectl port-forward that could bypass the target pod interface",
            "Litmus could mutate pod eth0 while API-server port-forward traffic remained healthy, yielding false no-injection failures or measuring the wrong path.",
            "Topology tracing showed the runner's default probe was 127.0.0.1:14000 while network chaos targets the encore-backend pod.",
            "Execute every baseline, active, workload, and recovery HTTP probe inside the simulator pod against the encore-backend Service DNS, forcing traffic across the affected network path.",
            ["compile/source verification; physical execution requires disposable Kind/Litmus"],
            "FIXED",
        ),
        _finding(
            "LITMUS-004",
            "HIGH",
            ["test_litmus_network_loss_experiment_proves_impairment_is_active_before_workload_assertions_begin"],
            [
                "distributed-backend/tests/property-tests/infra/litmus_driver.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/chaos_oracles.py",
            ],
            "twenty random probes had to measure at least the exact configured packet-loss percentage",
            "A correctly injected 10% probabilistic loss false-reds frequently when fewer than two of twenty samples are lost.",
            "The binomial probability of observing below the exact 10% threshold with twenty samples is operationally large.",
            "Use at least sixty-four bounded concurrent probes, require a clean baseline, independently verify exact parameter application, and require a conservative nonzero physical-effect threshold rather than equality to a probabilistic setting.",
            ["negative-control evidence tests", "physical execution requires disposable Kind/Litmus"],
            "FIXED",
        ),
        _finding(
            "LITMUS-005",
            "CRITICAL",
            list(IMPLEMENTED_CHAOS_NAMES(requirements)),
            [
                "distributed-backend/tests/property-tests/infra/litmus_driver.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py",
            ],
            "workload overlap used a broad activation-to-cleanup timestamp without binding requests to physical-effect observations",
            "A request sent after the fault had already reverted could still fall before cleanup and be counted as crossing the failure window.",
            "The old crossing calculation compared only request timestamps to active_at and the later cleanup timestamp.",
            "For network faults, execute the exact generated workload as a subset of the raw effect-bearing probe batch; for pod deletion, require a clean baseline and at least one generated request that independently observed unavailability. Recompute exact request count and overlap in Python.",
            ["fault-window hostile canary", "pod-workload-success hostile mutation"],
            "FIXED",
        ),
        _finding(
            "MAP-001",
            "HIGH",
            list(IMPLEMENTED_CHAOS_NAMES(requirements)),
            [
                "distributed-backend/tests/property-tests/infra/generate_contract_manifests.py",
                "distributed-backend/tests/property-tests/infra/litmus-contracts.json",
            ],
            "implemented infrastructure contracts were described as business/database workloads and annotationCheck disagreed with the executable engine",
            "The canonical mapping could not reconstruct the actual prerequisite or generated workload and falsely claimed database observations that the driver never collected.",
            "Joining the four implemented records to litmus_driver.py exposed contract_specific_business_workload, seeded trade state, and annotation_check=true versus in-cluster probes and annotationCheck=false.",
            "Emit exact infrastructure initial state, probes, effect-bound workload, raw observations, named oracle, and the executable annotation setting; retain business prerequisites only for unresolved business contracts.",
            ["test_litmus_mapping_is_exact_and_has_complete_execution_evidence_model"],
            "FIXED",
        ),
        _finding(
            "NEG-001",
            "CRITICAL",
            list(IMPLEMENTED_CHAOS_NAMES(requirements)),
            ["distributed-backend/tests/property-tests/e2e/tests/test_evidence_negative_controls.py"],
            "evidence protocol had no hostile testing",
            "Regression to a no-op, stale, mismatched, or non-overlapping driver could go unnoticed.",
            "No prior test deliberately attempted the required adversarial evidence mutations.",
            "Added no-op and every requested hostile mutation control.",
            ["pytest test_evidence_negative_controls.py"],
            "FIXED",
        ),
        _finding(
            "NATIVE-001",
            "CRITICAL",
            by_mechanism["NATIVE_EXISTING"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/engine.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/repo.py",
            ],
            "native wrapper assigned the None return from require_repo as a path and passed unsupported context to assert_ok",
            "Every applicable native wrapper raised a harness TypeError before its original E2E oracle could execute.",
            "Source execution tracing showed require_repo returns None and CommandResult.assert_ok previously accepted no context argument.",
            "Use the validated RepoInspector.root path and support an optional failure context in CommandResult.assert_ok.",
            ["test_native_existing_wrapper_uses_validated_repository_root_and_original_node"],
            "FIXED",
        ),
        _finding(
            "COUNT-001",
            "MEDIUM",
            all_names,
            [
                "distributed-backend/tests/property-tests/e2e/tests/test_catalog_registry.py",
                "distributed-backend/tests/property-tests/infra/verify_collection.py",
            ],
            "tests and documentation trusted hard-coded catalog counts",
            "Catalog changes could make green completeness claims stale.",
            "The registry test embedded fixed existing/proposed/total literals.",
            "All counts are recomputed from current Markdown, manifests, and collected node IDs.",
            ["verify_collection.py", "test_manifest_counts_are_derived_from_current_records"],
            "FIXED",
        ),
        _finding(
            "ROUTE-001",
            "HIGH",
            parser_fuzz_contracts,
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py",
                "distributed-backend/tests/property-tests/infra/generate_contract_manifests.py",
            ],
            "category-78 parser fuzz contracts were marked implemented without a parser fuzz runner",
            "The repository dispatcher fell through to external evidence, so development mode could skip tests advertised as implemented.",
            "Executing the implemented static subset produced skips for all category-78 contracts; source inspection confirmed no category-78 branch.",
            "Classified the exact contracts as SEMANTIC_ORACLE_REQUIRED until a real parser fuzz harness and acceptance/crash oracle exist.",
            ["test_every_unimplemented_contract_has_an_exhaustive_blocker_record", "strict static subset"],
            "FIXED",
        ),
        _finding(
            "ROUTE-002",
            "HIGH",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "REPOSITORY_STATIC"
                and requirements[name].get("category") in {66, 78, 101}
            ],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/engine.py",
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
            ],
            "execution ignored the canonical per-contract runner and re-derived dispatch from category",
            "Contracts deliberately classified static inside historically fault/edge categories would enter the wrong adapter if their fail-closed gate were resolved.",
            "Engine source called modes.runner_for(category) even though the exhaustive requirement record already carried the unique semantic runner.",
            "Dispatch from the canonical requirement runner and make repo the same executable route identifier in generation, registry validation, and engine execution.",
            ["test_every_requirement_has_exactly_one_known_execution_route", "engine gate-order meta-test"],
            "FIXED",
        ),
        _finding(
            "ROUTE-003",
            "CRITICAL",
            [
                name
                for name in all_names
                if requirements[name].get("category") in {26, 50}
                and requirements[name]["mechanism"] == "DIRECT_LIVE"
            ],
            ["distributed-backend/tests/property-tests/infra/generate_contract_manifests.py"],
            "direct live database contracts were assigned the trade adapter, which has no category-26/50 handler",
            "The wrapper would raise an unhandled trade category before reaching either a database probe or a fail-closed oracle.",
            "Joining implemented DIRECT_LIVE requirements to trade_contracts.handlers exposed every affected route as impossible.",
            "Route repository/database categories to the repo adapter even when their physical mechanism is DIRECT_LIVE; retain fail-closed status until the live probes exist.",
            ["test_every_requirement_has_exactly_one_known_execution_route"],
            "FIXED",
        ),
        _finding(
            "ROUTE-004",
            "CRITICAL",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "DIRECT_LIVE"
                and requirements[name].get("category") in {36, 64}
            ],
            [
                "distributed-backend/tests/property-tests/infra/generate_contract_manifests.py",
                "distributed-backend/tests/property-tests/e2e/tests/test_catalog_registry.py",
            ],
            "direct-live fault categories were assigned the trade adapter instead of the fault adapter",
            "Resolving any affected fail-closed semantic blocker would make execution enter an unsupported trade category rather than its synchronized fault driver.",
            "The strengthened route-integrity meta-test failed on category 64 and manifest inspection found the same defect in category 36.",
            "Route direct-live categories 36 and 64 through the fault adapter and validate direct routes against the complete fault category set.",
            ["test_every_requirement_has_exactly_one_known_execution_route"],
            "FIXED",
        ),
        _finding(
            "META-001",
            "MEDIUM",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "DIRECT_LIVE"
                and requirements[name]["runner"] == "repo"
            ],
            ["distributed-backend/tests/property-tests/e2e/tests/test_catalog_registry.py"],
            "route-integrity meta-test allowed the repository adapter only for categories 26 and 50",
            "Regenerating canonical routes correctly exposed every other live repository/database category as a false-red infrastructure failure.",
            "The regenerated meta suite failed first on category 80 even though both the mode registry and repository adapter support that category.",
            "Validate every direct runner against its complete concrete adapter category set rather than a special-case category pair.",
            ["repository/catalog meta suite"],
            "FIXED",
        ),
        _finding(
            "META-002",
            "HIGH",
            [
                name
                for name in all_names
                if requirements[name]["runner"] in {"repo", "fault"}
                and requirements[name]["mechanism"] in {"DIRECT_LIVE", "REPOSITORY_STATIC"}
            ],
            ["distributed-backend/tests/property-tests/e2e/tools/verify_generated_catalog.py"],
            "standalone catalog verifier retained obsolete runner vocabulary and excluded direct repository/fault adapters",
            "The canonical manifest could be internally consistent while the required zero-dependency verifier false-reded hundreds of valid routes.",
            "Strict verifier execution rejected repo for repository-static contracts, repo for live database contracts, and fault for live synchronized-fault contracts.",
            "Use the canonical repo runner vocabulary, allow every direct adapter, and independently join each direct route to the category sets parsed from modes.py.",
            ["verify_generated_catalog.py", "repository/catalog meta suite"],
            "FIXED",
        ),
        _finding(
            "ORACLE-001",
            "MEDIUM",
            [
                "test_proposed_test_names_contain_no_placeholder_words_todo_fixme_or_tbd",
                "test_proposed_test_names_do_not_encode_two_alternative_expected_outcomes_with_rejected_or_accepted_wording",
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py"],
            "naming policy oracles included their own necessarily self-referential contract identities",
            "The deterministic properties always false-red because their own names define the forbidden token patterns.",
            "The implemented static subset failed twice with each policy contract itself as the sole offending name.",
            "Exclude only each policy contract's own identity while continuing to scan every other proposed name.",
            ["targeted category-92 static execution"],
            "FIXED",
        ),
        _finding(
            "ORACLE-002",
            "HIGH",
            ["test_test_catalog_fails_when_referenced_test_name_no_longer_exists_in_collected_suite"],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py"],
            "test-reference oracle treated every test_* prose token as an executable pytest node selector",
            "Examples, historical reports, file stems, and reusable agent documentation produced false stale-reference failures.",
            "Targeted execution reported unrelated prose from AGENTS.md, changes/, .o11y/, driver documentation, and Dagger fixtures.",
            "Scan executable workflow/action/script sources and extract only ::node and explicit pytest selector references; catalog equality remains independently verified.",
            ["targeted category-91 static execution", "verify_collection.py"],
            "FIXED",
        ),
        _finding(
            "ORACLE-003",
            "HIGH",
            ["test_proposed_test_names_using_retry_identify_whether_business_effect_may_repeat"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py",
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
            ],
            "retry naming oracle used an incomplete token whitelist as a semantic business-effect classifier",
            "Valid names describing no resurrection, one durable result, bounded scheduling, or metric behavior false-red; the same heuristic could accept semantically vague names containing a magic token.",
            "Targeted category-92 execution produced twenty immediate counterexamples to the token whitelist.",
            "Mark the contract SEMANTIC_ORACLE_REQUIRED until every retry-bearing name has a reviewed business-effect classification; strict mode now fails closed.",
            ["test_every_unimplemented_contract_has_an_exhaustive_blocker_record"],
            "MITIGATED_FAIL_CLOSED",
        ),
        _finding(
            "ORACLE-004",
            "HIGH",
            ["test_proposed_test_names_using_crash_identify_crash_window_and_post_restart_invariant"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py",
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
            ],
            "crash naming oracle used a token whitelist as a semantic window/recovery classifier",
            "Valid fuzz-regression, no-crash pressure, and stale-lease transfer names false-red; magic window tokens could also false-green vague contracts.",
            "Targeted category-92 execution produced three counterexamples to the token whitelist.",
            "Mark the contract SEMANTIC_ORACLE_REQUIRED until crash-bearing names have a reviewed window and post-restart invariant mapping.",
            ["test_every_unimplemented_contract_has_an_exhaustive_blocker_record"],
            "MITIGATED_FAIL_CLOSED",
        ),
        _finding(
            "ORACLE-005",
            "HIGH",
            ["test_every_settlement_operation_enum_value_has_success_and_rejection_test_reference"],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py",
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
            ],
            "enum traceability oracle treated one textual token occurrence as proof of both success and rejection coverage",
            "Even a green result could not establish the two required test references; the current checkout also lacks token references for fifteen enum values.",
            "Targeted category-91 execution exposed the missing values and source inspection exposed the one-occurrence oracle.",
            "Mark the contract SEMANTIC_ORACLE_REQUIRED until an independent enum-to-success-and-rejection-test matrix is implemented.",
            ["test_every_unimplemented_contract_has_an_exhaustive_blocker_record"],
            "MITIGATED_FAIL_CLOSED",
        ),
        _finding(
            "ORACLE-006",
            "CRITICAL",
            [
                name
                for name in all_names
                if requirements[name].get("category") in {26, 50}
                and requirements[name]["mechanism"] == "DIRECT_LIVE"
            ],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py",
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
            ],
            "database rejection contracts delegated exact invalid-row construction to generic external evidence",
            "A migration token or scenario-shaped response could not prove PostgreSQL rejected the named direct write or foreign-key violation without side effects.",
            "_database_live_check sends every category-26/50 contract to runtime.evidence rather than constructing its promised row mutation.",
            "Mark every affected contract SEMANTIC_ORACLE_REQUIRED until schema-specific transactional mutation probes and persisted-state oracles exist.",
            ["test_every_unimplemented_contract_has_an_exhaustive_blocker_record"],
            "MITIGATED_FAIL_CLOSED",
        ),
        _finding(
            "HYPO-003",
            "HIGH",
            [
                "test_random_domain_valid_issue_accept_cancel_sequence_preserves_total_isk",
                "test_random_domain_valid_issue_accept_cancel_sequence_preserves_total_items",
            ],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/strategies.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/trade_contracts.py",
            ],
            "valid-sequence conservation strategies could shrink to retry/invalid-only no-op examples",
            "A green minimal example proved only that an untouched fixture conserved state; it did not cross an issue/accept/cancel transition.",
            "Strategy inspection showed an unconstrained operation list while the runner ignores symbols whose state preconditions are absent.",
            "Require every generated example to begin with a real issue and then a valid accept or cancel before any generated continuation.",
            ["strategy unit/meta suite", "compile verification; live execution requires the E2E deployment"],
            "FIXED",
        ),
        _finding(
            "ORACLE-007",
            "HIGH",
            [
                "test_accept_does_not_merge_into_item_escrow_backing_stack",
                "test_accept_does_not_merge_into_seller_source_stack",
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/trade_contracts.py"],
            "merge-safety properties greened on rejection of deliberately invalid explicit destination IDs",
            "Ownership/not-found rejection never exercised successful automatic destination selection or observed where accepted items were persisted.",
            "Both old branches assigned a forbidden destination and shared the generic rejection oracle.",
            "Execute a successful accept with automatic destination selection and independently compare seller stack, escrow, buyer aggregate, and escrow-ID nonappearance in item_stack.",
            ["source/compile verification; live execution requires the E2E deployment"],
            "FIXED",
        ),
        _finding(
            "ORACLE-008",
            "HIGH",
            [
                "test_accept_plan_caused_by_capsuleer_id_is_buyer_capsuleer_id",
                "test_accept_plan_intent_is_accept",
                "test_cancel_plan_caused_by_capsuleer_id_is_cancelling_seller_capsuleer_id",
                "test_cancel_plan_intent_is_cancel",
                "test_issue_plan_caused_by_capsuleer_id_is_seller_capsuleer_id",
                "test_issue_plan_intent_is_issue",
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/trade_contracts.py"],
            "implemented plan oracles fell back to generic external evidence when the required database column was absent",
            "A schema regression could bypass the authoritative PostgreSQL assertion instead of failing the property.",
            "Control-flow inspection found runtime.evidence fallbacks on missing intent and caused_by_capsuleer_id fields.",
            "Make both columns mandatory inputs to the live oracle and fail immediately when either authoritative field is absent.",
            ["implemented-route meta-test", "compile verification; live execution requires the E2E deployment"],
            "FIXED",
        ),
        _finding(
            "ORACLE-009",
            "HIGH",
            [
                "test_success_response_never_contains_internal_database_primary_keys_not_in_public_contract",
                "test_validation_error_identifies_invalid_public_field_without_echoing_secret_fields",
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/edge_contracts.py"],
            "public-response checks omitted nested/camelized primary keys and did not place an actual transport secret near the error path",
            "A response could leak settlementBatchId or a nested primary key, while the error check merely searched an unrelated direct gRPC message for the word secret.",
            "The old key comparison used only top-level snake_case keys; the validation branch never used edge credentials.",
            "Walk every nested response key, compare snake_case and lowerCamel primary-key forms, and drive an authenticated invalid UDP request while asserting the real signing secret is absent.",
            ["source/compile verification; live execution requires the E2E deployment"],
            "FIXED",
        ),
        _finding(
            "ORACLE-010",
            "HIGH",
            [
                "test_cancelled_trade_has_zero_remaining_quantity_and_zero_bound_item_escrow_quantity",
                "test_completed_settlement_batch_has_zero_failed_steps",
                "test_completed_trade_has_zero_remaining_quantity_and_zero_bound_item_escrow_quantity",
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/trade_contracts.py"],
            "terminal-state properties checked zero quantities or failed-step count without asserting the named terminal state",
            "An OPEN batch/trade with zero quantities could satisfy the weaker oracle.",
            "Each branch omitted its COMPLETED or CANCELLED state assertion; the batch branch also allowed an empty step set.",
            "Require the exact terminal state and a nonempty settlement-step observation before evaluating the named zero-state invariant.",
            ["source/compile verification; live execution requires the E2E deployment"],
            "FIXED",
        ),
        _finding(
            "ORACLE-011",
            "HIGH",
            ["test_ci_evidence_command_identity_is_unique_for_each_required_verification_job"],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py"],
            "command-identity uniqueness scanned a flat list without proving every required verification job supplied one",
            "One well-formed identity could green even if other evidence-producing verification jobs omitted their identity entirely.",
            "The old oracle extracted only command-identity text and never joined it to workflow jobs or their start/run/finish evidence steps.",
            "Enumerate each evidence-producing job, require exactly one finish step with a nondefault identity, then prove job-level uniqueness.",
            ["repository/static meta suite"],
            "FIXED",
        ),
        _finding(
            "ORACLE-012",
            "HIGH",
            [
                "test_settlement_result_consumer_uses_non_ephemeral_channel_for_correctness_critical_results",
                "test_settlement_worker_uses_non_ephemeral_channel_for_correctness_critical_work",
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py"],
            "durable-channel oracles searched one repository-wide blob for both channel names and the absence of #ephemeral",
            "Either named consumer could be disconnected from its durable channel while unrelated source/config text kept both properties green.",
            "The old branch did not join the consumer's NewSubscription call to its topic symbol, physical topic name, or each self-host NSQ configuration.",
            "For each consumer, prove its exact source binding, topic declaration, and unique matching subscription in every NSQ self-host configuration; reject ephemeral names at each binding.",
            ["targeted category-77 static execution", "source/compile verification"],
            "FIXED",
        ),
        _finding(
            "ORACLE-013",
            "CRITICAL",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "LITMUS_CHAOS"
                and requirements[name]["implementation_status"] == "IMPLEMENTED"
            ],
            ["distributed-backend/tests/property-tests/infra/litmus_driver.py"],
            "cleanup evidence asserted target_effect_active=false after deleting resources without independently probing that the physical effect was absent",
            "A stale qdisc or partially recovered replica set could satisfy resource-absence checks and make cleanup/recovery properties green while impairment remained.",
            "The old cleanup branch populated the false value directly; pre-cleanup recovery also accepted only one successful probe out of three.",
            "Require every recovery probe to succeed, then run bounded post-cleanup probes, compare latency with the clean baseline, require full workload readiness, and only then record effect absence.",
            ["test_post_cleanup_oracle_rejects_residual_network_effect", "hostile residual-effect evidence mutation", "compile verification; live Litmus execution remains environment-blocked"],
            "FIXED",
        ),
        _finding(
            "ORACLE-014",
            "CRITICAL",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "LITMUS_CHAOS"
                and requirements[name]["implementation_status"] == "IMPLEMENTED"
            ],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/chaos_oracles.py",
            ],
            "independent validation did not bind the raw ChaosEngine target or physical pod transition to the declared selected resources",
            "A collector could claim selected EVE Trade labels at the top level while its raw engine or UID transition targeted an unrelated workload.",
            "The old validator checked only the top-level selector and labels on claimed resources; it never inspected ChaosEngine appinfo or joined transition before_uids to selected UIDs.",
            "Validate execution labels, exact raw appinfo, service account, experiment identity, and require physical transition UIDs to equal the independently label-validated selected resource UIDs.",
            ["hostile wrong-ChaosEngine-target canary", "hostile unrelated-transition-UID canary"],
            "FIXED",
        ),
        _finding(
            "ORACLE-015",
            "HIGH",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "LITMUS_CHAOS"
                and requirements[name]["implementation_status"] == "IMPLEMENTED"
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py"],
            "final chaos evidence accepted a Litmus phase of Running",
            "An unfinished experiment with a prematurely populated verdict could pass phase validation without proving completion/revert semantics.",
            "The phase guard explicitly accepted both Running and Completed despite validation occurring only after the driver's bounded completion wait.",
            "Require Completed in both the top-level binding and raw ChaosResult before evaluating recovery or business oracles.",
            ["hostile Running-phase evidence canary"],
            "FIXED",
        ),
        _finding(
            "ORACLE-016",
            "CRITICAL",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "LITMUS_CHAOS"
                and requirements[name]["implementation_status"] == "IMPLEMENTED"
            ],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py"],
            "generated Litmus parameters were compared only with collector-authored applied_parameters, not the raw ChaosEngine environment",
            "A driver could inject a different duration/loss/latency than Hypothesis generated and still pass whenever the different fault happened to satisfy a weaker physical threshold.",
            "The old raw-engine validation checked neither experiment components nor environment values.",
            "Parse the raw ChaosEngine environment, reject duplicate variables, bind every static/generated Litmus variable to the exact case/namespace, and bind start_offset_ms to raw engine creation and activation timestamps.",
            ["hostile wrong-generated-ChaosEngine-environment canary", "hostile start-offset timestamp canary"],
            "FIXED",
        ),
        _finding(
            "ORACLE-017",
            "HIGH",
            [
                name
                for name in all_names
                if requirements[name]["mechanism"] == "LITMUS_CHAOS"
                and requirements[name]["implementation_status"] == "IMPLEMENTED"
            ],
            [
                "distributed-backend/tests/property-tests/infra/litmus_driver.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/evidence_integrity.py",
            ],
            "recovery deadline evidence was not derived from the generated recovery_deadline_seconds value",
            "A collector could report an arbitrarily distant deadline and make a slow recovery satisfy the bounded-recovery check.",
            "Validation compared completed_at with collector-supplied deadline_at but had no recovery start timestamp or duration join.",
            "Record recovery.started_at and require deadline_at to equal that timestamp plus the exact generated deadline before checking completion.",
            ["hostile extended-recovery-deadline canary"],
            "FIXED",
        ),
        _finding(
            "PIN-001",
            "HIGH",
            by_mechanism["LITMUS_CHAOS"],
            [
                "distributed-backend/tests/property-tests/infra/generate_contract_manifests.py",
                "distributed-backend/tests/property-tests/infra/install_litmus.py",
            ],
            "Litmus manifest recorded only the 3.31.0 release while the pinned checkout's installed litmus-agent chart and operator images are 3.30.0",
            "Evidence provenance could misleadingly identify the release tag without identifying the chart and images actually installed for every chaos contract.",
            "The exact litmus-core-3.31.0 checkout declares litmus-agent chart 3.30.0 and chaos-operator appVersion 3.30.0.",
            "Record release tag, installed chart/version, operator tag, and runner tag separately; make the installer reject any manifest or checked-out Chart.yaml mismatch.",
            ["manifest generation check", "source/compile verification"],
            "FIXED",
        ),
        _finding(
            "PIPE-001",
            "HIGH",
            all_names,
            [
                "distributed-backend/tests/property-tests/infra/orchestrate.py",
                "distributed-backend/tests/property-tests/infra/pipeline.py",
            ],
            "failed stages raised before being recorded and Dagger aborted before exporting failure artifacts",
            "A red run could leave summary.json without the failed stage or keep the only evidence inside an exited container.",
            "Control-flow inspection followed the nonzero subprocess path through pytest_stage and Dagger with_exec.",
            "Append stage evidence before raising, use ReturnType.ANY, export/retain artifacts, then propagate the nonzero status; add a nested-pytest failure canary.",
            ["test_failed_pytest_stage_returns_persistable_evidence_before_pipeline_abort"],
            "FIXED",
        ),
        _finding(
            "PIPE-002",
            "CRITICAL",
            by_mechanism["DIRECT_LIVE"]
            + by_mechanism["LITMUS_CHAOS"]
            + by_mechanism["PLATFORM_EXTERNAL"],
            ["distributed-backend/tests/property-tests/infra/pipeline.py"],
            "integration credentials were forwarded as ordinary Dagger environment values",
            "Database URLs and edge signing secrets could appear in DAG metadata or diagnostic output.",
            "Source inspection found all FORWARDED_ENV values passed through with_env_variable.",
            "Forward every externally supplied integration setting through Dagger Secret objects consumed directly by the Dagger execution container.",
            ["pipeline source inspection"],
            "FIXED",
        ),
        _finding(
            "PIPE-003",
            "HIGH",
            all_names,
            ["distributed-backend/tests/property-tests/infra/pipeline.py"],
            "supplied-cluster mode unnecessarily depended on a host Unix Docker socket and nested Docker",
            "The advertised first-class fallback could not run on hosts where nested Docker/Unix-socket mounting is unavailable.",
            "Both Kind and supplied branches passed through the same unconditional Docker socket check.",
            "Run supplied-cluster orchestration directly in a Dagger container with an isolated kubeconfig and exported artifact directory; scope the Docker socket requirement only to disposable Kind.",
            ["compile/source verification; live supplied-cluster execution requires an external disposable cluster"],
            "FIXED",
        ),
        _finding(
            "PIPE-004",
            "HIGH",
            all_names,
            ["distributed-backend/tests/property-tests/infra/pipeline.py"],
            "Dagger source root was derived from the caller's current working directory",
            "Invoking the documented script from its own directory or a CI wrapper directory could mount only that subtree and make every later build/test stage fail or inspect the wrong files.",
            "A direct invocation from property-tests/infra resolved REPO_ROOT to that directory rather than the checkout root.",
            "Resolve the checkout root from pipeline.py's stable repository location and add a meta-test independent of the test process working directory.",
            ["test_dagger_pipeline_repository_root_is_independent_of_caller_working_directory"],
            "FIXED",
        ),
        _finding(
            "PIPE-005",
            "HIGH",
            all_names,
            ["distributed-backend/tests/property-tests/infra/pipeline.py"],
            "Kind mode validated a Dagger source snapshot but executed tests from a second mutable host bind mount inside nested Docker",
            "Concurrent host edits or mount/path differences could make structural evidence and runtime evidence describe different source trees.",
            "Control-flow inspection found validate_source consuming the Dagger Directory while docker run mounted REPO_ROOT independently as /src.",
            "Copy the validated Dagger Directory into the host-networked Kind runner through the Docker API instead of bind-mounting REPO_ROOT, then copy and export failure artifacts through Dagger.",
            ["test_kind_pipeline_executes_the_validated_dagger_source_snapshot"],
            "FIXED",
        ),
        _finding(
            "SCOPE-001",
            "HIGH",
            by_mechanism["REPOSITORY_STATIC"],
            ["distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/repo.py"],
            "recursive repository scans traversed ignored generated dependency trees",
            "Static properties could false-red on vendored Terraform provider modules rather than EVE Trade source.",
            "The secret-variable contract failed on .terraform provider source; the same contract passed after excluding generated/cache roots.",
            "Centralized repository traversal exclusions for .git, .agents, .codex, .o11y, .terraform, virtual environments, Dagger caches, node_modules, target, and Python caches.",
            ["targeted secret-variable property passed after repair", "targeted catalog-reference property no longer scans .agents"],
            "FIXED",
        ),
        _finding(
            "SCOPE-002",
            "CRITICAL",
            [
                name
                for name in all_names
                if requirements[name].get("category") in {35, 65, 84}
            ],
            [
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/repo.py",
                "distributed-backend/tests/property-tests/e2e/eve_trade_hypothesis/contracts/repo_contracts.py",
            ],
            "Kubernetes properties omitted the production orchestration tree and allowed an empty workload set",
            "CI YAML made the document list nonempty while pod_specs yielded nothing, so universal security assertions passed vacuously.",
            "Path inspection showed kubernetes_documents lacked distributed-backend/orchestration/kubernetes; control-flow inspection showed no pod-count assertion.",
            "Render every leaf Kustomize target from the real orchestration tree, reject empty workload/container sets, and evaluate effective pods rather than patch fragments.",
            ["catalog/meta suite", "targeted Windows render attempted; kubectl child process was blocked by local filesystem policy"],
            "FIXED",
        ),
        _finding(
            "COVERAGE-001",
            "LOW",
            all_names,
            [
                "distributed-backend/tests/property-tests/infra/test-requirements.json",
                "distributed-backend/tests/property-tests/e2e/HYPOTHESIS_AUDIT.json",
            ],
            "there was no exhaustive per-name audit/requirement join",
            "Systemic defects could be documented only with representative examples.",
            "The authoritative catalog, generated collection, requirements, and audit affected-name sets are programmatically joined.",
            "Generated an exact route/status record for every name and full affected-name arrays for every systemic finding.",
            ["test_authoritative_catalog_registry_and_requirement_names_are_exactly_equal", "generate_hypothesis_audit.py --check"],
            "FIXED",
        ),
    ]
    findings.extend(systemic)
    finding_ids=[item["id"] for item in findings]
    if len(finding_ids)!=len(set(finding_ids)):
        duplicates=sorted({item for item in finding_ids if finding_ids.count(item)>1})
        raise RuntimeError(f"duplicate Hypothesis audit finding IDs: {duplicates}")
    findings.sort(key=lambda item: item["id"])
    summary = {
        "finding_count": len(findings),
        "severity_counts": dict(sorted(Counter(item["severity"] for item in findings).items())),
        "status_counts": dict(sorted(Counter(item["final_status"] for item in findings).items())),
        "fixed_findings": sum(item["final_status"] == "FIXED" for item in findings),
        "remaining_findings": sum(item["final_status"] != "FIXED" for item in findings),
        "audited_contract_count": len(all_names),
        "unimplemented_contract_count": sum(
            requirement["implementation_status"]
            not in {"IMPLEMENTED", "JUSTIFIED_NON_APPLICABLE"}
            for requirement in requirements.values()
        ),
    }
    return {
        "schema_version": "eve-trade.hypothesis-audit/v1",
        "summary": summary,
        "source_manifests": [str(LEGACY.relative_to(PROPERTY_ROOT)), str(SEMANTIC.relative_to(PROPERTY_ROOT)), str(REQUIREMENTS.relative_to(PROPERTY_ROOT))],
        "findings": findings,
    }


def IMPLEMENTED_CHAOS_NAMES(requirements: dict[str, dict[str, Any]]) -> list[str]:
    return [
        name
        for name, requirement in requirements.items()
        if requirement["mechanism"] == "LITMUS_CHAOS"
        and requirement["implementation_status"] == "IMPLEMENTED"
    ]


def markdown(document: dict[str, Any]) -> str:
    summary = document["summary"]
    lines = [
        "# Hypothesis Suite Audit",
        "",
        "This audit is generated from the authoritative catalog, the two source-audit manifests, and the canonical execution requirements. The JSON companion contains every exact affected test name; this Markdown intentionally does not duplicate thousand-name arrays.",
        "",
        "## Current result",
        "",
        f"- Audited contracts: {summary['audited_contract_count']}",
        f"- Finding records: {summary['finding_count']}",
        f"- Fixed findings: {summary['fixed_findings']}",
        f"- Remaining or partially mitigated findings: {summary['remaining_findings']}",
        f"- Fail-closed/unimplemented contracts: {summary['unimplemented_contract_count']}",
        "",
        "A green implemented test is evidence-bearing. Contracts without a named independent oracle are deliberately marked `eve_unimplemented` and fail in strict full-catalog execution. That is a mitigation, not completion.",
        "",
        "## Findings",
        "",
        "| ID | Severity | Status | Affected | Mechanism |",
        "|---|---:|---|---:|---|",
    ]
    for finding in document["findings"]:
        mechanism = finding["failure_mechanism"].replace("|", "\\|")
        lines.append(
            f"| {finding['id']} | {finding['severity']} | {finding['final_status']} | "
            f"{len(finding['affected_test_names'])} | {mechanism} |"
        )
    lines.extend(
        [
            "",
            "## Exhaustive details",
            "",
            "See `HYPOTHESIS_AUDIT.json` for each finding's full affected-name set, source files, proof, repair, verification, and final status. See `../infra/test-requirements.json` for every remaining exact contract and physical blocker.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    document = generate()
    outputs = {
        OUTPUT_JSON: json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        OUTPUT_MD: markdown(document),
    }
    stale = [str(path) for path, content in outputs.items() if not path.exists() or path.read_text(encoding="utf-8") != content]
    if args.check:
        if stale:
            raise SystemExit("stale generated Hypothesis audit: " + ", ".join(stale))
    else:
        for path, content in outputs.items():
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
