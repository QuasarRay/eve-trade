from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sys

from .process import run_process


OUTCOMES = {"PASSED", "FAILED", "UNAVAILABLE", "BLOCKED", "JUSTIFIED_SKIP"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LAW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")


class LawStatusError(RuntimeError):
    pass


_UNITTEST_RESULT_SENTINEL = "__AEGIS_UNITTEST_MODULE_RESULT__="
_UNITTEST_CHILD = r"""
import hashlib
import json
from pathlib import Path
import sys
import unittest

class ExactResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.test_records = []

    def _record(self, test, outcome, detail, capability_status="AVAILABLE"):
        self.test_records.append({
            "id": test.id(),
            "method": getattr(test, "_testMethodName", str(test)),
            "outcome": outcome,
            "capability_status": capability_status,
            "detail": detail,
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "PASS", "exact unittest oracle passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "FAIL", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "ERROR", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "SKIP", reason, "UNAVAILABLE")

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "EXPECTED_FAILURE", self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "UNEXPECTED_SUCCESS", "unexpected-success semantics are forbidden")

infra = Path(sys.argv[1]).resolve(strict=True)
directory = Path(sys.argv[2]).resolve(strict=True)
module_path = Path(sys.argv[3]).resolve(strict=True)
category = sys.argv[4]
sys.path.insert(0, str(infra))
sys.path.insert(0, str(directory))
if category == "property":
    suite = unittest.defaultTestLoader.discover(
        str(directory), pattern=module_path.name, top_level_dir=str(infra)
    )
else:
    suite = unittest.defaultTestLoader.discover(str(directory), pattern=module_path.name)
result = ExactResult()
suite.run(result)
successful = (
    result.testsRun
    - len(result.failures)
    - len(result.errors)
    - len(result.skipped)
    - len(result.expectedFailures)
    - len(result.unexpectedSuccesses)
)
payload = {
    "schema": 2,
    "module": module_path.name,
    "definition_sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(),
    "collected": result.testsRun,
    "successful_oracles": successful,
    "failures": len(result.failures),
    "errors": len(result.errors),
    "justified_capability_skips": [reason for _test, reason in result.skipped],
    "expected_failures": len(result.expectedFailures),
    "unexpected_successes": len(result.unexpectedSuccesses),
    "failed_tests": [str(test) for test, _detail in result.failures],
    "errored_tests": [str(test) for test, _detail in result.errors],
    "tests": result.test_records,
}
payload["ok"] = bool(successful) and not any(
    payload[key]
    for key in ("failures", "errors", "expected_failures", "unexpected_successes")
)
print("__AEGIS_UNITTEST_MODULE_RESULT__=" + json.dumps(payload, sort_keys=True))
"""


@dataclass(frozen=True)
class LawLayout:
    root: Path
    mode: str
    unit_tests: Path
    law_definitions: Path
    law_tests: Path
    specification: Path
    results: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise LawStatusError(f"{label} must be a canonical sha256")
    return value


def resolve_law_layout(root: Path) -> LawLayout:
    """Resolve authoritative law inputs while keeping every result under .aegis."""

    project = Path(root).resolve(strict=True)
    source = {
        "unit_tests": project / "infra" / "tests",
        "law_definitions": project / "infra" / "laws",
        "law_tests": project / "infra" / "law_tests",
        "specification": project / "tests-to-impl",
    }
    deployment = {
        "unit_tests": project / ".agents" / "infra" / "tests",
        "law_definitions": project / ".agents" / "infra" / "laws",
        "law_tests": project / ".agents" / "infra" / "law_tests",
        "specification": project / ".agents" / "tests-to-impl",
    }
    if all(path.is_dir() for path in source.values()):
        selected = source
        mode = "source"
    elif all(path.is_dir() for path in deployment.values()):
        selected = deployment
        mode = "deployment"
    else:
        missing_source = sorted(name for name, path in source.items() if not path.is_dir())
        missing_deployment = sorted(name for name, path in deployment.items() if not path.is_dir())
        raise LawStatusError(
            "incomplete Aegis law layout; "
            f"source missing={missing_source}, deployment missing={missing_deployment}"
        )
    return LawLayout(
        root=project,
        mode=mode,
        unit_tests=selected["unit_tests"],
        law_definitions=selected["law_definitions"],
        law_tests=selected["law_tests"],
        specification=selected["specification"],
        results=project / ".aegis" / "law-results",
    )


def run_unittest_module_isolated(
    infra_root: Path,
    test_file: Path,
    *,
    category: str,
    timeout: float = 240.0,
) -> dict:
    """Execute one source-assurance module in an import-isolated interpreter."""

    if category not in {"unit", "property"}:
        raise LawStatusError("unittest module category must be unit or property")
    infra = Path(infra_root).resolve(strict=True)
    if not infra.is_dir():
        raise LawStatusError("unittest infra root must be a directory")
    directory = (infra / ("tests" if category == "unit" else "property_tests")).resolve(strict=True)
    module_path = Path(test_file).resolve(strict=True)
    if module_path.parent != directory or not re.fullmatch(r"test_[A-Za-z0-9_]+\.py", module_path.name):
        raise LawStatusError("unittest module must be a direct canonical child of its category directory")
    definition_before = hashlib.sha256(module_path.read_bytes()).hexdigest()
    process = run_process(
        (
            sys.executable,
            "-B",
            "-c",
            _UNITTEST_CHILD,
            str(infra),
            str(directory),
            str(module_path),
            category,
        ),
        cwd=infra,
        timeout=timeout,
        capture_limit=1_000_000,
    )
    definition_after = hashlib.sha256(module_path.read_bytes()).hexdigest()
    if definition_after != definition_before:
        raise LawStatusError("unittest module definition changed during isolated execution")
    if process.timed_out:
        raise LawStatusError(f"isolated unittest module timed out after {timeout:g} seconds")
    if process.returncode != 0:
        raise LawStatusError(
            "isolated unittest module process failed: "
            f"exit={process.returncode}; stderr_sha256={process.stderr_sha256}; stderr={process.stderr[-2000:]}"
        )
    encoded = next(
        (line[len(_UNITTEST_RESULT_SENTINEL) :] for line in reversed(process.stdout.splitlines()) if line.startswith(_UNITTEST_RESULT_SENTINEL)),
        None,
    )
    if encoded is None:
        raise LawStatusError(
            "isolated unittest module emitted no authenticated summary: "
            f"stdout_sha256={process.stdout_sha256}; stderr_sha256={process.stderr_sha256}"
        )
    try:
        payload = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise LawStatusError("isolated unittest module summary is invalid JSON") from exc
    integer_fields = (
        "collected",
        "successful_oracles",
        "failures",
        "errors",
        "expected_failures",
        "unexpected_successes",
    )
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != 2
        or payload.get("module") != module_path.name
        or payload.get("definition_sha256") != definition_before
        or not isinstance(payload.get("ok"), bool)
        or any(not isinstance(payload.get(key), int) or isinstance(payload.get(key), bool) or payload[key] < 0 for key in integer_fields)
        or not isinstance(payload.get("justified_capability_skips"), list)
        or not isinstance(payload.get("tests"), list)
        or len(payload["tests"]) != payload.get("collected")
    ):
        raise LawStatusError("isolated unittest module summary failed schema or definition validation")
    allowed_outcomes = {"PASS", "FAIL", "ERROR", "SKIP", "EXPECTED_FAILURE", "UNEXPECTED_SUCCESS"}
    seen_ids: set[str] = set()
    for record in payload["tests"]:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("id"), str)
            or not record["id"]
            or record["id"] in seen_ids
            or not isinstance(record.get("method"), str)
            or not record["method"]
            or record.get("outcome") not in allowed_outcomes
            or not isinstance(record.get("capability_status"), str)
            or not record["capability_status"]
            or not isinstance(record.get("detail"), str)
            or not record["detail"]
        ):
            raise LawStatusError("isolated unittest exact-result record failed validation")
        seen_ids.add(record["id"])
    payload["process"] = {
        "returncode": process.returncode,
        "stdout_sha256": process.stdout_sha256,
        "stderr_sha256": process.stderr_sha256,
        "stdout_bytes": process.stdout_bytes,
        "stderr_bytes": process.stderr_bytes,
        "duration_seconds": process.duration_seconds,
    }
    return payload


