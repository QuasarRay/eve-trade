from __future__ import annotations

import copy
from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.kubernetes_contracts import (
    KUBERNETES_CONTRACTS,
    validate_kubernetes_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


SOURCE = Path("rendered-kustomization.yaml")


def _container(name: str) -> dict:
    return {
        "name": name,
        "image": f"example.invalid/{name}@sha256:" + "a" * 64,
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "1", "memory": "512Mi"},
        },
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "runAsNonRoot": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }


def _workload(kind: str, name: str, container: dict) -> dict:
    return {
        "apiVersion": "apps/v1" if kind != "Job" else "batch/v1",
        "kind": kind,
        "metadata": {"name": name},
        "spec": {
            "template": {
                "spec": {
                    "serviceAccountName": name,
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [container],
                }
            }
        },
    }


def _service(name: str, ports: list[dict], service_type: str = "ClusterIP") -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name},
        "spec": {"type": service_type, "ports": ports},
    }


def _policy(
    name: str,
    target: str | None,
    direction: str,
    rules: list[tuple[str, str, int, str]],
) -> dict:
    selector = {} if target is None else {"matchLabels": {"app.kubernetes.io/name": target}}
    peer_key = "from" if direction == "ingress" else "to"
    rendered_rules = []
    for peer_type, peer_name, port, protocol in rules:
        label = (
            {"app.kubernetes.io/name": peer_name}
            if peer_type == "pod"
            else {"kubernetes.io/metadata.name": peer_name}
        )
        rendered_rules.append(
            {
                peer_key: [{f"{peer_type}Selector": {"matchLabels": label}}],
                "ports": [{"port": port, "protocol": protocol}],
            }
        )
    spec = {"podSelector": selector, "policyTypes": [direction.capitalize()]}
    spec[direction] = rendered_rules
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": name},
        "spec": spec,
    }


def _valid_scopes() -> dict[str, list[tuple[Path, dict]]]:
    backend = _container("encore-backend")
    backend["ports"] = [
        {"name": "http", "containerPort": 4000},
        {"name": "quilkin-udp", "containerPort": 26000, "protocol": "UDP"},
    ]
    backend["envFrom"] = [{"secretRef": {"name": "market-database"}}]
    backend["env"] = [
        {
            "name": "API_GATEWAY_UDP_HMAC_SECRET",
            "valueFrom": {
                "secretKeyRef": {"name": "gateway-edge-auth", "key": "hmac"}
            },
        }
    ]
    backend["readinessProbe"] = {
        "httpGet": {"path": "/gateway/readyz", "port": "http"}
    }
    backend["livenessProbe"] = {
        "httpGet": {"path": "/gateway/healthz", "port": "http"}
    }

    settlement = _container("trade-settlement")
    settlement["ports"] = [{"name": "grpc", "containerPort": 9092}]
    settlement["envFrom"] = [{"secretRef": {"name": "trade-settlement-database"}}]
    settlement["readinessProbe"] = {
        "grpc": {"port": 9092, "service": "readiness"}
    }
    settlement["livenessProbe"] = {
        "grpc": {"port": 9092, "service": "liveness"}
    }

    quilkin = _container("quilkin")
    quilkin["ports"] = [
        {"name": "udp", "containerPort": 26001, "protocol": "UDP"}
    ]
    quilkin["env"] = [
        {
            "name": "QUILKIN_BACKEND_ENDPOINT",
            "value": "encore-backend.eve-trade.svc.cluster.local:26000",
        }
    ]

    production = [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": name},
            "automountServiceAccountToken": False,
        }
        for name in (
            "encore-backend",
            "trade-settlement",
            "nsqd",
            "settlement-migrate",
            "quilkin",
        )
    ]
    production.extend(
        [
            _workload("Deployment", "encore-backend", backend),
            _workload("Deployment", "trade-settlement", settlement),
            _workload("StatefulSet", "nsqd", _container("nsqd")),
            _workload("Job", "settlement-migrate", _container("migrate")),
            _workload("Deployment", "quilkin", quilkin),
            _service(
                "encore-backend",
                [
                    {"name": "http", "port": 4000, "targetPort": "http"},
                    {
                        "name": "quilkin-udp",
                        "port": 26000,
                        "targetPort": "quilkin-udp",
                        "protocol": "UDP",
                    },
                ],
            ),
            _service(
                "trade-settlement",
                [{"name": "grpc", "port": 9092, "targetPort": "grpc"}],
            ),
            _service(
                "quilkin",
                [
                    {
                        "name": "udp",
                        "port": 26001,
                        "targetPort": "udp",
                        "protocol": "UDP",
                    }
                ],
                "LoadBalancer",
            ),
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "encore-backend-config"},
                "data": {"PORT": "4000"},
            },
            {
                "apiVersion": "networking.k8s.io/v1",
                "kind": "NetworkPolicy",
                "metadata": {"name": "default-deny"},
                "spec": {
                    "podSelector": {},
                    "policyTypes": ["Ingress", "Egress"],
                },
            },
            _policy(
                "encore-backend-ingress",
                "encore-backend",
                "ingress",
                [
                    ("pod", "quilkin", 26000, "UDP"),
                    ("namespace", "istio-system", 4000, "TCP"),
                ],
            ),
            _policy(
                "encore-backend-egress",
                "encore-backend",
                "egress",
                [
                    ("pod", "nsqd", 4150, "TCP"),
                    ("pod", "trade-settlement", 9092, "TCP"),
                    ("pod", "postgres", 5432, "TCP"),
                    ("namespace", "eve-trade-observability", 4318, "TCP"),
                    ("namespace", "kube-system", 53, "UDP"),
                    ("namespace", "kube-system", 53, "TCP"),
                ],
            ),
            _policy(
                "nsqd-ingress",
                "nsqd",
                "ingress",
                [("pod", "encore-backend", 4150, "TCP")],
            ),
            _policy(
                "trade-settlement-ingress",
                "trade-settlement",
                "ingress",
                [("pod", "encore-backend", 9092, "TCP")],
            ),
        ]
    )
    local = [
        _service(
            "postgres",
            [{"name": "postgres", "port": 5432, "targetPort": "postgres"}],
        )
    ]
    return {
        "production": [(SOURCE, item) for item in production],
        "local": [(SOURCE, item) for item in local],
        "gateway": [
            (SOURCE, {"apiVersion": "gateway.networking.k8s.io/v1", "kind": "Gateway", "metadata": {"name": "eve-trade"}})
        ],
        "istio": [
            (SOURCE, {"apiVersion": "install.istio.io/v1alpha1", "kind": "IstioOperator", "metadata": {"name": "eve-trade"}})
        ],
        "observability": [
            (
                SOURCE,
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "otel-collector", "namespace": "eve-trade-observability"},
                    "spec": {"template": {"spec": {"containers": [{"name": "otel-collector"}]}}},
                },
            )
        ],
    }


