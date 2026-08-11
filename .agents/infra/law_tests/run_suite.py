from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
INFRA = HERE.parent
sys.path.insert(0, str(INFRA))
sys.path.insert(0, str(HERE))

from agentinfra.atomic import atomic_write_bytes
from agentinfra.law_runtime import resolve_law_layout
from agentinfra.laws import LawRunner
from agentinfra.release_source import build_deployment_tree
from build_traceability import build, project_root, record_evidence_digest, specification_roots, validate_capability_result
from constitutional_catalog import build_constitutional_catalog, constitutional_report_is_acceptable, evaluate_constitutional_catalog, render_constitutional_markdown
from scenarios import RequirementOutcome, run_family, run_requirement

ROOT = project_root(HERE)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()


def seal_record(record: dict) -> dict:
    record["evidence_digest"] = record_evidence_digest(record)
    return record


def protected_digests(root: Path) -> dict[str, str]:
    layout = resolve_law_layout(root)
    specification_paths = [
        path
        for specification_root in specification_roots(root)
        for path in sorted(specification_root.glob("*.md"))
    ]
    paths = [
        *specification_paths,
        *sorted(layout.unit_tests.glob("test_*.py")),
        *(sorted((root / "infra" / "property_tests").glob("test_*.py")) if layout.mode == "source" else []),
        *sorted(layout.law_tests.glob("*.py")),
        *sorted(layout.law_tests.glob("*.json")),
        layout.law_definitions / "framework.toml",
    ]
    return {path.relative_to(root).as_posix(): sha(path) for path in paths if path.is_file()}


class RecordingResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.records: dict[str, dict] = {}

    @staticmethod
    def name(test) -> str:
        return test._testMethodName

    def startTest(self, test):
        super().startTest(test)
        self.records[self.name(test)] = {"started": True, "finished": False, "started_at": now(), "monotonic": time.monotonic()}

    def _finish(self, test, outcome: str, detail: str, capability: str = "AVAILABLE"):
        record = self.records[self.name(test)]
        record.update(
            finished=True,
            finished_at=now(),
            duration_seconds=time.monotonic() - record.pop("monotonic"),
            outcome=outcome,
            capability_status=capability,
            oracle_count=1,
            detail=detail,
        )
        record["evidence_digest"] = evidence_digest({key: record.get(key) for key in ("outcome", "capability_status", "oracle_count", "detail")})

    def addSuccess(self, test):
        super().addSuccess(test); self._finish(test, "PASS", "original unittest executed successfully")

    def addFailure(self, test, err):
        super().addFailure(test, err); self._finish(test, "FAIL", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err); self._finish(test, "ERROR", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self._finish(test, "SKIP", reason, "UNCLASSIFIED"); self.records[self.name(test)]["justification"] = reason

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err); self._finish(test, "FAIL", "unexpected expected-failure marker: " + self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test); self._finish(test, "FAIL", "unexpected-success/xfail semantics are forbidden")


def run_units(root: Path) -> tuple[dict[str, dict], dict]:
    suite = unittest.defaultTestLoader.discover(str(resolve_law_layout(root).unit_tests), pattern="test_*.py")
    result = RecordingResult()
    suite.run(result)
    return result.records, {
        "collected": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skips": len(result.skipped),
        "unexpected_successes": len(result.unexpectedSuccesses),
    }


def record_requirement(result: RequirementOutcome, requirement: dict) -> dict:
    return {
        "started": True,
        "finished": True,
        "started_at": now(),
        "finished_at": now(),
        "outcome": result.outcome,
        "capability_status": result.capability_status,
        "required_capability": requirement["required_capability"],
        "required_observations": requirement["required_observations"],
        "oracle_count": result.oracle_count,
        "detail": result.detail,
        "scenario_evidence_digest": result.evidence_digest,
        "justification": result.justification,
    }


