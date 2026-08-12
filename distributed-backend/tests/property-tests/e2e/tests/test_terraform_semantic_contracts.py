from __future__ import annotations

import copy
from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.terraform_contracts import (
    TERRAFORM_CONTRACTS,
    _block,
    _locals,
    _outputs,
    _required_providers,
    _resource,
    binding_metadata,
    load_terraform_locks,
    load_terraform_scopes,
    validate_terraform_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def scopes():
    return load_terraform_scopes(REPO_ROOT)


@pytest.fixture(scope="module")
def locks():
    return load_terraform_locks(REPO_ROOT)


@pytest.mark.parametrize("name", sorted(TERRAFORM_CONTRACTS))
def test_each_terraform_binding_executes_its_exact_structured_oracle(name, scopes, locks):
    validate_terraform_contract(name, REPO_ROOT, scopes=scopes, locks=locks)


def test_terraform_requirement_bindings_are_exactly_the_executable_registry():
    requirements = requirements_by_name()
    bound = {
        name: requirement["semantic_binding"]
        for name, requirement in requirements.items()
        if requirement.get("semantic_binding", {}).get("family") == "structured_terraform"
    }
    expected = {
        name: binding_metadata(spec)
        for name, spec in TERRAFORM_CONTRACTS.items()
    }
    assert bound == expected
    assert all(requirements[name]["implementation_status"] == "IMPLEMENTED" for name in bound)


def test_provider_pin_oracle_rejects_unbounded_constraint(scopes):
    mutated = copy.deepcopy(scopes)
    _required_providers(mutated["eks"])["aws"]["version"] = ">= 5.0"
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_all_terraform_roots_pin_provider_versions_with_nonempty_constraints",
            REPO_ROOT,
            scopes=mutated,
        )


def test_secret_taint_oracle_rejects_nonsensitive_kubeconfig_output(scopes):
    mutated = copy.deepcopy(scopes)
    _outputs(mutated["gke"])["kubeconfig"]["sensitive"] = False
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_all_outputs_derived_from_secret_values_are_marked_sensitive",
            REPO_ROOT,
            scopes=mutated,
        )


def test_database_output_oracle_rejects_direct_secret_value_flow(scopes):
    mutated = copy.deepcopy(scopes)
    mutated["gke"].setdefault("output", []).append(
        {"leaked_database_url": {"value": "${local.database_url}", "sensitive": True}}
    )
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_no_terraform_output_exposes_database_password",
            REPO_ROOT,
            scopes=mutated,
        )


def test_provider_lock_oracle_rejects_selected_provider_without_checksums(scopes, locks):
    mutated = copy.deepcopy(locks)
    mutated["talos"]["registry.terraform.io/gavinbunney/kubectl"]["hashes"] = []
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_provider_lockfile_contains_checksum_for_every_selected_provider_version",
            REPO_ROOT,
            scopes=scopes,
            locks=mutated,
        )


def test_eks_endpoint_policy_oracle_rejects_unconditional_public_default(scopes):
    mutated = copy.deepcopy(scopes)
    variables = {name: body for item in mutated["eks"]["variable"] for name, body in item.items()}
    variables["cluster_endpoint_public_access"]["default"] = True
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_eks_cluster_endpoint_public_access_matches_declared_security_policy",
            REPO_ROOT,
            scopes=mutated,
        )


def test_eks_backup_oracle_rejects_minimum_below_seven_days(scopes):
    mutated = copy.deepcopy(scopes)
    variables = {name: body for item in mutated["eks"]["variable"] for name, body in item.items()}
    variables["database_backup_retention_period"]["validation"][0]["condition"] = (
        "${var.database_backup_retention_period >= 1}"
    )
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_eks_database_backup_retention_meets_configured_production_minimum",
            REPO_ROOT,
            scopes=mutated,
        )


def test_eks_encryption_oracle_rejects_unencrypted_managed_database(scopes):
    mutated = copy.deepcopy(scopes)
    _resource(mutated["eks"], "aws_db_instance", "trade_settlement")["storage_encrypted"] = False
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_eks_database_storage_encryption_is_enabled_when_database_is_managed_by_stack",
            REPO_ROOT,
            scopes=mutated,
        )


def test_kubernetes_provider_oracle_rejects_static_cluster_endpoint(scopes):
    mutated = copy.deepcopy(scopes)
    provider = next(
        body
        for item in mutated["eks"]["provider"]
        for name, body in item.items()
        if name == "kubectl"
    )
    provider["host"] = "https://static.example.invalid"
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_eks_kubernetes_provider_uses_created_cluster_endpoint_and_ca_without_static_credentials",
            REPO_ROOT,
            scopes=mutated,
        )


@pytest.mark.parametrize(
    ("name", "port"),
    [
        ("test_eks_security_groups_do_not_expose_postgres_port_to_world", 5432),
        ("test_eks_security_groups_do_not_expose_settlement_grpc_port_to_world", 9092),
    ],
)
def test_eks_ingress_oracle_rejects_world_rule_for_protected_port(name, port, scopes):
    mutated = copy.deepcopy(scopes)
    mutated["eks"].setdefault("resource", []).append(
        {
            "aws_security_group_rule": {
                "hostile_world_rule": {
                    "type": "ingress",
                    "protocol": "tcp",
                    "from_port": port,
                    "to_port": port,
                    "cidr_blocks": ["0.0.0.0/0"],
                }
            }
        }
    )
    with pytest.raises(AssertionError):
        validate_terraform_contract(name, REPO_ROOT, scopes=mutated)