@pytest.mark.parametrize("name", sorted(KUBERNETES_CONTRACTS))
def test_every_rendered_kubernetes_binding_has_a_nonvacuous_oracle(name):
    validate_kubernetes_contract(name, _valid_scopes())


def test_kubernetes_bindings_are_the_exact_implemented_manifest_set():
    requirements = requirements_by_name()
    bound = {
        name
        for name, requirement in requirements.items()
        if (requirement.get("semantic_binding") or {}).get("family")
        == "rendered_kubernetes"
    }
    assert bound == set(KUBERNETES_CONTRACTS)
    for name in bound:
        requirement = requirements[name]
        spec = KUBERNETES_CONTRACTS[name]
        assert requirement["implementation_status"] == "IMPLEMENTED"
        assert requirement["semantic_binding"]["oracle"] == spec.oracle
        assert requirement["semantic_binding"]["scopes"] == list(spec.scopes)


@pytest.mark.parametrize(
    ("name", "mutation", "message"),
    [
        (
            "test_application_service_account_token_automount_is_disabled_for_workloads_that_do_not_call_kubernetes_api",
            "automount",
            "encore-backend",
        ),
        (
            "test_rendered_kubernetes_does_not_embed_plaintext_secrets",
            "secret",
            "plaintext",
        ),
        (
            "test_network_policy_allows_only_required_gateway_market_worker_settlement_dependency_edges",
            "network",
            "encore-backend",
        ),
        (
            "test_settlement_service_is_not_publicly_exposed_unless_explicitly_intended",
            "service",
            "trade-settlement",
        ),
        (
            "test_rendered_kubernetes_configures_resource_requests_and_limits",
            "resources",
            "memory",
        ),
        (
            "test_quilkin_service_routes_udp_traffic_only_to_configured_gateway_backend_port",
            "route",
            "endpoint",
        ),
        (
            "test_rendered_pod_security_context_is_preserved_in_gateway_api_production_overlay",
            "overlay",
            "replaces application workloads",
        ),
        (
            "test_istio_and_gateway_api_production_overlays_preserve_same_readiness_and_liveness_probes",
            "probe-parity",
            "changes application probes",
        ),
    ],
)
def test_rendered_kubernetes_oracles_reject_representative_counterexamples(
    name, mutation, message
):
    scopes = copy.deepcopy(_valid_scopes())
    production = [document for _, document in scopes["production"]]
    if mutation == "automount":
        workload = next(document for document in production if document.get("kind") == "Deployment" and document["metadata"]["name"] == "encore-backend")
        workload["spec"]["template"]["spec"]["automountServiceAccountToken"] = True
    elif mutation == "secret":
        config = next(document for document in production if document.get("kind") == "ConfigMap")
        config["data"]["DATABASE_PASSWORD"] = "plaintext-password"
    elif mutation == "network":
        policy = next(document for document in production if document.get("kind") == "NetworkPolicy" and document["metadata"]["name"] == "encore-backend-egress")
        policy["spec"]["egress"].append(
            {
                "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "unrelated"}}}],
                "ports": [{"port": 9999, "protocol": "TCP"}],
            }
        )
    elif mutation == "service":
        service = next(document for document in production if document.get("kind") == "Service" and document["metadata"]["name"] == "trade-settlement")
        service["spec"]["type"] = "LoadBalancer"
    elif mutation == "resources":
        workload = next(document for document in production if document.get("kind") == "Deployment" and document["metadata"]["name"] == "encore-backend")
        del workload["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["memory"]
    elif mutation == "route":
        workload = next(document for document in production if document.get("kind") == "Deployment" and document["metadata"]["name"] == "quilkin")
        workload["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "attacker.invalid:26000"
    elif mutation == "overlay":
        scopes["gateway"].append(
            (
                SOURCE,
                _workload("Deployment", "encore-backend", _container("encore-backend")),
            )
        )
    elif mutation == "probe-parity":
        replacement = _workload("Deployment", "encore-backend", copy.deepcopy(_container("encore-backend")))
        replacement["spec"]["template"]["spec"]["containers"][0]["readinessProbe"] = {
            "httpGet": {"path": "/wrong", "port": "http"}
        }
        replacement["spec"]["template"]["spec"]["containers"][0]["livenessProbe"] = {
            "httpGet": {"path": "/wrong", "port": "http"}
        }
        scopes["gateway"].append((SOURCE, replacement))
    else:  # pragma: no cover - parameter table is exhaustive
        raise AssertionError(mutation)

    with pytest.raises(AssertionError, match=message):
        validate_kubernetes_contract(name, scopes)
