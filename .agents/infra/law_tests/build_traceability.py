from __future__ import annotations

"""Build the deterministic requirement-to-execution traceability registry.

This script deliberately does not infer a pass from the presence of a test name.  Until an outer-runner
ledger is supplied, executable requirements remain pending.  A final ledger can promote only completed,
non-vacuous PASS records or explicitly classified host limitations.
"""

import argparse
import ast
import hashlib
import json
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
INFRA = HERE.parent
if str(INFRA) not in sys.path:
    sys.path.insert(0, str(INFRA))

from agentinfra.law_runtime import resolve_law_layout


ENTRY_RE = re.compile(r"^\s*-\s+`([^`]+)`\s*$")
FINAL_STATUSES = {
    "EXISTING_AND_VERIFIED",
    "IMPLEMENTED_AND_PASSING",
    "SUPERSEDED_BY_STRONGER_EQUIVALENT_AND_PROVEN",
    "NOT_APPLICABLE_WITH_CONCRETE_JUSTIFICATION",
    "BLOCKED_BY_PROVEN_EXTERNAL_LIMITATION",
}
SEALED_RECORD_FIELDS = (
    "required_capability",
    "outcome",
    "capability_status",
    "oracle_count",
    "detail",
    "justification",
    "scenario_evidence_digest",
    "stronger_equivalent",
    "required_observations",
    "definition_sha256_before",
    "definition_sha256_after",
)


FAMILY = {
    "00": ("legacy-python", "regression", "unit"),
    "01": ("legacy-builtins", "law-runner", "law"),
    "02": ("law-template", "law-runner", "meta-law"),
    "10": ("release", "distribution", "filesystem-law"),
    "11": ("atomic-transaction", "transactions", "fault-law"),
    "12": ("bootstrap", "bootstrap", "lifecycle-law"),
    "13": ("state-identity", "state", "state-machine-law"),
    "14": ("workflow", "state", "state-machine-law"),
    "15": ("gates-risks-decisions", "state", "state-machine-law"),
    "16": ("evidence", "evidence", "provenance-law"),
    "17": ("workspace", "workspace", "filesystem-law"),
    "18": ("subagents", "subagents", "race-law"),
    "19": ("context", "context-cache", "cache-law"),
    "20": ("law-runner", "law-runner", "process-law"),
    "21": ("lawlib", "law-library", "property-law"),
    "22": ("modules", "modules", "module-law"),
    "23": ("codex-static", "codex", "adapter-law"),
    "24": ("codex-live", "codex", "live-host-law"),
    "25": ("xonsh", "shells", "host-law"),
    "26": ("python-meta", "python-meta", "extension-law"),
    "27": ("cli", "cli", "process-law"),
    "28": ("persistent-recovery", "bootstrap", "recovery-law"),
    "29": ("security", "security", "security-law"),
    "30": ("portability", "portability", "platform-law"),
    "31": ("reasoning-cost", "policy", "policy-law"),
    "32": ("policy-consistency", "policy", "meta-law"),
    "33": ("fault-race", "transactions", "fault-law"),
    "34": ("mutation", "test-integrity", "mutation-law"),
    "35": ("migration", "migration", "compatibility-law"),
    "36": ("performance", "performance", "resource-law"),
    "37": ("end-to-end", "assurance", "end-to-end-law"),
}


