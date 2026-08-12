from __future__ import annotations

"""Structured Terraform semantic contract families.

The loader excludes downloaded ``.terraform`` modules and parses the repository
owned HCL into blocks.  Each binding names its provider/root scope explicitly;
oracles inspect exact resource relationships, conditional creation, provider
credentials, IAM bindings, lockfile entries, or CI command ordering.
"""

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


TerraformScopes = Mapping[str, dict[str, Any]]
TerraformLocks = Mapping[str, dict[str, dict[str, Any]]]


@dataclass(frozen=True)
class TerraformContractSpec:
    oracle: str
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...] = ("structured_hcl",)
    parameters: tuple[tuple[str, str], ...] = ()


def _spec(
    oracle: str,
    *scopes: str,
    capabilities: tuple[str, ...] = ("structured_hcl",),
    **parameters: str,
) -> TerraformContractSpec:
    return TerraformContractSpec(
        oracle=oracle,
        scopes=tuple(scopes),
        capabilities=capabilities,
        parameters=tuple(sorted(parameters.items())),
    )


TERRAFORM_CONTRACTS: dict[str, TerraformContractSpec] = {
    "test_all_terraform_roots_pin_provider_versions_with_nonempty_constraints": _spec(
        "provider_version_pins", "eks", "gke", "talos"
    ),
    "test_all_outputs_derived_from_secret_values_are_marked_sensitive": _spec(
        "secret_output_sensitivity", "eks", "gke", "talos"
    ),
    "test_no_terraform_output_exposes_database_password": _spec(
        "no_database_password_output", "eks", "gke", "talos"
    ),
    "test_eks_cluster_endpoint_public_access_matches_declared_security_policy": _spec(
        "eks_endpoint_policy", "eks", "eks_module"
    ),
    "test_eks_database_backup_retention_meets_configured_production_minimum": _spec(
        "eks_backup_retention", "eks"
    ),
    "test_eks_database_storage_encryption_is_enabled_when_database_is_managed_by_stack": _spec(
        "eks_database_encryption", "eks"
    ),
    "test_eks_iam_roles_grant_no_wildcard_actions_outside_explicit_allowlist": _spec(
        "eks_iam_boundary", "eks_module"
    ),
    "test_eks_kubernetes_provider_uses_created_cluster_endpoint_and_ca_without_static_credentials": _spec(
        "eks_dynamic_kubernetes_provider", "eks"
    ),
    "test_eks_nodes_span_configured_failure_zones_when_multiple_zones_are_supplied": _spec(
        "eks_failure_zones", "eks_module", "vpc_module"
    ),
    "test_eks_secrets_are_not_rendered_into_nonsensitive_terraform_outputs": _spec(
        "secret_output_sensitivity", "eks"
    ),
    "test_eks_security_groups_do_not_expose_postgres_port_to_world": _spec(
        "eks_no_world_ingress", "eks", "eks_module", port="5432"
    ),
    "test_eks_security_groups_do_not_expose_settlement_grpc_port_to_world": _spec(
        "eks_no_world_ingress", "eks", "eks_module", port="9092"
    ),
    "test_eks_workload_identity_role_is_scoped_to_expected_service_account": _spec(
        "eks_oidc_subject_scope", "eks_module"
    ),
    "test_gke_cluster_control_plane_access_matches_declared_security_policy": _spec(
        "gke_control_plane_policy", "gke", "gke_module"
    ),
    "test_gke_firewall_rules_do_not_expose_postgres_port_to_world": _spec(
        "gke_no_world_ingress", "gke", "gke_module", "gcp_network", port="5432"
    ),
    "test_gke_firewall_rules_do_not_expose_settlement_grpc_port_to_world": _spec(
        "gke_no_world_ingress", "gke", "gke_module", "gcp_network", port="9092"
    ),
    "test_gke_kubernetes_provider_uses_created_cluster_endpoint_and_ca_without_static_credentials": _spec(
        "gke_dynamic_kubernetes_provider", "gke"
    ),
    "test_gke_nodes_span_configured_failure_zones_when_multiple_zones_are_supplied": _spec(
        "gke_failure_zones", "gke", "gke_module"
    ),
    "test_gke_secrets_are_not_rendered_into_nonsensitive_terraform_outputs": _spec(
        "secret_output_sensitivity", "gke"
    ),
    "test_gke_service_account_roles_grant_no_project_wide_owner_or_editor_role": _spec(
        "gke_service_account_roles", "gke_module"
    ),
    "test_talos_omni_database_password_is_marked_sensitive_in_variables_and_outputs": _spec(
        "talos_database_sensitivity", "talos"
    ),
    "test_talos_omni_destroy_preserves_external_database_when_external_database_mode_is_selected": _spec(
        "talos_external_database_unmanaged", "talos"
    ),
    "test_talos_omni_external_database_mode_does_not_create_in_cluster_postgres_statefulset": _spec(
        "talos_external_mode_no_postgres", "talos"
    ),
    "test_talos_omni_external_database_mode_requires_nonempty_external_database_url": _spec(
        "talos_external_url_check", "talos"
    ),
    "test_talos_omni_in_cluster_database_mode_creates_persistent_volume_claim_for_postgres": _spec(
        "talos_postgres_pvc", "talos"
    ),
    "test_talos_omni_in_cluster_database_mode_does_not_expose_postgres_outside_cluster": _spec(
        "talos_postgres_cluster_ip", "talos"
    ),
    "test_talos_omni_in_cluster_database_mode_requires_nondefault_database_password": _spec(
        "talos_nondefault_password", "talos"
    ),
    "test_provider_lockfile_contains_checksum_for_every_selected_provider_version": _spec(
        "selected_provider_checksums", "eks", "gke", "talos", capabilities=("structured_hcl", "provider_lockfile")
    ),
    "test_terraform_ci_fails_when_provider_lockfile_changes_after_read_only_init": _spec(
        "terraform_lock_ci_gate", "ci", capabilities=("python_ast", "provider_lockfile"), failure_point="readonly_init"
    ),
    "test_terraform_ci_fails_when_providers_lock_changes_committed_checksums": _spec(
        "terraform_lock_ci_gate", "ci", capabilities=("python_ast", "provider_lockfile"), failure_point="providers_lock_diff"
    ),
}


