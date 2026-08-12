from __future__ import annotations

"""Typed semantic oracles for rendered Kubernetes contract families.

The bindings in this module are intentionally exact.  They are consumed both by
the repository runner and by the requirement generator, so a contract becomes
``IMPLEMENTED`` only when it has a concrete scope and oracle here.  Validators
operate on parsed, rendered objects; they never infer correctness from the test
name or from source-text token presence.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


RenderedDocument = tuple[Path, dict[str, Any]]
RenderedScopes = Mapping[str, Sequence[RenderedDocument]]


@dataclass(frozen=True)
class KubernetesContractSpec:
    oracle: str
    scopes: tuple[str, ...] = ("production",)


KUBERNETES_CONTRACTS: dict[str, KubernetesContractSpec] = {
    "test_application_service_account_token_automount_is_disabled_for_workloads_that_do_not_call_kubernetes_api": KubernetesContractSpec(
        "service_account_token_automount"
    ),
    "test_kubernetes_secret_values_are_referenced_from_secret_objects_instead_of_committed_plaintext_literals": KubernetesContractSpec(
        "secret_references"
    ),
    "test_network_policy_allows_only_required_gateway_market_worker_settlement_dependency_edges": KubernetesContractSpec(
        "exact_application_network_graph"
    ),
    "test_network_policy_allows_worker_to_settlement_and_denies_unrelated_ingress": KubernetesContractSpec(
        "settlement_ingress"
    ),
    "test_network_policy_denies_unlisted_ingress_to_postgres": KubernetesContractSpec(
        "postgres_ingress_boundary", ("production", "local")
    ),
    "test_network_policy_denies_unlisted_ingress_to_trade_settlement": KubernetesContractSpec(
        "settlement_ingress"
    ),
    "test_no_application_pod_uses_host_network_namespace_unless_quilkin_overlay_explicitly_requires_it": KubernetesContractSpec(
        "host_network_boundary"
    ),
    "test_postgres_service_is_not_exposed_through_public_load_balancer": KubernetesContractSpec(
        "postgres_service_exposure", ("production", "local")
    ),
    "test_rendered_pod_security_context_is_preserved_in_gateway_api_production_overlay": KubernetesContractSpec(
        "platform_overlay_security", ("production", "gateway")
    ),
    "test_rendered_pod_security_context_is_preserved_in_istio_production_overlay": KubernetesContractSpec(
        "platform_overlay_security", ("production", "istio")
    ),
    "test_trade_settlement_service_is_cluster_internal": KubernetesContractSpec(
        "settlement_service_exposure"
    ),
    "test_quilkin_service_routes_udp_traffic_only_to_configured_gateway_backend_port": KubernetesContractSpec(
        "quilkin_udp_route"
    ),
    "test_rendered_kubernetes_configures_readiness_probe_for_encore_backend": KubernetesContractSpec(
        "encore_readiness"
    ),
    "test_rendered_kubernetes_configures_readiness_probe_for_trade_settlement": KubernetesContractSpec(
        "settlement_readiness"
    ),
    "test_rendered_kubernetes_configures_resource_requests_and_limits": KubernetesContractSpec(
        "resource_requirements"
    ),
    "test_rendered_kubernetes_contains_required_gateway_udp_port": KubernetesContractSpec(
        "gateway_udp_ports"
    ),
    "test_rendered_kubernetes_contains_required_settlement_grpc_port": KubernetesContractSpec(
        "settlement_grpc_ports"
    ),
    "test_rendered_kubernetes_does_not_embed_plaintext_secrets": KubernetesContractSpec(
        "secret_references"
    ),
    "test_settlement_service_is_not_publicly_exposed_unless_explicitly_intended": KubernetesContractSpec(
        "settlement_service_exposure"
    ),
    "test_istio_and_gateway_api_production_overlays_preserve_same_readiness_and_liveness_probes": KubernetesContractSpec(
        "platform_application_probe_parity", ("production", "gateway", "istio")
    ),
    "test_istio_and_gateway_api_production_overlays_preserve_same_resource_requests": KubernetesContractSpec(
        "platform_application_resource_parity", ("production", "gateway", "istio")
    ),
    "test_istio_and_gateway_api_production_overlays_preserve_same_security_context": KubernetesContractSpec(
        "platform_application_security_parity", ("production", "gateway", "istio")
    ),
    "test_istio_and_gateway_api_production_overlays_use_same_hmac_secret_references": KubernetesContractSpec(
        "platform_application_hmac_parity", ("production", "gateway", "istio")
    ),
    "test_observability_overlay_does_not_change_business_service_ports_or_message_channel_names": KubernetesContractSpec(
        "observability_business_boundary", ("production", "observability")
    ),
    "test_provider_specific_overlay_differences_are_limited_to_declared_platform_allowlist": KubernetesContractSpec(
        "platform_resource_allowlist", ("gateway", "istio")
    ),
}


WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}
APPLICATION_NAMES = {
    "encore-backend",
    "trade-settlement",
    "nsqd",
    "settlement-migrate",
    "quilkin",
}


def implemented_kubernetes_contracts() -> frozenset[str]:
    return frozenset(KUBERNETES_CONTRACTS)


def _documents(scopes: RenderedScopes, scope: str) -> list[dict[str, Any]]:
    documents = [document for _, document in scopes.get(scope, ())]
    assert documents, f"rendered Kubernetes scope {scope!r} is empty"
    return documents


def _metadata_name(document: Mapping[str, Any]) -> str:
    metadata = document.get("metadata") or {}
    return str(metadata.get("name") or "")


def _pod_spec(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if document.get("kind") not in WORKLOAD_KINDS:
        return None
    spec = document.get("spec") or {}
    if document.get("kind") == "CronJob":
        return (
            ((spec.get("jobTemplate") or {}).get("spec") or {})
            .get("template", {})
            .get("spec", {})
        )
    return (spec.get("template") or {}).get("spec") or {}


def _workloads(documents: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        pod_spec = _pod_spec(document)
        if pod_spec is None:
            continue
        name = _metadata_name(document)
        assert name, "rendered workload has no metadata.name"
        assert name not in result, f"duplicate rendered workload {name!r}"
        result[name] = pod_spec
    return result


def _resources(
    documents: Sequence[Mapping[str, Any]], kind: str, name: str
) -> Mapping[str, Any]:
    matches = [
        document
        for document in documents
        if document.get("kind") == kind and _metadata_name(document) == name
    ]
    assert len(matches) == 1, f"expected one rendered {kind} {name!r}, got {len(matches)}"
    return matches[0]


def _container(pod_spec: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    matches = [
        container
        for container in pod_spec.get("containers") or []
        if isinstance(container, dict) and container.get("name") == name
    ]
    assert len(matches) == 1, f"expected one rendered container {name!r}, got {len(matches)}"
    return matches[0]


def _service_port(
    service: Mapping[str, Any], name: str, port: int, protocol: str
) -> Mapping[str, Any]:
    matches = [
        item
        for item in (service.get("spec") or {}).get("ports") or []
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("port") == port
        and str(item.get("protocol", "TCP")).upper() == protocol
    ]
    assert len(matches) == 1, (
        f"expected one service port {name!r}/{port}/{protocol}, got {matches!r}"
    )
    return matches[0]


def _container_port(
    container: Mapping[str, Any], name: str, port: int, protocol: str
) -> Mapping[str, Any]:
    matches = [
        item
        for item in container.get("ports") or []
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("containerPort") == port
        and str(item.get("protocol", "TCP")).upper() == protocol
    ]
    assert len(matches) == 1, (
        f"expected one container port {name!r}/{port}/{protocol}, got {matches!r}"
    )
    return matches[0]


def _assert_cluster_internal(service: Mapping[str, Any]) -> None:
    spec = service.get("spec") or {}
    assert spec.get("type", "ClusterIP") == "ClusterIP", (
        _metadata_name(service),
        spec.get("type"),
    )
    assert not spec.get("externalIPs"), _metadata_name(service)
    assert not spec.get("loadBalancerIP"), _metadata_name(service)
    assert not spec.get("externalName"), _metadata_name(service)
    assert all("nodePort" not in port for port in spec.get("ports") or []), (
        _metadata_name(service),
        spec.get("ports"),
    )


def _assert_default_deny(documents: Sequence[Mapping[str, Any]]) -> None:
    policies = [
        document
        for document in documents
        if document.get("kind") == "NetworkPolicy"
        and (document.get("spec") or {}).get("podSelector") == {}
    ]
    assert policies, "production render has no namespace-wide default-deny NetworkPolicy"
    assert any(
        {"Ingress", "Egress"}.issubset(set((policy.get("spec") or {}).get("policyTypes") or []))
        and not (policy.get("spec") or {}).get("ingress")
        and not (policy.get("spec") or {}).get("egress")
        for policy in policies
    ), "default-deny policy does not deny both ingress and egress"


def _selected_policy(
    documents: Sequence[Mapping[str, Any]], target: str, policy_type: str
) -> Mapping[str, Any]:
    matches = []
    for document in documents:
        if document.get("kind") != "NetworkPolicy":
            continue
        spec = document.get("spec") or {}
        labels = (spec.get("podSelector") or {}).get("matchLabels") or {}
        if labels.get("app.kubernetes.io/name") != target:
            continue
        if policy_type not in (spec.get("policyTypes") or []):
            continue
        matches.append(document)
    assert len(matches) == 1, (
        f"expected one {policy_type} NetworkPolicy for {target!r}, got {len(matches)}"
    )
    return matches[0]


def _peer_edges(policy: Mapping[str, Any], direction: str) -> set[tuple[str, str, int, str]]:
    assert direction in {"ingress", "egress"}
    peer_key = "from" if direction == "ingress" else "to"
    edges: set[tuple[str, str, int, str]] = set()
    for rule in (policy.get("spec") or {}).get(direction) or []:
        peers = rule.get(peer_key) or []
        ports = rule.get("ports") or []
        assert peers and ports, f"{_metadata_name(policy)} contains an unbounded {direction} rule"
        for peer in peers:
            assert isinstance(peer, dict), peer
            assert "ipBlock" not in peer, f"{_metadata_name(policy)} uses an unreviewed ipBlock"
            if "podSelector" in peer:
                labels = (peer.get("podSelector") or {}).get("matchLabels") or {}
                peer_type = "pod"
                peer_name = str(labels.get("app.kubernetes.io/name") or "")
            elif "namespaceSelector" in peer:
                labels = (peer.get("namespaceSelector") or {}).get("matchLabels") or {}
                peer_type = "namespace"
                peer_name = str(labels.get("kubernetes.io/metadata.name") or "")
            else:
                raise AssertionError(
                    f"{_metadata_name(policy)} has a peer without an exact pod/namespace selector"
                )
            assert peer_name, f"{_metadata_name(policy)} has an empty peer selector"
            for port in ports:
                value = port.get("port")
                assert isinstance(value, int), (
                    f"{_metadata_name(policy)} must bind numeric policy ports, got {value!r}"
                )
                edges.add(
                    (peer_type, peer_name, value, str(port.get("protocol", "TCP")).upper())
                )
    return edges


def _assert_exact_network_graph(documents: Sequence[Mapping[str, Any]]) -> None:
    _assert_default_deny(documents)
    expected = {
        ("encore-backend", "ingress"): {
            ("pod", "quilkin", 26000, "UDP"),
            ("namespace", "istio-system", 4000, "TCP"),
        },
        ("encore-backend", "egress"): {
            ("pod", "nsqd", 4150, "TCP"),
            ("pod", "trade-settlement", 9092, "TCP"),
            ("pod", "postgres", 5432, "TCP"),
            ("namespace", "eve-trade-observability", 4318, "TCP"),
            ("namespace", "kube-system", 53, "UDP"),
            ("namespace", "kube-system", 53, "TCP"),
        },
        ("nsqd", "ingress"): {("pod", "encore-backend", 4150, "TCP")},
        ("trade-settlement", "ingress"): {
            ("pod", "encore-backend", 9092, "TCP")
        },
    }
    for (target, direction), wanted in expected.items():
        policy = _selected_policy(documents, target, direction.capitalize())
        assert _peer_edges(policy, direction) == wanted, (
            target,
            direction,
            _peer_edges(policy, direction),
            wanted,
        )


def _assert_settlement_ingress(documents: Sequence[Mapping[str, Any]]) -> None:
    _assert_default_deny(documents)
    policy = _selected_policy(documents, "trade-settlement", "Ingress")
    assert _peer_edges(policy, "ingress") == {
        ("pod", "encore-backend", 9092, "TCP")
    }


def _assert_application_security(documents: Sequence[Mapping[str, Any]]) -> None:
    workloads = _workloads(documents)
    for name in ("encore-backend", "trade-settlement"):
        assert name in workloads, f"production render is missing {name!r}"
        pod_spec = workloads[name]
        pod_security = pod_spec.get("securityContext") or {}
        assert pod_security.get("runAsNonRoot") is True, name
        assert (pod_security.get("seccompProfile") or {}).get("type") in {
            "RuntimeDefault",
            "Localhost",
        }, name
        for container in pod_spec.get("containers") or []:
            security = container.get("securityContext") or {}
            assert security.get("allowPrivilegeEscalation") is False, (name, container.get("name"))
            assert security.get("runAsNonRoot", True) is True, (name, container.get("name"))
            assert "ALL" in (((security.get("capabilities") or {}).get("drop")) or []), (
                name,
                container.get("name"),
            )


def _assert_secret_references(documents: Sequence[Mapping[str, Any]]) -> None:
    sensitive = (
        "password",
        "secret",
        "token",
        "private_key",
        "credential",
        "database_url",
    )
    references = 0
    forbidden: list[tuple[str, str]] = []
    for document in documents:
        name = _metadata_name(document)
        if document.get("kind") == "Secret":
            populated = {
                str(key): value
                for field in ("data", "stringData")
                for key, value in ((document.get(field) or {}).items())
                if value not in (None, "")
            }
            assert not populated, f"rendered Secret {name!r} embeds committed values: {sorted(populated)}"
        if document.get("kind") == "ConfigMap":
            for key, value in (document.get("data") or {}).items():
                if any(token in str(key).lower() for token in sensitive) and str(value).strip():
                    forbidden.append((name, str(key)))
        pod_spec = _pod_spec(document)
        if pod_spec is None:
            continue
        for container in list(pod_spec.get("containers") or []) + list(
            pod_spec.get("initContainers") or []
        ):
            for env_from in container.get("envFrom") or []:
                if (env_from or {}).get("secretRef"):
                    references += 1
            for env in container.get("env") or []:
                env_name = str((env or {}).get("name") or "")
                value_from = (env or {}).get("valueFrom") or {}
                if value_from.get("secretKeyRef"):
                    references += 1
                if any(token in env_name.lower() for token in sensitive):
                    if "value" in env and str(env.get("value") or "").strip():
                        forbidden.append((name, env_name))
                    assert value_from.get("secretKeyRef") or not str(env.get("value") or "").strip(), (
                        name,
                        env_name,
                    )
    assert not forbidden, f"rendered plaintext secret values: {forbidden}"
    assert references > 0, "production render contains no Secret-backed application values"


def _identity(document: Mapping[str, Any]) -> tuple[str, str, str]:
    api_version = str(document.get("apiVersion") or "")
    group = api_version.split("/", 1)[0] if "/" in api_version else "core"
    metadata = document.get("metadata") or {}
    return group, str(document.get("kind") or ""), f"{metadata.get('namespace') or ''}/{metadata.get('name') or ''}"


def _effective_documents(
    base: Sequence[Mapping[str, Any]], extra: Sequence[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    effective = {_identity(document): document for document in base}
    assert len(effective) == len(base), "base render contains duplicate resource identities"
    for document in extra:
        effective[_identity(document)] = document
    return list(effective.values())


def _application_snapshot(
    documents: Sequence[Mapping[str, Any]], dimension: str
) -> dict[tuple[str, str], Any]:
    snapshot: dict[tuple[str, str], Any] = {}
    for document in documents:
        name = _metadata_name(document)
        pod_spec = _pod_spec(document)
        if pod_spec is None or name not in APPLICATION_NAMES:
            continue
        if dimension == "probes":
            for container in pod_spec.get("containers") or []:
                container_name = str(container.get("name") or "")
                snapshot[(name, container_name)] = {
                    "readinessProbe": container.get("readinessProbe"),
                    "livenessProbe": container.get("livenessProbe"),
                }
        elif dimension == "resources":
            for container in list(pod_spec.get("containers") or []) + list(
                pod_spec.get("initContainers") or []
            ):
                snapshot[(name, str(container.get("name") or ""))] = container.get("resources")
        elif dimension == "security":
            snapshot[(name, "<pod>")] = pod_spec.get("securityContext")
            for container in list(pod_spec.get("containers") or []) + list(
                pod_spec.get("initContainers") or []
            ):
                snapshot[(name, str(container.get("name") or ""))] = container.get("securityContext")
        elif dimension == "hmac":
            for container in pod_spec.get("containers") or []:
                refs = [
                    (str(env.get("name") or ""), (env.get("valueFrom") or {}).get("secretKeyRef"))
                    for env in container.get("env") or []
                    if "HMAC" in str(env.get("name") or "").upper()
                ]
                if refs:
                    snapshot[(name, str(container.get("name") or ""))] = refs
        else:  # pragma: no cover - callers use a closed dimension set
            raise AssertionError(dimension)
    return snapshot


def _assert_platform_application_parity(
    scopes: RenderedScopes, dimension: str
) -> None:
    production = _documents(scopes, "production")
    expected = _application_snapshot(production, dimension)
    assert expected, f"production render has no application {dimension} values"
    if dimension == "probes":
        required = {
            key
            for key, value in expected.items()
            if key[0] in {"encore-backend", "trade-settlement"}
            and value.get("readinessProbe")
            and value.get("livenessProbe")
        }
        assert {key for key in expected if key[0] in {"encore-backend", "trade-settlement"}} <= required
    if dimension == "hmac":
        flattened = [ref for refs in expected.values() for _, ref in refs]
        assert flattened and all(isinstance(ref, dict) and ref.get("name") and ref.get("key") for ref in flattened)
    for scope in ("gateway", "istio"):
        actual = _application_snapshot(
            _effective_documents(production, _documents(scopes, scope)), dimension
        )
        assert actual == expected, f"{scope} changes application {dimension}: {actual!r} != {expected!r}"


def _assert_observability_boundary(scopes: RenderedScopes) -> None:
    production = _documents(scopes, "production")
    observability = _documents(scopes, "observability")
    production_identities = {_identity(document) for document in production}
    collisions = production_identities & {_identity(document) for document in observability}
    assert not collisions, f"observability render replaces business resources: {sorted(collisions)!r}"
    business_names = APPLICATION_NAMES | {"encore-backend-config", "trade-settlement-config"}
    overrides = [
        _identity(document)
        for document in observability
        if _metadata_name(document) in business_names
        and document.get("kind") in WORKLOAD_KINDS | {"Service", "ConfigMap", "Secret"}
    ]
    assert not overrides, f"observability render defines business configuration: {overrides!r}"
    assert any("otel" in _metadata_name(document) for document in observability), (
        "observability scope contains no OpenTelemetry resource"
    )


def _assert_platform_allowlist(scopes: RenderedScopes) -> None:
    allowed = {
        "gateway": {
            "Namespace",
            "ServiceAccount",
            "GatewayClass",
            "ClusterIssuer",
            "Gateway",
            "NetworkPolicy",
            "Telemetry",
        },
        "istio": {"Namespace", "IstioOperator"},
    }
    for scope, allowed_kinds in allowed.items():
        documents = _documents(scopes, scope)
        assert {str(document.get("kind") or "") for document in documents} <= allowed_kinds
        assert not [
            _identity(document)
            for document in documents
            if _metadata_name(document) in APPLICATION_NAMES
            or (document.get("metadata") or {}).get("namespace") == "eve-trade"
        ], f"{scope} contains an application/platform boundary violation"


def validate_kubernetes_contract(name: str, scopes: RenderedScopes) -> None:
    spec = KUBERNETES_CONTRACTS[name]
    for scope in spec.scopes:
        _documents(scopes, scope)
    production = _documents(scopes, "production")
    workloads = _workloads(production)

    if spec.oracle == "service_account_token_automount":
        service_accounts = {
            _metadata_name(document): (document.get("automountServiceAccountToken") is False)
            for document in production
            if document.get("kind") == "ServiceAccount"
        }
        checked = 0
        for workload_name, pod_spec in workloads.items():
            if workload_name not in APPLICATION_NAMES:
                continue
            checked += 1
            assert pod_spec.get("automountServiceAccountToken") is not True, workload_name
            account = str(pod_spec.get("serviceAccountName") or "default")
            assert pod_spec.get("automountServiceAccountToken") is False or service_accounts.get(account) is True, (
                workload_name,
                account,
            )
        assert checked >= 4, f"expected application workloads, checked only {checked}"
        return

    if spec.oracle == "secret_references":
        _assert_secret_references(production)
        return

    if spec.oracle == "exact_application_network_graph":
        _assert_exact_network_graph(production)
        return

    if spec.oracle == "settlement_ingress":
        _assert_settlement_ingress(production)
        return

    if spec.oracle == "postgres_ingress_boundary":
        _assert_default_deny(production)
        assert "postgres" not in workloads, "production must use the externally managed PostgreSQL boundary"
        local = _documents(scopes, "local")
        postgres = _resources(local, "Service", "postgres")
        _assert_cluster_internal(postgres)
        return

    if spec.oracle == "host_network_boundary":
        checked = 0
        for workload_name, pod_spec in workloads.items():
            if workload_name not in APPLICATION_NAMES:
                continue
            checked += 1
            if pod_spec.get("hostNetwork") is True:
                assert workload_name == "quilkin", workload_name
                annotations = pod_spec.get("annotations") or {}
                assert annotations.get("eve-trade.io/host-network-required") == "true"
        assert checked >= 4
        return

    if spec.oracle == "postgres_service_exposure":
        assert not [
            document
            for document in production
            if document.get("kind") == "Service" and _metadata_name(document) == "postgres"
        ], "production unexpectedly renders an in-cluster PostgreSQL Service"
        _assert_cluster_internal(_resources(_documents(scopes, "local"), "Service", "postgres"))
        return

    if spec.oracle == "platform_overlay_security":
        _assert_application_security(production)
        platform_scope = next(scope for scope in spec.scopes if scope != "production")
        platform_documents = _documents(scopes, platform_scope)
        overrides = [
            _metadata_name(document)
            for document in platform_documents
            if _pod_spec(document) is not None
            and _metadata_name(document) in {"encore-backend", "trade-settlement"}
        ]
        assert not overrides, (
            f"{platform_scope} platform overlay replaces application workloads instead of preserving "
            f"the production security context: {overrides}"
        )
        return

    if spec.oracle == "settlement_service_exposure":
        _assert_cluster_internal(_resources(production, "Service", "trade-settlement"))
        return

    if spec.oracle == "quilkin_udp_route":
        quilkin = _container(workloads["quilkin"], "quilkin")
        backend = _container(workloads["encore-backend"], "encore-backend")
        _container_port(quilkin, "udp", 26001, "UDP")
        _container_port(backend, "quilkin-udp", 26000, "UDP")
        endpoint_values = [
            env.get("value")
            for env in quilkin.get("env") or []
            if env.get("name") == "QUILKIN_BACKEND_ENDPOINT"
        ]
        assert endpoint_values == ["encore-backend.eve-trade.svc.cluster.local:26000"], (
            "Quilkin endpoint must resolve only to the rendered encore-backend UDP port",
            endpoint_values,
        )
        quilkin_service = _resources(production, "Service", "quilkin")
        quilkin_port = _service_port(quilkin_service, "udp", 26001, "UDP")
        assert quilkin_port.get("targetPort") == "udp"
        backend_service = _resources(production, "Service", "encore-backend")
        backend_port = _service_port(backend_service, "quilkin-udp", 26000, "UDP")
        assert backend_port.get("targetPort") == "quilkin-udp"
        return

    if spec.oracle == "encore_readiness":
        probe = _container(workloads["encore-backend"], "encore-backend").get("readinessProbe") or {}
        assert probe.get("httpGet") == {"path": "/gateway/readyz", "port": "http"}
        return

    if spec.oracle == "settlement_readiness":
        probe = _container(workloads["trade-settlement"], "trade-settlement").get("readinessProbe") or {}
        assert probe.get("grpc") == {"port": 9092, "service": "readiness"}
        return

    if spec.oracle == "resource_requirements":
        checked = 0
        for workload_name, pod_spec in workloads.items():
            if workload_name not in APPLICATION_NAMES:
                continue
            for container in list(pod_spec.get("containers") or []) + list(
                pod_spec.get("initContainers") or []
            ):
                checked += 1
                resources = container.get("resources") or {}
                for field in ("requests", "limits"):
                    values = resources.get(field) or {}
                    assert values.get("cpu") not in (None, ""), (workload_name, container.get("name"), field, "cpu")
                    assert values.get("memory") not in (None, ""), (workload_name, container.get("name"), field, "memory")
        assert checked >= 5
        return

    if spec.oracle == "gateway_udp_ports":
        backend = _container(workloads["encore-backend"], "encore-backend")
        _container_port(backend, "quilkin-udp", 26000, "UDP")
        service = _resources(production, "Service", "encore-backend")
        port = _service_port(service, "quilkin-udp", 26000, "UDP")
        assert port.get("targetPort") == "quilkin-udp"
        return

    if spec.oracle == "settlement_grpc_ports":
        settlement = _container(workloads["trade-settlement"], "trade-settlement")
        _container_port(settlement, "grpc", 9092, "TCP")
        service = _resources(production, "Service", "trade-settlement")
        port = _service_port(service, "grpc", 9092, "TCP")
        assert port.get("targetPort") == "grpc"
        return

    if spec.oracle == "platform_application_probe_parity":
        _assert_platform_application_parity(scopes, "probes")
        return

    if spec.oracle == "platform_application_resource_parity":
        _assert_platform_application_parity(scopes, "resources")
        return

    if spec.oracle == "platform_application_security_parity":
        _assert_platform_application_parity(scopes, "security")
        return

    if spec.oracle == "platform_application_hmac_parity":
        _assert_platform_application_parity(scopes, "hmac")
        return

    if spec.oracle == "observability_business_boundary":
        _assert_observability_boundary(scopes)
        return

    if spec.oracle == "platform_resource_allowlist":
        _assert_platform_allowlist(scopes)
        return

    raise AssertionError(f"unknown Kubernetes semantic oracle {spec.oracle!r} for {name}")