# Exact, reviewable capability exceptions.  Every unlisted requirement is
# portable and therefore must execute and PASS on every supported host.  Do not
# infer capabilities from words in a test name: negative-path laws frequently
# mention a missing executable or another platform while remaining fully local.
CAPABILITY_REQUIREMENTS = {
    "codex-current-cli-schema": {
        "test_codex_adapter_uses_only_current_officially_documented_config_keys_or_feature_gated_probed_keys",
        "test_codex_adapter_parent_reasoning_effort_is_exact_max_when_current_codex_supports_max",
        "test_codex_adapter_subagent_default_reasoning_effort_is_exact_max_when_current_codex_supports_max",
        "test_codex_current_cli_parses_installed_project_config_without_unknown_key_warning_or_error",
    },
    "codex-live-effective-metadata": {
        "test_codex_current_cli_project_is_trusted_before_project_scoped_aegis_config_is_considered_effective",
        "test_codex_effective_parent_model_is_gpt_5_6_sol",
        "test_codex_effective_parent_reasoning_effort_is_max",
        "test_codex_effective_spawned_child_model_is_gpt_5_6_sol",
        "test_codex_effective_spawned_child_reasoning_effort_is_max",
        "test_codex_explicit_spawn_model_override_cannot_bypass_gpt_5_6_sol_invariant",
        "test_codex_explicit_spawn_reasoning_override_cannot_bypass_max_invariant",
        "test_codex_only_aegis_registered_spawn_paths_are_permitted_when_strict_mode_is_enabled",
        "test_codex_runtime_never_has_more_than_one_spawned_child_thread_open",
        "test_codex_attempt_to_spawn_second_child_is_rejected_while_first_child_is_open",
        "test_codex_child_cannot_spawn_grandchild",
        "test_codex_nested_delegation_is_runtime_proven_without_relying_on_undocumented_config_key",
        "test_codex_parent_cli_override_cannot_silently_lower_reasoning_below_max",
        "test_codex_parent_profile_override_cannot_silently_lower_reasoning_below_max",
        "test_codex_user_config_override_cannot_silently_lower_reasoning_below_max",
        "test_codex_managed_config_or_runtime_probe_detects_higher_precedence_override_and_fails_closed",
        "test_codex_read_only_role_effective_sandbox_is_read_only_under_parent_runtime_overrides",
        "test_codex_implementer_effective_sandbox_is_no_more_permissive_than_declared_policy",
        "test_codex_child_approval_policy_does_not_silently_inherit_more_permissive_parent_setting",
        "test_codex_runtime_verifier_records_codex_version_and_effective_config_proof",
        "test_codex_sequential_lease_and_codex_native_thread_limit_agree_under_real_spawn_race",
        "test_codex_child_handoff_is_completed_before_real_second_spawn_is_allowed",
        "test_codex_custom_role_instructions_are_loaded_in_actual_spawned_child",
        "test_end_to_end_codex_parent_and_child_are_live_verified_gpt_5_6_sol_max_or_run_fails_closed",
        "test_end_to_end_codex_nested_delegation_attempt_is_rejected",
        "test_end_to_end_codex_second_concurrent_child_attempt_is_rejected",
        "test_host_adapter_proves_no_unmanaged_spawn_path_can_bypass_framework_sequential_lease",
        "test_runtime_concurrency_probe_observes_never_more_than_one_spawned_child",
        "test_child_cannot_close_own_lease_and_spawn_replacement_without_parent_integration",
        "test_every_spawned_managed_subagent_uses_max_reasoning_or_spawn_fails_closed",
        "test_policy_claim_sequential_only_matches_live_host_and_global_lease_enforcement",
        "test_policy_claim_nested_delegation_false_matches_live_host_behavior",
        "test_policy_claim_max_reasoning_matches_effective_parent_and_child_runtime",
        "test_codex_untrusted_project_causes_aegis_codex_verifier_to_fail_closed",
        "test_codex_project_config_install_is_not_reported_effective_until_live_debug_config_confirms_values",
    },
    "xonsh": {
        "test_xonsh_module_detects_installed_version",
        "test_xonsh_module_enforces_minimum_supported_version_or_uses_version_compatible_verification",
        "test_xonsh_verify_executes_live_wrapper_smoke_test_when_xonsh_is_available",
        "test_xonsh_agentctl_wrapper_preserves_argument_boundaries_with_spaces",
        "test_xonsh_agentctl_wrapper_preserves_unicode_arguments",
        "test_xonsh_agentctl_wrapper_preserves_quotes_and_metacharacters_without_injection",
        "test_xonsh_agentctl_wrapper_propagates_exit_code_exactly",
        "test_xonsh_agentctl_wrapper_uses_same_python_interpreter_version_required_by_framework",
        "test_xonsh_rc_source_is_idempotent",
        "test_xonsh_rc_source_does_not_duplicate_aliases_or_path_entries",
        "test_xonsh_rc_source_does_not_overwrite_unrelated_user_environment_variables",
        "test_xonsh_rc_source_sets_aegis_root_to_canonical_project_root",
        "test_xonsh_environment_switch_preserves_working_directory",
        "test_xonsh_environment_switch_preserves_only_expected_environment_state",
        "test_xonsh_live_smoke_test_runs_agentctl_doctor_successfully",
        "test_all_shell_wrappers_preserve_exit_status_and_argument_vector_equivalently",
        "test_end_to_end_xonsh_interactive_path_matches_direct_cli_semantics",
    },
    "mcpyrate-and-unpythonic": {
        "test_python_meta_verify_imports_mcpyrate_not_only_find_spec",
        "test_python_meta_verify_imports_unpythonic_not_only_find_spec",
        "test_python_meta_verify_rejects_incompatible_mcpyrate_version",
        "test_python_meta_verify_rejects_incompatible_unpythonic_version",
        "test_python_meta_sample_extension_compiles_and_executes_with_mcpyrate",
        "test_python_meta_sample_extension_executes_with_unpythonic",
        "test_python_meta_import_hook_is_scoped_and_does_not_mutate_unrelated_project_import_semantics",
        "test_python_meta_extension_generated_code_is_auditable_and_deterministic",
        "test_end_to_end_python_meta_enabled_and_disabled_paths_preserve_same_core_invariants",
    },
    "windows": {
        "test_framework_runs_on_supported_windows_python_version",
        "test_framework_handles_windows_drive_letter_paths",
        "test_framework_handles_unc_paths_when_supported",
    },
    "linux": {"test_framework_runs_on_supported_linux_python_version"},
    "macos": {"test_framework_runs_on_supported_macos_python_version"},
    "posix-permissions": {
        "test_atomic_write_preserves_existing_posix_permission_bits",
        "test_atomic_write_preserves_required_executable_bit",
        "test_release_archive_roundtrip_preserves_required_executable_mode_bits",
        "test_persistent_install_backup_permissions_are_preserved",
        "test_framework_runtime_control_files_are_not_group_or_world_writable_by_default",
    },
    "filesystem-symlink": {
        "test_framework_manifest_rejects_symlinked_immutable_files",
        "test_atomic_write_rejects_target_symlink_when_policy_requires_real_file",
        "test_atomic_write_rejects_parent_symlink_escape_when_policy_requires_root_confinement",
        "test_bootstrap_install_rejects_destination_symlink_escape",
        "test_state_store_rejects_symlinked_task_directory_escape",
        "test_state_store_rejects_symlinked_state_file_escape",
        "test_workspace_fingerprint_changes_when_relevant_symlink_target_text_changes",
        "test_workspace_fingerprint_detects_declared_external_symlink_dependency_content_change",
        "test_workspace_fingerprint_non_git_tree_rejects_symlink_escape_or_tracks_external_dependency_explicitly",
        "test_context_cache_file_entry_becomes_stale_after_symlink_retarget",
        "test_persistent_install_backup_is_never_symlink_followed_outside_framework_control",
        "test_framework_rejects_symlinked_control_files_that_escape_repository",
    },
    "external-state-anchor": {
        "test_state_history_is_cryptographically_or_externally_anchored_against_rewrite_when_claimed_append_only",
        "test_evidence_chain_has_external_or_task_state_anchor_preventing_full_chain_rewrite",
    },
    "windows-and-posix-matrix": {
        "test_framework_handles_case_insensitive_filesystem_without_duplicate_identity",
        "test_framework_handles_case_sensitive_filesystem_without_path_alias_confusion",
        "test_framework_archive_extracts_and_selftests_on_windows_and_posix",
    },
    "full-platform-matrix": {
        "test_full_adversarial_mutation_fault_injection_cross_platform_and_live_host_matrix_passes",
    },
}

