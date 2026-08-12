from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from eve_trade_hypothesis.chaos_oracles import IMPLEMENTED_CHAOS_ORACLES
from eve_trade_hypothesis.contracts.repo_contracts import _executable_test_references
from eve_trade_hypothesis.external import ExternalContractDriver
from eve_trade_hypothesis.engine import execute_existing
from eve_trade_hypothesis.repo import CommandResult, RepoInspector
from eve_trade_hypothesis.requirements import (
    litmus_contracts_by_name,
    requirement_document,
    requirements_by_name,
)


INFRA_ROOT = Path(__file__).resolve().parents[2] / "infra"


def _load_infra_module(stem: str):
    path = INFRA_ROOT / f"{stem}.py"
    spec = importlib.util.spec_from_file_location(f"eve_trade_test_{stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_counts_are_derived_from_current_records():
    document = requirement_document()
    records = document["contracts"]
    assert document["counts"] == dict(
        sorted(Counter(record["mechanism"] for record in records).items())
    )
    assert document["implementation_status_counts"] == dict(
        sorted(Counter(record["implementation_status"] for record in records).items())
    )


def test_every_unimplemented_contract_has_an_exhaustive_blocker_record():
    unresolved = {
        "SEMANTIC_ORACLE_REQUIRED",
        "EXTERNAL_CAPABILITY_REQUIRED",
        "INFRASTRUCTURE_READY",
    }
    for name, requirement in requirements_by_name().items():
        if requirement["implementation_status"] in unresolved:
            assert requirement["blockers"], name
            assert all(isinstance(blocker, str) and blocker for blocker in requirement["blockers"])


def test_litmus_mapping_is_exact_and_has_complete_execution_evidence_model():
    requirements = requirements_by_name()
    chaos_names = {
        name for name, requirement in requirements.items() if requirement["mechanism"] == "LITMUS_CHAOS"
    }
    contracts = litmus_contracts_by_name()
    assert set(contracts) == chaos_names
    for name, contract in contracts.items():
        assert contract["contract"] == name
        assert contract["fault_family"]
        assert contract["target"]["selector"]
        assert contract["target"]["workload"]
        assert contract["generated_parameters"]
        assert contract["required_initial_state"]
        assert contract["precondition_probes"]
        assert contract["fault_injection"]["experiment"]
        assert contract["fault_effect_proof"]["required_distinct_sources"] >= 2
        assert contract["fault_effect_proof"]["chaos_result_passed_alone_is_sufficient"] is False
        assert contract["fault_window"]["activation"]
        assert contract["workload"]["overlap_proof"]
        assert contract["raw_observations"]
        assert contract["recovery"]["deadline_parameter"]
        assert contract["post_recovery_invariants"]
        assert contract["cleanup"]
        assert contract["evidence_schema"] == "eve-trade.chaos-evidence/v1"


def test_only_contracts_with_repository_owned_chaos_oracles_are_marked_implemented():
    requirements = requirements_by_name()
    implemented = {
        name
        for name, requirement in requirements.items()
        if requirement["mechanism"] == "LITMUS_CHAOS"
        and requirement["implementation_status"] == "IMPLEMENTED"
    }
    assert implemented == set(IMPLEMENTED_CHAOS_ORACLES)
    contracts = litmus_contracts_by_name()
    for name in implemented:
        contract = contracts[name]
        assert contract["fault_injection"]["annotation_check"] == "false"
        assert contract["workload"]["action"] == "in_cluster_service_probe_batch"
        assert contract["workload"]["effect_binding"]
        assert all(
            "PostgreSQL" not in observation
            for observation in contract["raw_observations"]
        )


def test_litmus_manifest_and_checked_out_chart_versions_are_independently_pinned(tmp_path):
    installer = _load_infra_module("install_litmus")
    manifest = json.loads((INFRA_ROOT / "litmus-contracts.json").read_text(encoding="utf-8"))
    installer.validate_manifest_pins(manifest["litmus_core"])

    wrong_manifest = dict(manifest["litmus_core"])
    wrong_manifest["operator_image_tag"] = "latest"
    with pytest.raises(installer.InstallError, match="operator_image_tag"):
        installer.validate_manifest_pins(wrong_manifest)

    chart = tmp_path / "litmus-agent"
    operator = chart / "charts" / "chaos-operator"
    operator.mkdir(parents=True)
    (chart / "Chart.yaml").write_text(
        "apiVersion: v2\nname: litmus-agent\nversion: 3.30.0\n",
        encoding="utf-8",
    )
    (operator / "Chart.yaml").write_text(
        'apiVersion: v2\nname: chaos-operator\nappVersion: "3.30.0"\n',
        encoding="utf-8",
    )
    installer.validate_chart_pins(chart)

    (operator / "Chart.yaml").write_text(
        'apiVersion: v2\nname: chaos-operator\nappVersion: "latest"\n',
        encoding="utf-8",
    )
    with pytest.raises(installer.InstallError, match="appVersion"):
        installer.validate_chart_pins(chart)


def test_dagger_pipeline_repository_root_is_independent_of_caller_working_directory():
    pipeline = _load_infra_module("pipeline")
    assert pipeline.REPO_ROOT == Path(__file__).resolve().parents[5]


def test_kind_pipeline_executes_the_validated_dagger_source_snapshot():
    source = (INFRA_ROOT / "pipeline.py").read_text(encoding="utf-8")
    assert '.with_directory("/validated-src", source)' in source
    assert "docker cp /validated-src/." in source
    assert 'f"{REPO_ROOT}:/src"' not in source
    assert 'executed.directory("/artifacts").export' in source


def test_post_cleanup_oracle_rejects_residual_network_effect():
    driver = _load_infra_module("litmus_driver")
    execution = object.__new__(driver.LitmusExecution)
    execution.baseline_samples = [
        {"success": True, "latency_ms": 10.0},
        {"success": True, "latency_ms": 12.0},
        {"success": True, "latency_ms": 14.0},
    ]

    execution.spec = {"fault_family": "packet_loss"}
    execution.case = {"network_loss_percent": 25}
    with pytest.raises(driver.DriverError, match="did not all reach"):
        execution.assert_post_cleanup_effect_absent(
            [
                {"success": True, "latency_ms": 11.0},
                {"success": False, "latency_ms": 2000.0},
            ]
        )

    execution.spec = {"fault_family": "network_latency"}
    execution.case = {"network_latency_ms": 200}
    with pytest.raises(driver.DriverError, match="latency remains"):
        execution.assert_post_cleanup_effect_absent(
            [
                {"success": True, "latency_ms": 180.0},
                {"success": True, "latency_ms": 200.0},
                {"success": True, "latency_ms": 220.0},
            ]
        )
    execution.assert_post_cleanup_effect_absent(
        [
            {"success": True, "latency_ms": 12.0},
            {"success": True, "latency_ms": 15.0},
            {"success": True, "latency_ms": 18.0},
        ]
    )


def test_parser_fuzz_contracts_fail_closed_until_a_real_parser_harness_exists():
    parser_fuzz = {
        name: requirement
        for name, requirement in requirements_by_name().items()
        if requirement.get("category") == 78
    }
    assert parser_fuzz
    assert {
        requirement["implementation_status"] for requirement in parser_fuzz.values()
    } == {"SEMANTIC_ORACLE_REQUIRED"}
    assert all("parser fuzz harness" in requirement["blockers"][0] for requirement in parser_fuzz.values())


def test_reference_scanner_distinguishes_executable_nodes_from_prose_examples():
    assert _executable_test_references(
        "pytest tests/test_trade.py::test_real_node -q\npytest -k test_selected_node"
    ) == {"test_real_node", "test_selected_node"}
    assert not _executable_test_references(
        "For example, call it test_placeholder_name; the file is test_trade_lifecycle.py."
    )


def test_native_existing_wrapper_uses_validated_repository_root_and_original_node(
    tmp_path, monkeypatch
):
    native_name = "test_native_contract"
    native_file = "test_native_contract.py"
    target = tmp_path / "distributed-backend" / "tests" / "e2e" / native_file
    target.parent.mkdir(parents=True)
    target.write_text("def test_native_contract():\n    pass\n", encoding="utf-8")
    calls = []

    class FakeRepo:
        root = tmp_path

        @staticmethod
        def require_repo():
            return None

        @staticmethod
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return CommandResult(tuple(argv), 0, "native passed", "")

    monkeypatch.setattr(
        "eve_trade_hypothesis.engine.load_catalog",
        lambda: {
            "existing": {
                native_name: {"native_files": {"experimental": native_file}}
            }
        },
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(target_branch="experimental", strict=True),
        repo=FakeRepo(),
    )

    execute_existing(runtime, native_name)

    assert calls
    assert calls[0][0][-3:] == [
        f"distributed-backend/tests/e2e/{native_file}::{native_name}",
        "-q",
        "--maxfail=1",
    ]


def test_every_external_route_declares_evidence_schema_and_fails_closed_without_driver(tmp_path):
    for name, requirement in requirements_by_name().items():
        if requirement["mechanism"] == "PLATFORM_EXTERNAL":
            assert requirement["observable"].startswith("fresh protocol-v3")
    name = next(
        name
        for name, requirement in requirements_by_name().items()
        if requirement["mechanism"] == "PLATFORM_EXTERNAL"
    )
    driver = ExternalContractDriver(
        None,
        repo_root=tmp_path,
        strict=True,
        role="evidence",
        run_id="pt-strict-no-driver",
    )
    with pytest.raises(pytest.fail.Exception, match="fail-closed"):
        driver.run(name, {"nonce": "missing-capability"})

    # Merely supplying a command cannot upgrade an unresolved, name-derived
    # platform oracle into an implemented semantic contract.
    commanded = ExternalContractDriver(
        "command-that-must-never-run",
        repo_root=tmp_path,
        strict=True,
        role="evidence",
        run_id="pt-strict-unresolved-driver",
    )
    with pytest.raises(pytest.fail.Exception, match="EXTERNAL_CAPABILITY_REQUIRED"):
        commanded.run(name, {"nonce": "still-missing-capability"})


def test_strict_chaos_route_cannot_skip_when_fault_driver_is_missing(tmp_path):
    name = next(iter(IMPLEMENTED_CHAOS_ORACLES))
    driver = ExternalContractDriver(
        None,
        repo_root=tmp_path,
        strict=True,
        role="fault",
        run_id="pt-strict-no-fault",
    )
    with pytest.raises(pytest.fail.Exception, match="requires fresh protocol-v3 raw evidence"):
        driver.run(name, {"category": 101, "nonce": "missing-fault"})


def test_kubernetes_inspector_renders_leaf_kustomizations_and_rejects_patch_only_scope(
    tmp_path, monkeypatch
):
    (tmp_path / "go.mod").write_text("module example.invalid/eve-trade\n", encoding="utf-8")
    base = tmp_path / "distributed-backend" / "orchestration" / "kubernetes" / "base"
    overlay = (
        tmp_path
        / "distributed-backend"
        / "orchestration"
        / "kubernetes"
        / "overlay"
        / "test"
    )
    base.mkdir(parents=True)
    overlay.mkdir(parents=True)
    (base / "kustomization.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - deployment.yaml\n",
        encoding="utf-8",
    )
    (base / "deployment.yaml").write_text(
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n",
        encoding="utf-8",
    )
    (overlay / "kustomization.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources:\n  - ../../base\n",
        encoding="utf-8",
    )

    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        rendered = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
spec:
  selector:
    matchLabels:
      app: app
  template:
    metadata:
      labels:
        app: app
    spec:
      containers:
        - name: app
          image: example.invalid/app@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"""
        return CommandResult(tuple(argv), 0, rendered, "")

    inspector = RepoInspector(tmp_path, strict=True)
    monkeypatch.setattr(inspector, "run", fake_run)
    documents = inspector.rendered_kubernetes_documents()

    assert len(calls) == 1
    assert calls[0][0][:2] == ["kubectl", "kustomize"]
    assert calls[0][0][2] == str(overlay.resolve())
    assert [document["kind"] for _, document in documents] == ["Deployment"]
