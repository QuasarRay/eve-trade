from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from observability.ci.compare_runs import _hint, _nested, _read, compare_runs
from observability.ci.generate_failure_report import (
    _load_json_if_exists,
    _load_text_if_exists,
    generate_run_report,
)
from observability.ci.run_context import RunContext


def write_json(root: Path, relative: str, value: object) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ParityCoverageTests(unittest.TestCase):
    def test_compare_runs_reports_tool_schema_environment_service_and_sequence_differences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / "local"
            ci = root / "ci"
            output = root / "output"
            for directory, sha, dirty, python, schema, env, sequence in (
                (local, "local-sha", True, "3.14", "local-schema", {"TOKEN": "<redacted:present>"}, ["build", "test"]),
                (ci, "ci-sha", False, "3.13", "ci-schema", {}, ["build"]),
            ):
                write_json(directory, "git.json", {"sha": sha, "dirty": dirty, "branch": "experimental"})
                write_json(directory, "tool-versions.json", {"python": python, "go": "go1.26.5", "rustc": "rustc", "encore": "encore", "os": "windows" if directory == local else "linux"})
                write_json(
                    directory,
                    "hashes.json",
                    {
                        "encore.config_hash": sha,
                        "kubernetes.manifest_hash": sha,
                        "db.migration_hash": sha,
                        "protobuf.generated_hash": sha,
                        "migrations": {"files": [f"{sha}.sql"]},
                    },
                )
                write_json(directory, "db/metadata.json", {"db.schema_hash": schema})
                write_json(directory, "pytest/pytest-summary.json", {"collected_count": 2, "first_failing_test_nodeid": sha, "collected_tests": [sha]})
                write_json(directory, "env-redacted.json", env)
                write_json(
                    directory,
                    "run-summary.json",
                    {
                        "service_urls": {"gateway": f"https://{sha}.test"},
                        "service_readiness_ms": {"gateway": 10 if directory == local else 20},
                        "command_sequence": sequence,
                    },
                )

            outputs = compare_runs(local, ci, output)
            summary = json.loads(outputs["json"].read_text(encoding="utf-8"))
            fields = {item["field"] for item in summary["differences"]}
            self.assertIn("git.sha", fields)
            self.assertIn("env.TOKEN.present", fields)
            self.assertIn("service.url.gateway", fields)
            self.assertIn("pipeline.command_sequence", fields)
            self.assertIn("Local vs CI parity diff", outputs["markdown"].read_text(encoding="utf-8"))
            self.assertIn("<table>", outputs["html"].read_text(encoding="utf-8"))

    def test_compare_helpers_cover_invalid_files_nested_values_and_all_hint_families(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.json").write_text("not-json", encoding="utf-8")
            (root / "list.json").write_text("[]", encoding="utf-8")
            self.assertEqual(_read(root / "missing.json"), {})
            self.assertEqual(_read(root / "bad.json"), {})
            self.assertEqual(_read(root / "list.json"), {})
        self.assertEqual(_nested({"a": {"b": 1}}, "a", "b"), 1)
        self.assertIsNone(_nested({"a": "not-a-map"}, "a", "b"))
        for field in (
            "git.dirty",
            "db.schema_hash",
            "db.migration_hash",
            "protobuf.generated_hash",
            "encore.version",
            "kubernetes.manifest_hash",
            "python.version",
            "os.name",
            "pytest.collected",
            "other.field",
        ):
            self.assertTrue(_hint(field, True, False))

    def test_compare_identical_empty_runs_emits_no_difference_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local = root / "local"
            ci = root / "ci"
            local.mkdir()
            ci.mkdir()
            outputs = compare_runs(local, ci)
            self.assertIn("No compared differences", outputs["markdown"].read_text(encoding="utf-8"))


class RunReportCoverageTests(unittest.TestCase):
    def test_run_report_renders_complete_diagnosis_event_graph_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "commands").mkdir()
            (run_dir / "commands/build.log").write_text("failed", encoding="utf-8")
            context = RunContext(
                "run-1",
                run_dir,
                root,
                "2026-01-01T00:00:00+00:00",
                "test",
                full_head_sha="a" * 40,
                short_head_sha="a" * 8,
                branch="experimental",
                status="COMPLETE",
            )
            provenance = {
                "schema_version": "o11y.run-provenance.v2",
                "run_id": "run-1",
                "source_stability": "STABLE",
                "run_status": "COMPLETE",
                "start_provenance": {
                    "full_head_sha": "a" * 40,
                    "short_head_sha": "a" * 8,
                    "branch": "experimental",
                    "worktree_dirty": False,
                },
                "finish_provenance": {
                    "full_head_sha": "a" * 40,
                    "worktree_dirty": False,
                },
            }
            diagnosis = {
                "run_id": "run-1",
                "requested_command": "test",
                "validation_result": "failed",
                "harness_status": "OK",
                "product_status": "FAILED",
                "analysis_status": "OK",
                "commands": [{"stage": "build", "name": "compile", "exit_code": 1, "log_path": "commands/build.log"}],
                "test_execution": {"UNIT_TEST": {"status": "FAILED", "tests_collected": 2, "tests_started": 2, "tests_passed": 1, "tests_failed": 1, "tests_skipped": 0}},
                "earliest_causal_failure": {"event_id": "E1", "stage": "build", "command_id": "compile", "component": "go", "message": "compile failed", "evidence_reference": "commands/build.log", "event_source": "COMMAND", "relation": "OBSERVED_FAILURE"},
                "most_supported_root_cause_event": {"event_id": "E1", "event_source": "COMMAND", "relation": "LIKELY_CAUSE", "message": "compiler diagnostic"},
                "primary_diagnosis": {
                    "summary": "compiler diagnostic observed",
                    "category_dimensions": {"stage": "build", "mechanism": "COMPILE_ERROR", "component": "go", "external_system": ""},
                    "confidence_band": "HIGH",
                    "confidence_score": 0.8,
                    "supporting_evidence": ["undefined symbol"],
                    "contradicting_evidence": ["none"],
                    "missing_evidence": ["race trace"],
                    "unsupported_diagnoses": ["network failure"],
                },
                "events": [{"event_id": "E1", "relation": "LIKELY_CAUSE", "message": "compile failed", "evidence_reference": "commands/build.log"}],
                "recommendations": [{"action": "Fix symbol", "rationale": "compiler output", "would_confirm_or_reject": "compile passes"}],
            }
            outputs = generate_run_report(context, diagnosis, provenance=provenance)
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            self.assertIn("Most Supported Root Cause Event", markdown)
            self.assertIn("Fix symbol", markdown)
            self.assertIn("commands/build.log", markdown)
            self.assertIn("Eve Trade observed run report", outputs["html"].read_text(encoding="utf-8"))

    def test_report_load_helpers_handle_missing_invalid_and_text_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(_load_json_if_exists(root / "missing.json"), {})
            invalid = root / "invalid.json"
            invalid.write_text("{", encoding="utf-8")
            self.assertEqual(_load_json_if_exists(invalid), {})
            text = root / "log.txt"
            text.write_text("hello", encoding="utf-8")
            self.assertEqual(_load_text_if_exists(text), "hello")
            self.assertEqual(_load_text_if_exists(root / "missing.txt"), "")


if __name__ == "__main__":
    unittest.main()