CAPABILITY_RESULT_POLICY = {
    "portable": {("PASS", "AVAILABLE")},
    "codex-current-cli-schema": {
        ("PASS", "AVAILABLE"),
        ("UNAVAILABLE", "MISSING"),
        ("UNAVAILABLE", "INCOMPATIBLE"),
        ("UNAVAILABLE", "UNOBSERVABLE"),
    },
    "codex-live-effective-metadata": {
        ("PASS", "AVAILABLE"),
        ("UNAVAILABLE", "MISSING"),
        ("UNAVAILABLE", "INCOMPATIBLE"),
        ("UNAVAILABLE", "UNTRUSTED"),
        ("UNAVAILABLE", "UNOBSERVABLE"),
    },
    "xonsh": {("PASS", "AVAILABLE"), ("UNAVAILABLE", "MISSING"), ("UNAVAILABLE", "INCOMPATIBLE")},
    "mcpyrate-and-unpythonic": {("PASS", "AVAILABLE"), ("UNAVAILABLE", "MISSING"), ("UNAVAILABLE", "INCOMPATIBLE")},
    "windows": {("PASS", "AVAILABLE"), ("NOT_APPLICABLE", "NOT_APPLICABLE")},
    "linux": {("PASS", "AVAILABLE"), ("NOT_APPLICABLE", "NOT_APPLICABLE")},
    "macos": {("PASS", "AVAILABLE"), ("NOT_APPLICABLE", "NOT_APPLICABLE")},
    "posix-permissions": {("PASS", "AVAILABLE"), ("NOT_APPLICABLE", "NOT_APPLICABLE")},
    "filesystem-symlink": {("PASS", "AVAILABLE"), ("UNAVAILABLE", "UNOBSERVABLE")},
    "external-state-anchor": {("PASS", "AVAILABLE"), ("UNAVAILABLE", "UNOBSERVABLE"), ("UNAVAILABLE", "MISSING")},
    "windows-and-posix-matrix": {("PASS", "AVAILABLE"), ("UNAVAILABLE", "UNOBSERVABLE")},
    "full-platform-matrix": {("PASS", "AVAILABLE"), ("UNAVAILABLE", "UNOBSERVABLE")},
}