def test_eks_iam_oracle_rejects_unreviewed_policy(scopes):
    mutated = copy.deepcopy(scopes)
    role = _block(mutated["eks_module"], "module", "iam_assumable_role_adot_amp")
    role["role_policy_arns"] = ["arn:aws:iam::aws:policy/AdministratorAccess"]
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_eks_iam_roles_grant_no_wildcard_actions_outside_explicit_allowlist",
            REPO_ROOT,
            scopes=mutated,
        )


def test_eks_oidc_oracle_rejects_wildcard_service_account_subject(scopes):
    mutated = copy.deepcopy(scopes)
    role = _block(mutated["eks_module"], "module", "iam_assumable_role_adot_amp")
    role["oidc_fully_qualified_subjects"] = ["system:serviceaccount:*:*"]
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_eks_workload_identity_role_is_scoped_to_expected_service_account",
            REPO_ROOT,
            scopes=mutated,
        )


def test_eks_zone_oracle_rejects_single_zone_slice(scopes):
    mutated = copy.deepcopy(scopes)
    local_block = next(
        item for item in mutated["vpc_module"]["locals"] if "azs" in item
    )
    local_block["azs"] = "${slice(data.aws_availability_zones.available.names, 0, 1)}"
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_eks_nodes_span_configured_failure_zones_when_multiple_zones_are_supplied",
            REPO_ROOT,
            scopes=mutated,
        )


def test_gke_control_plane_oracle_rejects_missing_public_endpoint_precondition(scopes):
    mutated = copy.deepcopy(scopes)
    cluster = _resource(mutated["gke_module"], "google_container_cluster", "this")
    cluster["lifecycle"] = []
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_gke_cluster_control_plane_access_matches_declared_security_policy",
            REPO_ROOT,
            scopes=mutated,
        )


@pytest.mark.parametrize(
    ("name", "port"),
    [
        ("test_gke_firewall_rules_do_not_expose_postgres_port_to_world", "5432"),
        ("test_gke_firewall_rules_do_not_expose_settlement_grpc_port_to_world", "9092"),
    ],
)
def test_gke_firewall_oracle_rejects_world_rule_for_protected_port(name, port, scopes):
    mutated = copy.deepcopy(scopes)
    mutated["gcp_network"].setdefault("resource", []).append(
        {
            "google_compute_firewall": {
                "hostile_world_rule": {
                    "direction": "INGRESS",
                    "source_ranges": ["0.0.0.0/0"],
                    "allow": [{"protocol": "tcp", "ports": [port]}],
                }
            }
        }
    )
    with pytest.raises(AssertionError):
        validate_terraform_contract(name, REPO_ROOT, scopes=mutated)


def test_gke_role_oracle_rejects_project_owner(scopes):
    mutated = copy.deepcopy(scopes)
    binding = _resource(
        mutated["gke_module"], "google_project_iam_member", "nodes_artifact_registry"
    )
    binding["role"] = "roles/owner"
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_gke_service_account_roles_grant_no_project_wide_owner_or_editor_role",
            REPO_ROOT,
            scopes=mutated,
        )


def test_talos_external_mode_oracle_rejects_unconditional_postgres(scopes):
    mutated = copy.deepcopy(scopes)
    _resource(mutated["talos"], "kubectl_manifest", "postgres_statefulset")["count"] = 1
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_talos_omni_external_database_mode_does_not_create_in_cluster_postgres_statefulset",
            REPO_ROOT,
            scopes=mutated,
        )


def test_talos_external_url_oracle_rejects_missing_precondition(scopes):
    mutated = copy.deepcopy(scopes)
    check = _block(mutated["talos"], "check", "external_database_url")
    check["assert"][0]["condition"] = "${true}"
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_talos_omni_external_database_mode_requires_nonempty_external_database_url",
            REPO_ROOT,
            scopes=mutated,
        )


def test_talos_pvc_oracle_rejects_ephemeral_postgres_storage(scopes):
    mutated = copy.deepcopy(scopes)
    stateful = _resource(mutated["talos"], "kubectl_manifest", "postgres_statefulset")
    stateful["yaml_body"] = str(stateful["yaml_body"]).replace("volumeClaimTemplates", "emptyDir")
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_talos_omni_in_cluster_database_mode_creates_persistent_volume_claim_for_postgres",
            REPO_ROOT,
            scopes=mutated,
        )


def test_talos_service_oracle_rejects_public_postgres(scopes):
    mutated = copy.deepcopy(scopes)
    service = _resource(mutated["talos"], "kubectl_manifest", "postgres_service")
    service["yaml_body"] = str(service["yaml_body"]).replace("ClusterIP", "LoadBalancer")
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_talos_omni_in_cluster_database_mode_does_not_expose_postgres_outside_cluster",
            REPO_ROOT,
            scopes=mutated,
        )


def test_talos_password_oracle_rejects_nonempty_only_check(scopes):
    mutated = copy.deepcopy(scopes)
    check = _block(mutated["talos"], "check", "in_cluster_database_password")
    check["assert"][0]["condition"] = (
        '${var.database_mode != "in_cluster" || var.in_cluster_database_password != ""}'
    )
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_talos_omni_in_cluster_database_mode_requires_nondefault_database_password",
            REPO_ROOT,
            scopes=mutated,
        )


def test_terraform_lock_ci_oracle_rejects_removed_git_diff_gate(scopes):
    path = REPO_ROOT / ".github" / "dagger" / "_terraform_root.py"
    source = path.read_text(encoding="utf-8")
    mutated = source.replace(
        'f"git diff --exit-code -- {root}/.terraform.lock.hcl",',
        'f"echo unchecked {root}/.terraform.lock.hcl",',
    )
    assert mutated != source
    with pytest.raises(AssertionError):
        validate_terraform_contract(
            "test_terraform_ci_fails_when_providers_lock_changes_committed_checksums",
            REPO_ROOT,
            scopes=scopes,
            terraform_ci_source=mutated,
        )