SCOPE_DIRECTORIES = {
    "eks": "distributed-backend/terraform/eks",
    "gke": "distributed-backend/terraform/gke",
    "talos": "distributed-backend/terraform/talos-omni",
    "eks_module": "distributed-backend/terraform/lib/eks",
    "vpc_module": "distributed-backend/terraform/lib/vpc",
    "gke_module": "distributed-backend/terraform/lib/gke",
    "gcp_network": "distributed-backend/terraform/lib/gcp-network",
}
ROOT_SCOPES = ("eks", "gke", "talos")
LOCK_SCOPE_TO_DIRECTORY = {
    "eks": "distributed-backend/terraform/eks",
    "gke": "distributed-backend/terraform/gke",
    "talos": "distributed-backend/terraform/talos-omni",
}


def implemented_terraform_contracts() -> frozenset[str]:
    return frozenset(TERRAFORM_CONTRACTS)


def binding_metadata(spec: TerraformContractSpec) -> dict[str, Any]:
    result: dict[str, Any] = {
        "family": "structured_terraform",
        "oracle": spec.oracle,
        "scopes": list(spec.scopes),
        "capabilities": list(spec.capabilities),
    }
    if spec.parameters:
        result["parameters"] = dict(spec.parameters)
    return result


def _merge_documents(documents: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for document in documents:
        for key, value in document.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif key not in merged:
                merged[key] = value
            else:
                raise AssertionError(f"duplicate non-block Terraform key {key!r}")
    return merged


def load_terraform_scopes(root: Path) -> dict[str, dict[str, Any]]:
    import hcl2

    scopes: dict[str, dict[str, Any]] = {}
    for scope, relative in SCOPE_DIRECTORIES.items():
        directory = root / relative
        paths = sorted(directory.glob("*.tf"))
        assert paths, f"Terraform scope {scope!r} has no HCL files"
        documents = []
        for path in paths:
            with path.open(encoding="utf-8") as handle:
                documents.append(hcl2.load(handle))
        scopes[scope] = _merge_documents(documents)
    return scopes


def load_terraform_locks(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    import hcl2

    locks: dict[str, dict[str, dict[str, Any]]] = {}
    for scope, relative in LOCK_SCOPE_TO_DIRECTORY.items():
        path = root / relative / ".terraform.lock.hcl"
        assert path.is_file(), f"missing provider lockfile: {path}"
        with path.open(encoding="utf-8") as handle:
            document = hcl2.load(handle)
        records: dict[str, dict[str, Any]] = {}
        for item in document.get("provider") or []:
            assert isinstance(item, dict) and len(item) == 1
            address, body = next(iter(item.items()))
            assert address not in records, f"duplicate locked provider {address}"
            records[str(address)] = body
        assert records, f"provider lockfile {path} has no provider records"
        locks[scope] = records
    return locks


def _blocks(scope: Mapping[str, Any], kind: str):
    for item in scope.get(kind) or []:
        assert isinstance(item, dict)
        for label, body in item.items():
            yield str(label), body


def _block(scope: Mapping[str, Any], kind: str, label: str) -> dict[str, Any]:
    matches = [body for candidate, body in _blocks(scope, kind) if candidate == label]
    assert len(matches) == 1, (kind, label, len(matches))
    assert isinstance(matches[0], dict)
    return matches[0]


def _resource(scope: Mapping[str, Any], resource_type: str, name: str) -> dict[str, Any]:
    resources = _resource_types(scope)
    assert resource_type in resources, resource_type
    type_block = resources[resource_type]
    assert name in type_block, (resource_type, name)
    body = type_block[name]
    assert isinstance(body, dict)
    return body


def _resource_types(scope: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for resource_type, names in _blocks(scope, "resource"):
        assert isinstance(names, dict)
        result.setdefault(resource_type, {}).update(names)
    return result


def _variables(scope: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: body for name, body in _blocks(scope, "variable")}


def _outputs(scope: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: body for name, body in _blocks(scope, "output")}


def _locals(scope: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in scope.get("locals") or []:
        assert isinstance(item, dict)
        result.update(item)
    return result


def _serialized(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _taint_text(value: Any, references: set[str]) -> str:
    """Serialize an expression after removing explicit Terraform declassification.

    ``nonsensitive(secret)`` may safely drive whether a *secret name* output is
    null without exposing the secret value.  Treating that predicate as value
    flow false-reds those names and contradicts Terraform's sensitivity model.
    """

    text = _serialized(value)
    for reference in sorted(references, key=len, reverse=True):
        text = text.replace(f"nonsensitive({reference})", "<declassified>")
    return text


def _required_providers(scope: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    terraform_blocks = scope.get("terraform") or []
    assert terraform_blocks
    for terraform in terraform_blocks:
        for required in terraform.get("required_providers") or []:
            result.update(required)
    assert result
    return result


def _sensitive_roots(scope: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    variables = {
        f"var.{name}"
        for name, body in _variables(scope).items()
        if body.get("sensitive") is True
    }
    assert variables, "Terraform root has no explicitly sensitive variables"
    roots = set(variables)
    roots.update(
        {
            "random_password.database[0].result",
            "data.aws_eks_cluster_auth.this.token",
            "data.aws_eks_cluster_auth.cluster.token",
            "data.google_client_config.this.access_token",
        }
    )
    tainted_locals: set[str] = set()
    local_values = _locals(scope)
    changed = True
    while changed:
        changed = False
        references = roots | tainted_locals
        for name, value in local_values.items():
            address = f"local.{name}"
            text = _taint_text(value, references)
            if address not in tainted_locals and any(reference in text for reference in references):
                tainted_locals.add(address)
                changed = True
    return roots, tainted_locals


def _validate_secret_output_sensitivity(scopes: TerraformScopes, selected: tuple[str, ...]) -> None:
    tainted_count = 0
    for scope_name in selected:
        scope = scopes[scope_name]
        roots, locals_ = _sensitive_roots(scope)
        references = roots | locals_
        for output_name, body in _outputs(scope).items():
            if any(reference in _taint_text(body.get("value"), references) for reference in references):
                tainted_count += 1
                assert body.get("sensitive") is True, (scope_name, output_name)
    # GKE intentionally exposes a generated kubeconfig and private DB address as
    # sensitive outputs, making the taint analysis non-vacuous.
    if "gke" in selected:
        assert tainted_count >= 1


def _validate_no_database_password_output(scopes: TerraformScopes, selected: tuple[str, ...]) -> None:
    checked_sources = 0
    for scope_name in selected:
        scope = scopes[scope_name]
        variables = _variables(scope)
        database_sources = {
            f"var.{name}"
            for name, body in variables.items()
            if body.get("sensitive") is True
            and name in {"external_database_url", "market_database_url", "in_cluster_database_password"}
        }
        database_sources.update({"random_password.database[0].result", "local.database_url"})
        checked_sources += len(database_sources)
        offenders = [
            output_name
            for output_name, body in _outputs(scope).items()
            if any(source in _taint_text(body.get("value"), database_sources) for source in database_sources)
        ]
        assert not offenders, (scope_name, offenders)
    assert checked_sources >= 6


def _validate_provider_version_pins(scopes: TerraformScopes, selected: tuple[str, ...]) -> None:
    count = 0
    for scope_name in selected:
        for provider, body in _required_providers(scopes[scope_name]).items():
            count += 1
            version = str(body.get("version") or "")
            assert re.fullmatch(r"=\s*\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version), (
                scope_name,
                provider,
                version,
            )
            assert str(body.get("source") or ""), (scope_name, provider)
    assert count >= 10


def _validate_selected_provider_checksums(
    scopes: TerraformScopes,
    locks: TerraformLocks,
) -> None:
    hash_pattern = re.compile(r"^(?:h1:[A-Za-z0-9+/]{43}=|zh:[0-9a-f]{64})$")
    selected_count = 0
    for scope_name in ROOT_SCOPES:
        records = locks[scope_name]
        for address, record in records.items():
            hashes = record.get("hashes")
            assert isinstance(hashes, list) and hashes, (scope_name, address)
            assert len(hashes) == len(set(hashes)), (scope_name, address, "duplicate hash")
            assert all(hash_pattern.fullmatch(str(value)) for value in hashes), (scope_name, address)
            assert str(record.get("version") or ""), (scope_name, address, "empty version")
        for provider, required in _required_providers(scopes[scope_name]).items():
            selected_count += 1
            address = "registry.terraform.io/" + str(required["source"])
            assert address in records, (scope_name, provider, address)
            pinned = str(required["version"]).removeprefix("=").strip()
            assert records[address].get("version") == pinned, (
                scope_name,
                address,
                records[address].get("version"),
                pinned,
            )
    assert selected_count >= 10


def _validate_eks_endpoint_policy(scopes: TerraformScopes) -> None:
    variable = _variables(scopes["eks"])["cluster_endpoint_public_access"]
    assert variable.get("default") is False
    root_module = _block(scopes["eks"], "module", "_app_eks")
    library_module = _block(scopes["eks_module"], "module", "eks_cluster")
    assert root_module.get("cluster_endpoint_public_access") == "${var.cluster_endpoint_public_access}"
    assert library_module.get("cluster_endpoint_public_access") == "${var.cluster_endpoint_public_access}"


def _validate_eks_backup_retention(scopes: TerraformScopes) -> None:
    variable = _variables(scopes["eks"])["database_backup_retention_period"]
    assert int(variable.get("default")) >= 7
    validations = variable.get("validation") or []
    assert len(validations) == 1
    condition = str(validations[0].get("condition") or "")
    assert "var.database_backup_retention_period >= 7" in condition
    assert "var.database_backup_retention_period <= 35" in condition
    database = _resource(scopes["eks"], "aws_db_instance", "trade_settlement")
    assert database.get("backup_retention_period") == "${var.database_backup_retention_period}"


def _validate_eks_database_encryption(scopes: TerraformScopes) -> None:
    database = _resource(scopes["eks"], "aws_db_instance", "trade_settlement")
    assert database.get("count") == "${var.database_enabled ? 1 : 0}"
    assert database.get("storage_encrypted") is True
    assert database.get("publicly_accessible") is False
    assert database.get("storage_type") == "gp3"


def _provider_connection_bodies(scope: Mapping[str, Any], provider_names: set[str]) -> list[dict[str, Any]]:
    connections: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "host" in value or "cluster_ca_certificate" in value:
                connections.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for provider, body in _blocks(scope, "provider"):
        if provider in provider_names:
            visit(body)
    return connections


def _validate_dynamic_provider(
    scope: Mapping[str, Any],
    *,
    endpoint: str,
    ca: str,
    tokens: set[str],
) -> None:
    connections = _provider_connection_bodies(scope, {"kubernetes", "kubectl", "helm"})
    assert len(connections) >= 3
    forbidden = {"username", "password", "client_certificate", "client_key", "config_path"}
    for connection in connections:
        assert connection.get("host") == endpoint
        assert connection.get("cluster_ca_certificate") == ca
        assert connection.get("token") in tokens
        assert forbidden.isdisjoint(connection), connection


def _validate_eks_dynamic_provider(scopes: TerraformScopes) -> None:
    _validate_dynamic_provider(
        scopes["eks"],
        endpoint="${module._app_eks.cluster_endpoint}",
        ca="${base64decode(module._app_eks.cluster_certificate_authority_data)}",
        tokens={
            "${data.aws_eks_cluster_auth.this.token}",
            "${data.aws_eks_cluster_auth.cluster.token}",
        },
    )
    cluster = [
        body
        for provider, body in _blocks(scopes["eks"], "provider")
        if provider == "kubernetes" and body.get("alias") == "cluster"
    ]
    assert len(cluster) == 1
    assert cluster[0].get("token") == "${data.aws_eks_cluster_auth.cluster.token}"


def _validate_gke_dynamic_provider(scopes: TerraformScopes) -> None:
    _validate_dynamic_provider(
        scopes["gke"],
        endpoint="https://${module._app_gke.cluster_endpoint}",
        ca="${base64decode(module._app_gke.cluster_certificate_authority_data)}",
        tokens={"${data.google_client_config.this.access_token}"},
    )


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _covers_port(rule: Mapping[str, Any], port: int) -> bool:
    protocol = str(rule.get("protocol") or "").lower()
    if protocol == "-1":
        return True
    start = rule.get("from_port")
    end = rule.get("to_port")
    if isinstance(start, int) and isinstance(end, int):
        return start <= port <= end
    text = _serialized(rule)
    return str(port) in text or "local.database_port" in text and port == 5432


def _validate_eks_no_world_ingress(scopes: TerraformScopes, port: int) -> None:
    rules = [
        value
        for scope_name in ("eks", "eks_module")
        for value in _walk_dicts(scopes[scope_name])
        if value.get("type") == "ingress" and ("from_port" in value or "protocol" in value)
    ]
    assert len(rules) >= 3
    relevant = [rule for rule in rules if _covers_port(rule, port)]
    for rule in relevant:
        text = _serialized(rule)
        assert "0.0.0.0/0" not in text and "::/0" not in text, (port, rule)
    if port == 5432:
        assert any("source_security_group_id" in rule for rule in relevant)


def _validate_eks_iam_boundary(scopes: TerraformScopes) -> None:
    modules = {
        name: body
        for name, body in _blocks(scopes["eks_module"], "module")
        if name.startswith("iam_assumable_role_")
    }
    assert set(modules) == {"iam_assumable_role_adot_amp", "iam_assumable_role_adot_logs"}
    allowed_policy_suffixes = {
        ":iam::aws:policy/AWSXRayDaemonWriteAccess",
        ":iam::aws:policy/CloudWatchAgentServerPolicy",
    }
    for name, body in modules.items():
        policies = body.get("role_policy_arns")
        assert isinstance(policies, list) and len(policies) == 1, name
        assert any(str(policies[0]).endswith(suffix) for suffix in allowed_policy_suffixes), policies[0]
    for value in _walk_dicts(scopes["eks_module"]):
        actions = value.get("actions")
        if isinstance(actions, list):
            assert "*" not in actions, actions


def _validate_eks_oidc_scope(scopes: TerraformScopes) -> None:
    modules = [
        body
        for name, body in _blocks(scopes["eks_module"], "module")
        if name.startswith("iam_assumable_role_")
    ]
    assert len(modules) == 2
    subjects = []
    for body in modules:
        assert body.get("provider_url") == "${module.eks_cluster.cluster_oidc_issuer_url}"
        bound = body.get("oidc_fully_qualified_subjects")
        assert isinstance(bound, list) and len(bound) == 1
        subject = str(bound[0])
        assert subject.startswith("system:serviceaccount:${kubernetes_namespace_v1.adot.metadata[0].name}:")
        assert "*" not in subject
        subjects.append(subject)
    assert len(subjects) == len(set(subjects))


def _validate_eks_failure_zones(scopes: TerraformScopes) -> None:
    azs = _locals(scopes["vpc_module"]).get("azs")
    assert azs == "${slice(data.aws_availability_zones.available.names, 0, 3)}"
    module = _block(scopes["eks_module"], "module", "eks_cluster")
    groups = module.get("eks_managed_node_groups")
    assert isinstance(groups, dict) and len(groups) == 3
    subnets = []
    for group in groups.values():
        values = group.get("subnet_ids")
        assert isinstance(values, list) and len(values) == 1
        subnets.append(str(values[0]))
    assert set(subnets) == {
        "${var.subnet_ids[0]}",
        "${var.subnet_ids[1]}",
        "${var.subnet_ids[2]}",
    }


def _validate_gke_control_plane(scopes: TerraformScopes) -> None:
    variables = _variables(scopes["gke"])
    assert variables["enable_private_endpoint"].get("default") is True
    module = _block(scopes["gke"], "module", "_app_gke")
    assert module.get("enable_private_endpoint") == "${var.enable_private_endpoint}"
    assert module.get("master_authorized_networks") == "${var.master_authorized_networks}"
    cluster = _resource(scopes["gke_module"], "google_container_cluster", "this")
    private = cluster.get("private_cluster_config")
    assert isinstance(private, list) and len(private) == 1
    assert private[0].get("enable_private_endpoint") == "${var.enable_private_endpoint}"
    authorized = cluster.get("master_authorized_networks_config")
    assert isinstance(authorized, list) and len(authorized) == 1
    assert "var.master_authorized_networks" in _serialized(authorized)
    lifecycle = cluster.get("lifecycle")
    assert "var.enable_private_endpoint || length(var.master_authorized_networks) > 0" in _serialized(lifecycle)


def _validate_gke_failure_zones(scopes: TerraformScopes) -> None:
    locations = _variables(scopes["gke"])["node_locations"].get("default")
    assert isinstance(locations, list) and len(locations) >= 2
    assert len(locations) == len(set(locations))
    module = _block(scopes["gke"], "module", "_app_gke")
    assert module.get("node_locations") == "${var.node_locations}"
    cluster = _resource(scopes["gke_module"], "google_container_cluster", "this")
    assert cluster.get("node_locations") == "${var.node_locations}"


def _gcp_allow_covers_port(allow: Mapping[str, Any], port: int) -> bool:
    protocol = str(allow.get("protocol") or "").lower()
    if protocol in {"all", "-1"}:
        return True
    ports = allow.get("ports")
    if not ports:
        return protocol in {"tcp", "udp"}
    for value in ports:
        text = str(value)
        if "-" in text:
            start, end = text.split("-", 1)
            if start.isdigit() and end.isdigit() and int(start) <= port <= int(end):
                return True
        elif text.isdigit() and int(text) == port:
            return True
    return False


def _validate_gke_no_world_ingress(scopes: TerraformScopes, port: int) -> None:
    network_resources = _resource_types(scopes["gcp_network"])
    assert "google_compute_network" in network_resources
    network = network_resources["google_compute_network"]["this"]
    assert network.get("auto_create_subnetworks") is False
    for name, firewall in (network_resources.get("google_compute_firewall") or {}).items():
        direction = str(firewall.get("direction") or "INGRESS").upper()
        if direction != "INGRESS":
            continue
        source_ranges = {str(value) for value in firewall.get("source_ranges") or []}
        if source_ranges.isdisjoint({"0.0.0.0/0", "::/0"}):
            continue
        allows = firewall.get("allow") or []
        assert not any(_gcp_allow_covers_port(allow, port) for allow in allows), (
            name,
            port,
            firewall,
        )
    database = _resource(scopes["gke"], "google_sql_database_instance", "trade_settlement")
    assert '"ipv4_enabled":false' in _serialized(database)
    cluster = _resource(scopes["gke_module"], "google_container_cluster", "this")
    assert '"enable_private_nodes":true' in _serialized(cluster)


def _validate_gke_roles(scopes: TerraformScopes) -> None:
    resources = _resource_types(scopes["gke_module"])
    bindings = resources.get("google_project_iam_member")
    assert isinstance(bindings, dict) and len(bindings) == 4
    allowed = {
        "roles/artifactregistry.reader",
        "roles/logging.logWriter",
        "roles/monitoring.metricWriter",
        "roles/monitoring.viewer",
    }
    roles = {str(body.get("role")) for body in bindings.values()}
    assert roles == allowed
    for body in bindings.values():
        assert body.get("member") == "serviceAccount:${google_service_account.nodes.email}"
    assert {"roles/owner", "roles/editor"}.isdisjoint(roles)


def _talos_manifest(scopes: TerraformScopes, name: str) -> dict[str, Any]:
    return _resource(scopes["talos"], "kubectl_manifest", name)


def _validate_talos_database_sensitivity(scopes: TerraformScopes) -> None:
    variables = _variables(scopes["talos"])
    assert variables["in_cluster_database_password"].get("sensitive") is True
    assert variables["external_database_url"].get("sensitive") is True
    _validate_secret_output_sensitivity(scopes, ("talos",))


def _validate_talos_external_unmanaged(scopes: TerraformScopes) -> None:
    resources = _resource_types(scopes["talos"])
    forbidden = {
        resource_type
        for resource_type in resources
        if resource_type.startswith(("aws_db_", "google_sql_", "postgresql_"))
    }
    assert not forbidden, forbidden
    stateful = _talos_manifest(scopes, "postgres_statefulset")
    service = _talos_manifest(scopes, "postgres_service")
    assert stateful.get("count") == '${var.database_mode == "in_cluster" ? 1 : 0}'
    assert service.get("count") == '${var.database_mode == "in_cluster" ? 1 : 0}'
    assert "external" in str(_variables(scopes["talos"])["database_mode"].get("validation"))


def _validate_talos_external_no_postgres(scopes: TerraformScopes) -> None:
    _validate_talos_external_unmanaged(scopes)


def _validate_talos_external_url(scopes: TerraformScopes) -> None:
    check = _block(scopes["talos"], "check", "external_database_url")
    assertions = check.get("assert")
    assert isinstance(assertions, list) and len(assertions) == 1
    condition = str(assertions[0].get("condition") or "")
    assert 'var.database_mode != "external"' in condition
    assert 'nonsensitive(var.external_database_url) != ""' in condition


def _validate_talos_pvc(scopes: TerraformScopes) -> None:
    stateful = _talos_manifest(scopes, "postgres_statefulset")
    assert stateful.get("count") == '${var.database_mode == "in_cluster" ? 1 : 0}'
    rendered = str(stateful.get("yaml_body") or "")
    assert "volumeClaimTemplates" in rendered
    assert "postgres_storage_size" in rendered
    assert "ReadWriteOnce" in rendered


def _validate_talos_cluster_ip(scopes: TerraformScopes) -> None:
    service = _talos_manifest(scopes, "postgres_service")
    assert service.get("count") == '${var.database_mode == "in_cluster" ? 1 : 0}'
    rendered = str(service.get("yaml_body") or "")
    assert '"type": "ClusterIP"' in rendered
    assert '"port": 5432' in rendered
    assert "LoadBalancer" not in rendered and "NodePort" not in rendered


def _validate_talos_nondefault_password(scopes: TerraformScopes) -> None:
    check = _block(scopes["talos"], "check", "in_cluster_database_password")
    assertions = check.get("assert")
    assert isinstance(assertions, list) and len(assertions) == 1
    condition = str(assertions[0].get("condition") or "")
    assert 'var.database_mode != "in_cluster"' in condition
    assert "lower(trimspace(var.in_cluster_database_password))" in condition
    for forbidden in ("postgres", "password", "changeme", "eve_trade"):
        assert forbidden in condition


def _terraform_ci_commands(source: str) -> list[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "sh":
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.List):
            continue
        commands = [ast.get_source_segment(source, item) or ast.unparse(item) for item in node.args[1].elts]
        if any("providers lock" in command for command in commands):
            return commands
    raise AssertionError("Terraform Dagger verifier has no structured sh command list")


def _validate_terraform_lock_ci(source: str) -> None:
    commands = _terraform_ci_commands(source)
    normalized = [re.sub(r"\s+", " ", command) for command in commands]
    def command_index(fragment: str) -> int:
        matches = [i for i, command in enumerate(normalized) if fragment in command]
        assert len(matches) == 1, (fragment, matches)
        return matches[0]

    init = command_index(" init ")
    lock = command_index(" providers lock ")
    diff = command_index("git diff --exit-code")
    assert init < lock < diff
    assert "-lockfile=readonly" in normalized[init]
    assert "-platform=linux_amd64" in normalized[lock]
    assert "-platform=windows_amd64" in normalized[lock]
    assert ".terraform.lock.hcl" in normalized[diff]


def validate_terraform_contract(
    name: str,
    root: Path,
    *,
    scopes: TerraformScopes | None = None,
    locks: TerraformLocks | None = None,
    terraform_ci_source: str | None = None,
) -> None:
    spec = TERRAFORM_CONTRACTS[name]
    parsed = scopes if scopes is not None else load_terraform_scopes(root)
    if spec.oracle == "provider_version_pins":
        _validate_provider_version_pins(parsed, spec.scopes)
    elif spec.oracle == "secret_output_sensitivity":
        _validate_secret_output_sensitivity(parsed, spec.scopes)
    elif spec.oracle == "no_database_password_output":
        _validate_no_database_password_output(parsed, spec.scopes)
    elif spec.oracle == "selected_provider_checksums":
        parsed_locks = locks if locks is not None else load_terraform_locks(root)
        _validate_selected_provider_checksums(parsed, parsed_locks)
    elif spec.oracle == "eks_endpoint_policy":
        _validate_eks_endpoint_policy(parsed)
    elif spec.oracle == "eks_backup_retention":
        _validate_eks_backup_retention(parsed)
    elif spec.oracle == "eks_database_encryption":
        _validate_eks_database_encryption(parsed)
    elif spec.oracle == "eks_dynamic_kubernetes_provider":
        _validate_eks_dynamic_provider(parsed)
    elif spec.oracle == "eks_no_world_ingress":
        _validate_eks_no_world_ingress(parsed, int(dict(spec.parameters)["port"]))
    elif spec.oracle == "eks_iam_boundary":
        _validate_eks_iam_boundary(parsed)
    elif spec.oracle == "eks_oidc_subject_scope":
        _validate_eks_oidc_scope(parsed)
    elif spec.oracle == "eks_failure_zones":
        _validate_eks_failure_zones(parsed)
    elif spec.oracle == "gke_control_plane_policy":
        _validate_gke_control_plane(parsed)
    elif spec.oracle == "gke_dynamic_kubernetes_provider":
        _validate_gke_dynamic_provider(parsed)
    elif spec.oracle == "gke_failure_zones":
        _validate_gke_failure_zones(parsed)
    elif spec.oracle == "gke_no_world_ingress":
        _validate_gke_no_world_ingress(parsed, int(dict(spec.parameters)["port"]))
    elif spec.oracle == "gke_service_account_roles":
        _validate_gke_roles(parsed)
    elif spec.oracle == "talos_database_sensitivity":
        _validate_talos_database_sensitivity(parsed)
    elif spec.oracle == "talos_external_database_unmanaged":
        _validate_talos_external_unmanaged(parsed)
    elif spec.oracle == "talos_external_mode_no_postgres":
        _validate_talos_external_no_postgres(parsed)
    elif spec.oracle == "talos_external_url_check":
        _validate_talos_external_url(parsed)
    elif spec.oracle == "talos_postgres_pvc":
        _validate_talos_pvc(parsed)
    elif spec.oracle == "talos_postgres_cluster_ip":
        _validate_talos_cluster_ip(parsed)
    elif spec.oracle == "talos_nondefault_password":
        _validate_talos_nondefault_password(parsed)
    elif spec.oracle == "terraform_lock_ci_gate":
        source = terraform_ci_source
        if source is None:
            source = (root / ".github" / "dagger" / "_terraform_root.py").read_text(encoding="utf-8")
        _validate_terraform_lock_ci(source)
    else:
        raise AssertionError(f"unknown Terraform oracle {spec.oracle!r} for {name}")