# Reviewed stronger-property mappings for the constitutional source architecture.
# Each label is an executable module observation emitted by scenarios._source_assurance.
# The legacy semantic catalog remains validated as an exact 834-name bijection; these
# mappings replace obsolete deployment-self-mutation probes with current production-path
# properties without treating mere name presence as proof.
SOURCE_PROPERTY_SUBSUMPTION = {
    "10": ("property-module-test_release_source_properties", "unit-module-test_manifest"),
    "11": ("unit-module-test_atomic_transaction", "property-module-test_governance_runtime_properties"),
    "12": ("unit-module-test_bootstrap", "property-module-test_governance_runtime_properties", "property-module-test_release_source_properties"),
    "13": ("unit-module-test_state_machine", "property-module-test_tdd_lifecycle_properties", "property-module-test_state_assurance_integration_properties"),
    "14": ("property-module-test_tdd_lifecycle_properties", "unit-module-test_state_machine", "property-module-test_state_assurance_integration_properties"),
    "15": ("property-module-test_policy_compiler_properties", "unit-module-test_state_machine", "property-module-test_scope_controls_properties"),
    "16": ("property-module-test_assurance_review_properties", "property-module-test_state_assurance_integration_properties", "unit-module-test_evidence"),
    "17": ("property-module-test_discovery_integrity_properties", "property-module-test_governance_runtime_properties", "property-module-test_scope_controls_properties", "unit-module-test_workspace"),
    "18": ("unit-module-test_subagent_lease", "property-module-test_policy_compiler_properties", "property-module-test_state_assurance_integration_properties"),
    "19": ("unit-module-test_context_cache", "property-module-test_operational_runtime_properties"),
    "20": ("unit-module-test_laws", "property-module-test_law_runtime_properties", "property-module-test_law_runner_source_layout_properties"),
    "21": ("unit-module-test_lawlib",),
    "22": ("unit-module-test_modules", "property-module-test_governance_runtime_properties", "property-module-test_release_source_properties"),
    "23": ("unit-module-test_codex_config", "property-module-test_operational_runtime_properties"),
    "24": ("unit-module-test_codex_config", "property-module-test_policy_compiler_properties"),
    "25": ("unit-module-test_shell_select",),
    "26": ("unit-module-test_process_security", "property-module-test_scope_controls_properties"),
    "27": ("unit-module-test_cli_workflow", "property-module-test_operational_runtime_properties", "property-module-test_law_runtime_properties"),
    "28": ("unit-module-test_atomic_transaction", "unit-module-test_bootstrap", "property-module-test_operational_runtime_properties", "property-module-test_release_source_properties"),
    "29": ("property-module-test_governance_runtime_properties", "property-module-test_discovery_integrity_properties", "property-module-test_scope_controls_properties", "unit-module-test_process_security"),
    "30": ("property-module-test_release_source_properties", "unit-module-test_atomic_transaction", "unit-module-test_workspace"),
    "31": ("property-module-test_policy_compiler_properties", "property-module-test_law_runtime_properties", "unit-module-test_context_cache"),
    "32": ("property-module-test_policy_compiler_properties", "property-module-test_tdd_lifecycle_properties", "property-module-test_state_assurance_integration_properties", "property-module-test_final_audit_properties"),
    "33": ("unit-module-test_atomic_transaction", "property-module-test_state_assurance_integration_properties", "property-module-test_operational_runtime_properties", "unit-module-test_locks"),
    "35": ("property-module-test_operational_runtime_properties", "property-module-test_release_source_properties", "unit-module-test_migration"),
    "36": ("property-module-test_release_source_properties", "unit-module-test_context_cache", "unit-module-test_evidence", "unit-module-test_laws"),
}