def collect_law(law_id: str, *, definition_digest: str) -> dict:
    if not isinstance(law_id, str) or not _LAW_ID_RE.fullmatch(law_id):
        raise LawStatusError("law id must be canonical and bounded")
    return {
        "schema": 1,
        "id": law_id,
        "definition_digest": _require_digest(definition_digest, "definition digest"),
        "collected": True,
        "started": False,
        "completed": False,
        "outcome": None,
        "oracle_count": 0,
        "capability_status": None,
        "evidence_digest": None,
        "detail": "",
        "justification": None,
        "collected_at": _now(),
        "started_at": None,
        "completed_at": None,
    }


def start_law(record: dict) -> dict:
    if not isinstance(record, dict) or record.get("schema") != 1 or record.get("collected") is not True:
        raise LawStatusError("only a collected law may start")
    if record.get("started") or record.get("completed"):
        raise LawStatusError("law execution may start exactly once")
    updated = copy.deepcopy(record)
    updated["started"] = True
    updated["started_at"] = _now()
    return updated


def complete_law(
    record: dict,
    *,
    outcome: str,
    oracle_count: int,
    evidence_digest: str,
    current_definition_digest: str,
    detail: str,
    capability_status: str,
    justification: str | None = None,
) -> dict:
    if not isinstance(record, dict) or record.get("started") is not True or record.get("completed") is True:
        raise LawStatusError("only a started, incomplete law may complete")
    frozen = _require_digest(record.get("definition_digest"), "frozen definition digest")
    if _require_digest(current_definition_digest, "current definition digest") != frozen:
        raise LawStatusError("law definition changed between collection and completion")
    if outcome not in OUTCOMES:
        raise LawStatusError("unknown law outcome")
    if not isinstance(oracle_count, int) or isinstance(oracle_count, bool) or oracle_count <= 0:
        raise LawStatusError("completed law requires at least one executed oracle")
    if not isinstance(detail, str) or not detail.strip():
        raise LawStatusError("completed law requires execution detail")
    if not isinstance(capability_status, str) or not capability_status.strip():
        raise LawStatusError("completed law requires capability status")
    limitation = outcome in {"UNAVAILABLE", "BLOCKED", "JUSTIFIED_SKIP"}
    if outcome in {"PASSED", "FAILED"} and capability_status != "AVAILABLE":
        raise LawStatusError(f"{outcome} requires AVAILABLE capability")
    if limitation and capability_status == "AVAILABLE":
        raise LawStatusError(f"{outcome} cannot claim AVAILABLE capability")
    if limitation and (not isinstance(justification, str) or not justification.strip()):
        raise LawStatusError(f"{outcome} requires a concrete justification")
    updated = copy.deepcopy(record)
    updated.update(
        completed=True,
        completed_at=_now(),
        outcome=outcome,
        oracle_count=oracle_count,
        evidence_digest=_require_digest(evidence_digest, "evidence digest"),
        capability_status=capability_status.strip(),
        detail=detail.strip(),
        justification=justification.strip() if isinstance(justification, str) else None,
    )
    return updated


def summarize_law_records(records: list[dict]) -> dict:
    if not isinstance(records, list):
        raise LawStatusError("law records must be an array")
    ids = [record.get("id") for record in records if isinstance(record, dict)]
    if len(ids) != len(records) or len(ids) != len(set(ids)):
        raise LawStatusError("law record ids must be present and unique")
    summary = {
        "collected": sum(record.get("collected") is True for record in records),
        "started": sum(record.get("started") is True for record in records),
        "completed": sum(record.get("completed") is True for record in records),
        "passed": sum(record.get("outcome") == "PASSED" for record in records),
        "failed": sum(record.get("outcome") == "FAILED" for record in records),
        "unavailable": sum(record.get("outcome") == "UNAVAILABLE" for record in records),
        "blocked": sum(record.get("outcome") == "BLOCKED" for record in records),
        "justified_skips": sum(record.get("outcome") == "JUSTIFIED_SKIP" for record in records),
    }
    summary["unexecuted"] = summary["collected"] - summary["completed"]
    summary["ok"] = bool(records) and summary["passed"] == len(records) and summary["unexecuted"] == 0
    return summary
