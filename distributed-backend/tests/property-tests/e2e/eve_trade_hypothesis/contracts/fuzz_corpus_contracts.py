from __future__ import annotations

"""Structured bindings for the committed gateway fuzz regression corpus."""

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


TARGET = "FuzzAuthenticatedPayloadNeverAcceptsAnUnboundPrincipal"
CORPUS_DIRECTORY = Path("distributed-backend/src/gateway/testdata/fuzz") / TARGET
METADATA_PATH = Path("distributed-backend/src/gateway/testdata/fuzz-regressions.json")


@dataclass(frozen=True)
class FuzzCorpusContractSpec:
    oracle: str
    seed: str | None = None
    capabilities: tuple[str, ...] = ("go_fuzz_corpus", "structured_seed_observation")


FUZZ_CORPUS_CONTRACTS: dict[str, FuzzCorpusContractSpec] = {
    "test_every_committed_gateway_fuzz_seed_is_executed_before_random_fuzz_iterations": FuzzCorpusContractSpec(
        "ci_seed_execution", capabilities=("go_fuzz", "dagger_command_plan")
    ),
    "test_fuzz_corpus_is_identical_between_local_and_ci_checkout": FuzzCorpusContractSpec(
        "corpus_snapshot_identity", capabilities=("dagger_source_snapshot", "corpus_metadata")
    ),
    "test_fuzz_regression_seed_rejection_is_deterministic_across_one_hundred_runs": FuzzCorpusContractSpec(
        "hundred_run_rejection", capabilities=("go_native_test", "real_gateway_parser")
    ),
    "test_gateway_fuzz_corpus_contains_seed_with_duplicate_json_security_field": FuzzCorpusContractSpec(
        "duplicate_json_key", "regression-duplicate-security-key"
    ),
    "test_gateway_fuzz_corpus_contains_seed_with_integer_overflow_boundary": FuzzCorpusContractSpec(
        "integer_overflow", "regression-integer-overflow"
    ),
    "test_gateway_fuzz_corpus_contains_seed_with_invalid_utf8": FuzzCorpusContractSpec(
        "invalid_utf8", "regression-invalid-utf8"
    ),
    "test_gateway_fuzz_corpus_contains_seed_with_nested_json_at_depth_limit": FuzzCorpusContractSpec(
        "nesting_depth", "regression-nesting-depth-65"
    ),
    "test_gateway_fuzz_corpus_contains_seed_with_trailing_non_whitespace_bytes": FuzzCorpusContractSpec(
        "trailing_bytes", "regression-trailing-non-whitespace"
    ),
    "test_gateway_fuzz_corpus_seed_names_or_metadata_identify_regression_issue_when_derived_from_bug": FuzzCorpusContractSpec(
        "regression_metadata", capabilities=("corpus_metadata", "source_traceability")
    ),
    "test_rust_parser_or_proto_fuzz_regression_seed_suite_runs_in_ci_when_rust_fuzz_targets_exist": FuzzCorpusContractSpec(
        "rust_fuzz_ci_conditional", capabilities=("cargo_fuzz_inventory", "dagger_command_plan")
    ),
}


def binding_metadata(spec: FuzzCorpusContractSpec) -> dict[str, Any]:
    result: dict[str, Any] = {
        "family": "fuzz_corpus",
        "oracle": spec.oracle,
        "capabilities": list(spec.capabilities),
    }
    if spec.seed is not None:
        result["parameters"] = {"seed": spec.seed, "target": TARGET}
    return result


def decode_go_fuzz_seed(path: Path) -> bytes:
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip().splitlines()
    assert len(lines) == 2 and lines[0] == "go test fuzz v1", path
    match = re.fullmatch(r"\[\]byte\((.+)\)", lines[1])
    assert match, f"unsupported Go fuzz corpus encoding: {path}"
    value = ast.literal_eval("b" + match.group(1))
    assert isinstance(value, bytes)
    return value


def _load_corpus(root: Path) -> dict[str, bytes]:
    directory = root / CORPUS_DIRECTORY
    assert directory.is_dir(), directory
    corpus = {
        path.name: decode_go_fuzz_seed(path)
        for path in sorted(directory.iterdir())
        if path.is_file()
    }
    assert corpus, "gateway fuzz corpus is empty"
    return corpus


def _load_metadata(root: Path) -> dict[str, Any]:
    document = json.loads((root / METADATA_PATH).read_text(encoding="utf-8"))
    assert document.get("schema_version") == "eve-trade.gateway-fuzz-regressions/v1"
    assert document.get("target") == TARGET
    assert isinstance(document.get("seeds"), dict)
    return document