def specification_roots(root: Path) -> tuple[Path, ...]:
    """Return every authoritative specification surface present in *root*.

    Release-source checkouts contain ``tests-to-impl`` at the repository root and
    mirror it into the installed ``.agents`` distribution.  A clean package
    extraction contains only the installed mirror.  When both exist, require an
    exact byte-for-byte mirror so a stale source or package copy cannot silently
    select a different 834-law inventory.
    """
    specification = resolve_law_layout(root).specification
    if not (specification / "INDEX.md").is_file():
        raise RuntimeError("Aegis law specification is missing")
    return (specification,)


def specification_root(root: Path) -> Path:
    return specification_roots(root)[0]


def project_root(start: Path) -> Path:
    for candidate in (start.resolve(), *start.resolve().parents):
        try:
            specification_roots(candidate)
        except (RuntimeError, OSError):
            continue
        return candidate
    raise RuntimeError("cannot locate Aegis repository root")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: object) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def record_evidence_digest(record: dict) -> str:
    material = {key: record.get(key) for key in SEALED_RECORD_FIELDS if record.get(key) is not None}
    return sha256_bytes(json.dumps(material, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8"))


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    out = {}
    for key, value in pairs:
        if key in out:
            raise RuntimeError(f"duplicate JSON key in ledger: {key}")
        out[key] = value
    return out


def load_semantic_catalog(root: Path, inventory_sources: dict[str, str]) -> tuple[dict[str, list[str]], str]:
    """Load and strictly validate the reviewed exact observation catalog."""
    inventory_names = set(inventory_sources)
    path = resolve_law_layout(root).law_tests / "semantic_catalog.json"
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load semantic observation catalog: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RuntimeError("semantic observation catalog has unsupported schema")
    bindings = payload.get("bindings")
    if not isinstance(bindings, dict) or payload.get("requirement_count") != len(inventory_names):
        raise RuntimeError("semantic observation catalog count is inconsistent")
    if set(bindings) != inventory_names:
        missing = sorted(inventory_names - set(bindings))
        extra = sorted(set(bindings) - inventory_names)
        raise RuntimeError(
            f"semantic observation catalog is not an exact inventory bijection; missing={missing[:5]} extra={extra[:5]}"
        )
    permitted_families = {item[0] for item in FAMILY.values()} | {
        "outer-unit",
        "outer-law",
        "outer-template",
        "source-assurance",
        "historical-integrity",
    }
    forbidden_labels = {
        "family-completed", "family-registered",
        *(item[0] for item in FAMILY.values()),
    }
    validated: dict[str, list[str]] = {}
    for name in sorted(bindings):
        values = bindings[name]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or item.count("::") != 1 for item in values)
            or len(values) != len(set(values))
        ):
            raise RuntimeError(f"semantic observation catalog has invalid bindings for {name}")
        for item in values:
            family, label = item.split("::", 1)
            if family not in permitted_families or not label or label in forbidden_labels or label.endswith("-battery"):
                raise RuntimeError(f"semantic observation catalog has forbidden generic/unknown binding for {name}: {item}")
        validated[name] = list(values)
    for name, source_file in inventory_sources.items():
        prefix = source_file[:2]
        if prefix in SOURCE_PROPERTY_SUBSUMPTION:
            validated[name] = [f"source-assurance::{label}" for label in SOURCE_PROPERTY_SUBSUMPTION[prefix]]
    effective_digest = canonical_digest(
        {"reviewed_catalog_sha256": sha256_bytes(raw), "effective_bindings": validated}
    )
    return validated, effective_digest