def run(root: Path) -> dict:
    layout = resolve_law_layout(root)
    scenario_workspace = None
    scenario_root = root
    if layout.mode == "source":
        scenario_workspace = tempfile.TemporaryDirectory(prefix="law-deployment-", dir=root)
        scenario_root = Path(scenario_workspace.name) / "fixture"
        build_deployment_tree(root, scenario_root)
    registry = build(root)
    before = protected_digests(root)
    requirements: dict[str, dict] = {}
    unit_records, unit_summary = run_units(root)
    builtin_files = [layout.law_definitions / "framework.toml"]
    builtin_results = {item.id: item for item in LawRunner(root).run(builtin_files)}
    template_path = (root / "laws" / "template.toml") if layout.mode == "source" else (root / ".agents" / "laws" / "template.toml")
    with template_path.open("rb") as stream:
        import tomllib
        template_ids = {item["id"] for item in tomllib.load(stream).get("law", [])}
    for item in registry["requirements"]:
        name = item["name"]
        prefix = item["source_file"][:2]
        if prefix == "00":
            record = unit_records.get(name)
            if record is None:
                record = {"started": False, "finished": False, "outcome": "ERROR", "capability_status": "AVAILABLE", "oracle_count": 0, "detail": "specified original unittest was not collected"}
            else:
                record = dict(record)
            record["required_capability"] = item["required_capability"]
            record["required_observations"] = item["required_observations"]
            requirements[name] = seal_record(record)
        elif prefix == "01":
            law = builtin_results.get(name)
            if law is None:
                requirements[name] = seal_record({"started": False, "finished": False, "outcome": "ERROR", "capability_status": "AVAILABLE", "required_capability": item["required_capability"], "required_observations": item["required_observations"], "oracle_count": 0, "detail": "specified built-in law was not executed"})
            else:
                execution_detail = law.detail
                record = {
                    "started": True,
                    "finished": True,
                    "started_at": now(),
                    "finished_at": now(),
                    "outcome": law.outcome,
                    "capability_status": law.capability_status,
                    "required_capability": item["required_capability"],
                    "required_observations": item["required_observations"],
                    "oracle_count": law.oracle_count,
                    "detail": f"built-in law {law.id} executed with outcome {law.outcome} and {law.oracle_count} oracle(s)",
                    "execution_detail": execution_detail,
                    "execution_detail_sha256": hashlib.sha256(execution_detail.encode("utf-8")).hexdigest(),
                    "definition_sha256_before": law.metadata.get("definition_sha256_before"),
                    "definition_sha256_after": law.metadata.get("definition_sha256_after"),
                }
                requirements[name] = seal_record(record)
        elif prefix == "02":
            passed = name in template_ids and name not in builtin_results
            record = {
                "started": True,
                "finished": True,
                "started_at": now(),
                "finished_at": now(),
                "outcome": "PASS" if passed else "FAIL",
                "capability_status": "AVAILABLE",
                "required_capability": item["required_capability"],
                "required_observations": item["required_observations"],
                "oracle_count": 3,
                "detail": "template law schema parsed, exact id present, and example remains outside default acceptance collection",
                "stronger_equivalent": True,
            }
            requirements[name] = seal_record(record)
        else:
            requirements[name] = seal_record(record_requirement(run_requirement(scenario_root, item), item))
    # This meta-law is a ledger assertion, not an inventory-presence assertion.
    # Bind it to the actual outcomes from the same outer run so a regressed legacy
    # test or built-in law cannot coexist with a passing completeness claim.
    legacy_meta = "test_every_existing_unit_test_and_builtin_law_still_passes_after_all_v4_0_flaw_fixes"
    if legacy_meta in requirements:
        legacy_names = {
            item["name"]
            for item in registry["requirements"]
            if item["source_file"].startswith(("00_", "01_"))
        }
        legacy_failures = sorted(
            name for name in legacy_names if requirements.get(name, {}).get("outcome") != "PASS"
        )
        record = dict(requirements[legacy_meta])
        record["oracle_count"] = int(record.get("oracle_count", 0)) + len(legacy_names)
        if legacy_failures:
            record["outcome"] = "FAIL"
            record["capability_status"] = "AVAILABLE"
            record["detail"] = "legacy regression/built-in failures in this ledger: " + ", ".join(legacy_failures[:12])
        else:
            record["detail"] = f"all {len(legacy_names)} legacy unit and built-in law results passed in this exact ledger"
        requirements[legacy_meta] = seal_record(record)
    after = protected_digests(root)
    integrity_errors = []
    if before != after:
        integrity_errors.append("protected specification/test/law definitions changed during suite execution")
    expected = {item["name"] for item in registry["requirements"]}
    if set(requirements) != expected:
        integrity_errors.append("result ledger is not a bijection with the 834-name specification")
    for name, record in requirements.items():
        if not record.get("started") or not record.get("finished") or int(record.get("oracle_count", 0)) <= 0:
            integrity_errors.append(f"{name} was missing, incomplete, or vacuous")
        try:
            validate_capability_result(record.get("required_capability"), record.get("outcome"), record.get("capability_status"))
        except RuntimeError as exc:
            integrity_errors.append(f"{name}: {exc}")
        if record.get("outcome") in {"UNAVAILABLE", "NOT_APPLICABLE"} and not record.get("justification"):
            integrity_errors.append(f"{name}: capability limitation lacks concrete justification")
    counts: dict[str, int] = {}
    capabilities: dict[str, int] = {}
    for record in requirements.values():
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1
        capability = record.get("capability_status", "AVAILABLE")
        capabilities[capability] = capabilities.get(capability, 0) + 1
    assurance = run_family(str(scenario_root.resolve()), "source-assurance")
    constitutional_observations = {
        f"source-assurance::{label}": (passed, detail)
        for label, passed, detail in assurance.observations
    }
    constitutional = evaluate_constitutional_catalog(
        build_constitutional_catalog(root),
        constitutional_observations,
    )
    failed = (
        any(record["outcome"] in {"FAIL", "ERROR"} for record in requirements.values())
        or bool(integrity_errors)
        or not constitutional_report_is_acceptable(constitutional)
    )
    has_capability_limitations = bool(
        counts.get("UNAVAILABLE")
        or counts.get("NOT_APPLICABLE")
        or constitutional["counts"].get("UNAVAILABLE")
    )
    report = {
        "schema": 2,
        "suite": "aegis-comprehensive-laws",
        "started_and_finished": True,
        "generated_at": now(),
        "root": str(root),
        "inventory_sha256": registry["inventory_sha256"],
        "requirement_count": len(requirements),
        "outcome": "FAIL" if failed else "PASS_WITH_EXPLICIT_CAPABILITY_LIMITATIONS" if has_capability_limitations else "PASS",
        "counts": dict(sorted(counts.items())),
        "capabilities": dict(sorted(capabilities.items())),
        "unit_suite": unit_summary,
        "constitutional": constitutional,
        "definition_digests_before": before,
        "definition_digests_after": after,
        "integrity_errors": integrity_errors,
        "requirements": requirements,
    }
    if scenario_workspace is not None:
        scenario_workspace.cleanup()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    output = args.output or resolve_law_layout(root).results / "latest.json"
    ledger = run(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(output, (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode("utf-8"), root=root, mode=0o600)
    constitutional_markdown = output.with_suffix(".constitutional.md")
    atomic_write_bytes(constitutional_markdown, render_constitutional_markdown(ledger["constitutional"]).encode("utf-8"), root=root, mode=0o600)
    print(json.dumps({"output": str(output), "constitutional_traceability": str(constitutional_markdown), "outcome": ledger["outcome"], "requirement_count": ledger["requirement_count"], "constitutional_law_count": ledger["constitutional"]["law_count"], "constitutional_counts": ledger["constitutional"]["counts"], "counts": ledger["counts"], "capabilities": ledger["capabilities"], "integrity_errors": ledger["integrity_errors"]}, indent=2, sort_keys=True))
    return 1 if ledger["outcome"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
