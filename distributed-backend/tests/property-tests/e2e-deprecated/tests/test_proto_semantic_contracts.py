from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from eve_trade_hypothesis.contracts.proto_contracts import (
    PROTO_CONTRACTS,
    binding_metadata,
    validate_proto_contract,
)
from eve_trade_hypothesis.requirements import requirements_by_name


REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.mark.parametrize("name", sorted(PROTO_CONTRACTS))
def test_each_proto_binding_executes_its_exact_oracle_without_external_buf(name):
    validate_proto_contract(name, REPO_ROOT, execute_buf=False)


def test_proto_requirement_bindings_are_exactly_the_executable_registry():
    requirements = requirements_by_name()
    bound = {
        name: requirement["semantic_binding"]
        for name, requirement in requirements.items()
        if requirement.get("semantic_binding", {}).get("family") == "protobuf_buf"
    }
    assert bound == {name: binding_metadata(spec) for name, spec in PROTO_CONTRACTS.items()}
    assert all(requirements[name]["implementation_status"] == "IMPLEMENTED" for name in bound)


def _canary_module():
    path = REPO_ROOT / "scripts" / "verify_buf_breaking_canaries.py"
    spec = importlib.util.spec_from_file_location("test_buf_canaries", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_buf_breaking_canary_harness_requires_clean_control_and_rejects_each_mutation():
    module = _canary_module()

    def runner(command, **kwargs):
        is_control = command[1] == "breaking" and command[2] == command[4]
        return subprocess.CompletedProcess(
            command,
            0 if command[1] == "build" or is_control else 100,
            stdout="",
            stderr="breaking change detected" if not is_control else "",
        )

    observations = module.verify_breaking_canaries(runner=runner)
    assert {item["case"] for item in observations} == set(module.BREAKING_CASES)
    assert all(item["candidate_build_exit_code"] == 0 for item in observations)
    assert all(item["breaking_exit_code"] != 0 for item in observations)


def test_buf_breaking_canary_harness_rejects_vacuous_detector_that_accepts_mutations():
    module = _canary_module()

    def always_green(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    with pytest.raises(AssertionError, match="accepted breaking canary"):
        module.verify_breaking_canaries(runner=always_green)


def test_buf_breaking_canary_harness_distinguishes_invalid_candidate_from_breaking_result():
    module = _canary_module()

    def invalid_candidate(command, **kwargs):
        is_control = command[1] == "breaking" and command[2] == command[4]
        return subprocess.CompletedProcess(
            command,
            0 if is_control else 1,
            stdout="",
            stderr="parse failure",
        )

    with pytest.raises(RuntimeError, match="not a valid candidate schema"):
        module.verify_breaking_canaries(runner=invalid_candidate)


def _mutated_proto_root(tmp_path: Path, relative: str, old: str, new: str) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "proto", root / "proto")
    path = root / "proto" / relative
    source = path.read_text(encoding="utf-8")
    assert old in source
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return root


def test_identifier_rule_oracle_rejects_removed_uuid_rule(tmp_path: Path):
    root = _mutated_proto_root(
        tmp_path,
        "eve/trade/v1/trade.proto",
        'string trade_instance_id = 3 [(buf.validate.field).string.(eve.validation.v1.uuid_string) = true];',
        "string trade_instance_id = 3;",
    )
    with pytest.raises(AssertionError, match="uuid_string"):
        validate_proto_contract(
            "test_protovalidate_rules_are_present_on_every_required_business_identifier",
            root,
        )


def test_positive_quantity_oracle_rejects_removed_positive_rule(tmp_path: Path):
    root = _mutated_proto_root(
        tmp_path,
        "eve/market/v1/market.proto",
        'int64 quantity = 5 [(buf.validate.field).int64.(eve.validation.v1.positive_int64) = true];',
        "int64 quantity = 5;",
    )
    with pytest.raises(AssertionError, match="positive_int64"):
        validate_proto_contract(
            "test_protovalidate_rules_require_positive_trade_quantity",
            root,
        )


def test_isk_rule_oracle_rejects_negative_optional_amount_boundary(tmp_path: Path):
    root = _mutated_proto_root(
        tmp_path,
        "eve/trade/v1/trade.proto",
        "int64 return_isk_amount = 10 [(buf.validate.field).int64.gte = 0];",
        "int64 return_isk_amount = 10 [(buf.validate.field).int64.gte = -1];",
    )
    with pytest.raises(AssertionError, match="nonnegative"):
        validate_proto_contract(
            "test_protovalidate_rules_require_nonnegative_or_positive_isk_fields_according_to_domain_contract",
            root,
        )