def inventory(root: Path) -> tuple[list[dict], dict[str, str]]:
    entries: list[dict] = []
    digests: dict[str, str] = {}
    spec_root = specification_root(root)
    for path in sorted(spec_root.glob("*.md")):
        if path.name == "INDEX.md":
            continue
        raw = path.read_bytes()
        digests[path.name] = sha256_bytes(raw)
        for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            match = ENTRY_RE.fullmatch(line)
            if match:
                entries.append({"source_file": path.name, "source_line": line_no, "name": match.group(1)})
    names = [entry["name"] for entry in entries]
    duplicates = sorted(name for name, count in Counter(names).items() if count != 1)
    if duplicates:
        raise RuntimeError(f"duplicate specification names: {duplicates}")
    if len(entries) != 834:
        raise RuntimeError(f"expected 834 specification requirements, found {len(entries)}")
    return entries, digests


def existing_test_locations(root: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    test_root = resolve_law_layout(root).unit_tests
    for path in sorted(test_root.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                    if child.name in found:
                        raise RuntimeError(f"duplicate existing unittest name: {child.name}")
                    found[child.name] = f"{path.relative_to(root).as_posix()}:{child.lineno}"
    return found


def existing_law_ids(root: Path) -> set[str]:
    with (resolve_law_layout(root).law_definitions / "framework.toml").open("rb") as stream:
        data = tomllib.load(stream)
    return {law["id"] for law in data.get("law", [])}


def capability(source_file: str, name: str) -> str:
    del source_file
    matches = [capability_name for capability_name, names in CAPABILITY_REQUIREMENTS.items() if name in names]
    if len(matches) > 1:
        raise RuntimeError(f"requirement is assigned multiple capabilities: {name}: {matches}")
    if matches:
        return matches[0]
    return "portable"


def validate_capability_result(required_capability: str, outcome: str, capability_status: str) -> None:
    policy = CAPABILITY_RESULT_POLICY.get(required_capability)
    if policy is None:
        raise RuntimeError(f"unknown required capability: {required_capability}")
    if (outcome, capability_status) not in policy:
        raise RuntimeError(
            f"outcome {outcome}/{capability_status} is forbidden for required capability {required_capability}"
        )


def default_falsifier(name: str) -> str:
    claim = name.removeprefix("test_").replace("_", " ")
    return f"the {claim} observation is absent, contradicted, stale, partial, or accepted without executing its production boundary"


def implementation(root: Path, entry: dict, tests: dict[str, str], laws: set[str]) -> tuple[str, str, str]:
    source = entry["source_file"]
    name = entry["name"]
    prefix = source[:2]
    if prefix == "00":
        location = tests.get(name)
        if not location:
            raise RuntimeError(f"existing test inventory entry has no executable method: {name}")
        return location, "existing-test", "PENDING_VERIFICATION"
    if prefix == "01":
        if name not in laws:
            raise RuntimeError(f"existing built-in law inventory entry not found: {name}")
        law_path = (resolve_law_layout(root).law_definitions / "framework.toml").relative_to(root).as_posix()
        return f"{law_path}#{name}", "existing-law", "PENDING_VERIFICATION"
    if prefix == "02":
        return (
            f"{(resolve_law_layout(root).law_tests / 'test_meta.py').relative_to(root).as_posix()}::test_law_template_examples_are_schema_valid_and_inert",
            "stronger-equivalent",
            "PENDING_VERIFICATION",
        )
    adapter = (resolve_law_layout(root).law_tests / "test_specification.py").relative_to(root).as_posix()
    return f"{adapter}::{name}", "exact-name-adapter", "MISSING"


def apply_ledger(requirements: list[dict], ledger_path: Path) -> None:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    expected_inventory = canonical_digest(
        [
            {"source_file": item["source_file"], "source_line": item["source_line"], "name": item["name"]}
            for item in requirements
        ]
    )
    if not isinstance(ledger, dict) or ledger.get("schema") != 2 or ledger.get("suite") != "aegis-comprehensive-laws":
        raise RuntimeError("ledger has unsupported identity/schema")
    if ledger.get("started_and_finished") is not True:
        raise RuntimeError("ledger did not start and finish as one complete run")
    if ledger.get("requirement_count") != len(requirements):
        raise RuntimeError("ledger requirement count does not match traceability inventory")
    if ledger.get("inventory_sha256") != expected_inventory:
        raise RuntimeError("ledger inventory digest does not match traceability inventory")
    if ledger.get("integrity_errors") != []:
        raise RuntimeError("ledger contains integrity errors")
    before = ledger.get("definition_digests_before")
    after = ledger.get("definition_digests_after")
    if not isinstance(before, dict) or not before or before != after:
        raise RuntimeError("ledger protected-definition digests are missing or changed")
    results = ledger.get("requirements", {})
    if not isinstance(results, dict):
        raise RuntimeError("ledger requirements must be an object")
    expected_names = {requirement["name"] for requirement in requirements}
    if set(results) != expected_names:
        missing = sorted(expected_names - set(results))
        extra = sorted(set(results) - expected_names)
        raise RuntimeError(f"ledger/result inventory mismatch; missing={missing[:5]} extra={extra[:5]}")
    for requirement in requirements:
        name = requirement["name"]
        record = results.get(name)
        if not record:
            raise RuntimeError(f"ledger is missing requirement result: {name}")
        if record.get("started") is not True or record.get("finished") is not True:
            raise RuntimeError(f"requirement did not execute to completion: {name}")
        if int(record.get("oracle_count", 0)) <= 0:
            raise RuntimeError(f"requirement has a vacuous zero-oracle result: {name}")
        outcome = record.get("outcome")
        cap = record.get("capability_status")
        if record.get("required_capability") != requirement["required_capability"]:
            raise RuntimeError(f"ledger capability assignment drifted: {name}")
        if record.get("required_observations") != requirement["required_observations"]:
            raise RuntimeError(f"ledger semantic observation binding drifted: {name}")
        if record.get("evidence_digest") != record_evidence_digest(record):
            raise RuntimeError(f"ledger evidence seal is invalid: {name}")
        validate_capability_result(requirement["required_capability"], outcome, cap)
        if outcome == "PASS" and cap == "AVAILABLE":
            if requirement["source_file"].startswith(("00_", "01_")):
                requirement["status"] = "EXISTING_AND_VERIFIED"
            elif requirement["source_file"].startswith("02_") or record.get("stronger_equivalent"):
                requirement["status"] = "SUPERSEDED_BY_STRONGER_EQUIVALENT_AND_PROVEN"
            else:
                requirement["status"] = "IMPLEMENTED_AND_PASSING"
        elif outcome == "UNAVAILABLE" and cap in {"MISSING", "INCOMPATIBLE", "UNTRUSTED", "UNOBSERVABLE", "STALE"}:
            requirement["status"] = "BLOCKED_BY_PROVEN_EXTERNAL_LIMITATION"
        elif outcome == "NOT_APPLICABLE" and cap == "NOT_APPLICABLE" and (record.get("justification") or record.get("detail")):
            requirement["status"] = "NOT_APPLICABLE_WITH_CONCRETE_JUSTIFICATION"
        else:
            raise RuntimeError(f"requirement has non-final result {outcome}/{cap}: {name}")
        requirement["last_result"] = {
            key: record.get(key)
            for key in (
                "required_capability", "required_observations", "outcome", "capability_status",
                "oracle_count", "detail", "evidence_digest", "justification",
                "definition_sha256_before", "definition_sha256_after",
            )
            if record.get(key) is not None
        }
    counts = dict(sorted(Counter(record.get("outcome") for record in results.values()).items()))
    capabilities = dict(sorted(Counter(record.get("capability_status") for record in results.values()).items()))
    if ledger.get("counts") != counts or ledger.get("capabilities") != capabilities:
        raise RuntimeError("ledger aggregate counts do not match requirement records")
    expected_outcome = (
        "PASS_WITH_EXPLICIT_CAPABILITY_LIMITATIONS"
        if counts.get("UNAVAILABLE") or counts.get("NOT_APPLICABLE")
        else "PASS"
    )
    if ledger.get("outcome") != expected_outcome:
        raise RuntimeError("ledger top-level outcome is inconsistent with final requirement records")


def build(root: Path, ledger: Path | None = None) -> dict:
    raw_entries, source_digests = inventory(root)
    inventory_names = {entry["name"] for entry in raw_entries}
    classified_names = set().union(*CAPABILITY_REQUIREMENTS.values())
    unknown_classifications = sorted(classified_names - inventory_names)
    if unknown_classifications:
        raise RuntimeError(f"capability catalog names are not in the specification: {unknown_classifications}")
    tests = existing_test_locations(root)
    laws = existing_law_ids(root)
    semantic_bindings, semantic_catalog_sha256 = load_semantic_catalog(
        root, {entry["name"]: entry["source_file"] for entry in raw_entries}
    )
    requirements = []
    for entry in raw_entries:
        prefix = entry["source_file"][:2]
        if prefix not in FAMILY:
            raise RuntimeError(f"no semantic family for {entry['source_file']}")
        scenario, subsystem, test_type = FAMILY[prefix]
        location, mapping, status = implementation(root, entry, tests, laws)
        if prefix in SOURCE_PROPERTY_SUBSUMPTION:
            mapping = "stronger-property-module-subsumption"
        requirements.append(
            {
                **entry,
                "source_sha256": source_digests[entry["source_file"]],
                "implemented_test_location": location,
                "mapping": mapping,
                "test_type": test_type,
                "production_subsystem": subsystem,
                "scenario": scenario,
                "oracle": "explicit-observations:" + canonical_digest(semantic_bindings[entry["name"]]),
                "required_observations": semantic_bindings[entry["name"]],
                "falsifier": default_falsifier(entry["name"]),
                "required_capability": capability(entry["source_file"], entry["name"]),
                "status": status,
                "subsumed_by": semantic_bindings[entry["name"]] if prefix in SOURCE_PROPERTY_SUBSUMPTION else [],
            }
        )
    if ledger is not None:
        apply_ledger(requirements, ledger)
        unfinished = [req["name"] for req in requirements if req["status"] not in FINAL_STATUSES]
        if unfinished:
            raise RuntimeError(f"non-final traceability statuses remain: {unfinished[:10]}")
    compact_inventory = [
        {"source_file": item["source_file"], "source_line": item["source_line"], "name": item["name"]}
        for item in requirements
    ]
    return {
        "schema": 2,
        "requirement_count": len(requirements),
        "inventory_sha256": canonical_digest(compact_inventory),
        "source_files": source_digests,
        "semantic_catalog_sha256": semantic_catalog_sha256,
        "families": dict(sorted(Counter(item["scenario"] for item in requirements).items())),
        "requirements": requirements,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = (args.root or project_root(Path(__file__))).resolve()
    output = args.output or resolve_law_layout(root).results / "traceability.json"
    registry = build(root, args.ledger)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "requirements": registry["requirement_count"], "inventory_sha256": registry["inventory_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
