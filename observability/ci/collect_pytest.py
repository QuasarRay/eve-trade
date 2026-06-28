"""Pytest text/JUnit failure extraction and source-link enrichment."""

from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .links import source_url
from .redaction import redact_text
from .run_context import RunContext
from .storage import RunStorage


@dataclass(frozen=True)
class PytestSummary:
    first_failing_test_nodeid: str = ""
    failure_message: str = ""
    assertion_text: str = ""
    source_file: str = ""
    source_line: int | None = None
    source_url: str = ""
    failed_count: int = 0
    passed_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    duration_seconds: float = 0.0
    collected_count: int = 0
    collected_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.update({"test.nodeid": self.first_failing_test_nodeid, "test.file": self.source_file, "test.line": self.source_line})
        return value


def collect_pytest(
    context: RunContext,
    output: str,
    *,
    junit_path: Path | None = None,
    storage: RunStorage | None = None,
) -> PytestSummary:
    storage = storage or RunStorage(context.run_dir)
    safe_output = redact_text(output)
    storage.write_text("pytest/pytest-output.txt", safe_output)
    junit_summary = _parse_junit(junit_path) if junit_path and junit_path.exists() else {}
    text_summary = _parse_text(safe_output)
    merged = {**text_summary, **{key: value for key, value in junit_summary.items() if value not in (None, "")}}
    file = str(merged.get("source_file", ""))
    line = merged.get("source_line")
    summary = PytestSummary(
        first_failing_test_nodeid=str(merged.get("first_failing_test_nodeid", "")),
        failure_message=str(merged.get("failure_message", ""))[:4000],
        assertion_text=str(merged.get("assertion_text", ""))[:4000],
        source_file=file,
        source_line=int(line) if isinstance(line, (int, str)) and str(line).isdigit() else None,
        source_url=source_url(context, file, int(line) if isinstance(line, (int, str)) and str(line).isdigit() else None) if file else "",
        failed_count=int(merged.get("failed_count", 0) or 0),
        passed_count=int(merged.get("passed_count", 0) or 0),
        skipped_count=int(merged.get("skipped_count", 0) or 0),
        error_count=int(merged.get("error_count", 0) or 0),
        duration_seconds=float(merged.get("duration_seconds", 0.0) or 0.0),
        collected_count=int(merged.get("collected_count", 0) or 0),
        collected_tests=[str(item) for item in merged.get("collected_tests", [])],
    )
    if junit_path and junit_path.exists():
        storage.copy(junit_path, "pytest/pytest-junit.xml")
    storage.write_json("pytest/pytest-summary.json", summary.to_dict())
    return summary


def _parse_junit(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    result: dict[str, Any] = {
        "failed_count": sum(int(suite.attrib.get("failures", 0)) for suite in suites),
        "error_count": sum(int(suite.attrib.get("errors", 0)) for suite in suites),
        "skipped_count": sum(int(suite.attrib.get("skipped", 0)) for suite in suites),
        "duration_seconds": sum(float(suite.attrib.get("time", 0)) for suite in suites),
        "collected_count": sum(int(suite.attrib.get("tests", 0)) for suite in suites),
        "collected_tests": [],
    }
    result["passed_count"] = max(0, result["collected_count"] - result["failed_count"] - result["error_count"] - result["skipped_count"])
    for case in root.iter("testcase"):
        case_file = case.attrib.get("file", "") or _classname_to_file(case.attrib.get("classname", ""))
        case_name = case.attrib.get("name", "")
        case_classname = case.attrib.get("classname", "")
        nodeid = f"{case_file}::{case_name}" if case_file else f"{case_classname}::{case_name}".strip(":")
        if nodeid:
            result["collected_tests"].append(nodeid)
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        if failure is None:
            continue
        result.update(
            {
                "first_failing_test_nodeid": nodeid,
                "failure_message": failure.attrib.get("message", "") or _first_line(failure.text or ""),
                "assertion_text": failure.text or "",
                "source_file": case_file,
                "source_line": case.attrib.get("line"),
            }
        )
        break
    return result


def _first_line(value: str) -> str:
    lines = value.splitlines()
    return lines[0] if lines else ""


def _classname_to_file(classname: str) -> str:
    if not classname:
        return ""
    return classname.replace(".", "/") + ".py"


def _parse_text(output: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    failure = re.search(r"(?m)^FAILED\s+([^\s]+)(?:\s+-\s+(.+))?$", output)
    if failure:
        result["first_failing_test_nodeid"] = failure.group(1)
        result["failure_message"] = failure.group(2) or ""
        file_part = failure.group(1).split("::", 1)[0]
        result["source_file"] = file_part
    location = re.search(r"(?m)^([^\s:]+\.py):(\d+):\s+(?:AssertionError|Error|FAILED)", output)
    if location:
        result["source_file"] = location.group(1)
        result["source_line"] = int(location.group(2))
    assertion = re.search(r"(?ms)(E\s+assert .+?)(?=\n\n|\n_{3,}|\Z)", output)
    if assertion:
        result["assertion_text"] = assertion.group(1)
    summary = re.search(r"(?m)=+\s*(.*?)\s+in\s+([0-9.]+)s\s*=+", output)
    if summary:
        for count, kind in re.findall(r"(\d+)\s+(passed|failed|skipped|error|errors)", summary.group(1)):
            result[{"passed": "passed_count", "failed": "failed_count", "skipped": "skipped_count", "error": "error_count", "errors": "error_count"}[kind]] = int(count)
        result["duration_seconds"] = float(summary.group(2))
    collected = re.search(r"collected\s+(\d+)\s+items?", output)
    if collected:
        result["collected_count"] = int(collected.group(1))
    result["collected_tests"] = list(
        dict.fromkeys(
            match.group("nodeid")
            for match in re.finditer(
                r"(?m)^(?P<nodeid>\S+\.py(?:::\S+)+)\s+(?:PASSED|FAILED|SKIPPED|ERROR)\b",
                output,
            )
        )
    )
    return result