def _max_json_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_max_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_max_json_depth(item) for item in value), default=0)
    return 0


def seed_satisfies_oracle(oracle: str, payload: bytes) -> bool:
    if oracle == "invalid_utf8":
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return False

    text = payload.decode("utf-8")
    if oracle == "duplicate_json_key":
        duplicates: list[str] = []

        def observe_pairs(pairs):
            seen: set[str] = set()
            for key, _ in pairs:
                if key in seen:
                    duplicates.append(str(key))
                seen.add(str(key))
            return dict(pairs)

        json.loads(text, object_pairs_hook=observe_pairs)
        return any(key in {"key_id", "signature", "algorithm", "schema_version"} for key in duplicates)

    if oracle == "trailing_bytes":
        decoder = json.JSONDecoder()
        _, end = decoder.raw_decode(text)
        return bool(text[end:].strip())

    value = json.loads(text)
    if oracle == "integer_overflow":
        pending = [value]
        while pending:
            current = pending.pop()
            if isinstance(current, dict):
                pending.extend(current.values())
            elif isinstance(current, list):
                pending.extend(current)
            elif isinstance(current, int) and not (-(2**63) <= current <= 2**63 - 1):
                return True
        return False
    if oracle == "nesting_depth":
        return _max_json_depth(value) > 64
    raise AssertionError(f"unsupported seed oracle: {oracle}")


def rust_fuzz_ci_is_wired(targets: tuple[Path, ...], rust_stage_source: str) -> bool:
    if not targets:
        return True
    return "cargo fuzz" in rust_stage_source and all(target.stem in rust_stage_source for target in targets)


def validate_fuzz_corpus_contract(name: str, root: Path) -> None:
    assert name in FUZZ_CORPUS_CONTRACTS, f"unknown fuzz corpus contract: {name}"
    spec = FUZZ_CORPUS_CONTRACTS[name]
    corpus = _load_corpus(root)
    metadata = _load_metadata(root)
    go_stage = (root / ".github" / "dagger" / "go.py").read_text(encoding="utf-8")

    if spec.seed is not None:
        assert spec.seed in corpus
        assert seed_satisfies_oracle(spec.oracle, corpus[spec.seed]), (
            spec.seed,
            spec.oracle,
        )
        return

    if spec.oracle == "ci_seed_execution":
        assert "GOFLAGS=-mod=mod encore test ./..." in go_stage
        assert "-fuzz '^FuzzAuthenticatedPayload'" in go_stage
        assert "-fuzztime 10s" in go_stage
        assert set(corpus) == set(metadata["seeds"])
        return

    if spec.oracle == "corpus_snapshot_identity":
        assert set(corpus) == set(metadata["seeds"])
        common = (root / ".github" / "dagger" / "_common.py").read_text(encoding="utf-8")
        assert "dag.host().directory" in common
        assert "testdata/fuzz" not in common, "Dagger source snapshot excludes the fuzz corpus"
        return

    if spec.oracle == "hundred_run_rejection":
        gateway_test = (root / "distributed-backend" / "src" / "gateway" / "udp_test.go").read_text(encoding="utf-8")
        assert "TestCommittedGatewayFuzzCorpusRejectsDeterministicallyAcrossOneHundredParses" in gateway_test
        assert "attempt < 100" in gateway_test
        assert "committedGatewayFuzzCorpus(t)" in gateway_test
        assert "accepted on attempt" in gateway_test and "rejection changed on attempt" in gateway_test
        return

    if spec.oracle == "regression_metadata":
        assert set(corpus) == set(metadata["seeds"])
        for seed, record in metadata["seeds"].items():
            assert isinstance(record, dict)
            source = str(record.get("regression_source") or "")
            property_text = str(record.get("property") or "")
            assert source and property_text, seed
            assert (root / source).exists(), (seed, source)
        return

    if spec.oracle == "rust_fuzz_ci_conditional":
        targets = tuple(
            sorted(
                path
                for path in (root / "distributed-backend" / "src" / "trade-settlement").rglob("*.rs")
                if "fuzz_targets" in path.parts
            )
        )
        rust_stage = (root / ".github" / "dagger" / "rust.py").read_text(encoding="utf-8")
        assert rust_fuzz_ci_is_wired(targets, rust_stage), targets
        return

    raise AssertionError(f"unhandled fuzz corpus oracle: {spec.oracle}")
