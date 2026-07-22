from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from observability.ci.ci_aggregate import aggregate_exit_code, diagnose_ci_needs
from observability.ci.ci_evidence import (
    canonical_digest,
    load_evidence_directory,
    sign_evidence,
    structured_diagnostics,
)
from observability.tests.test_ci_aggregate import CONTEXT, evidence


ROOT = Path(__file__).resolve().parents[3]


class ObservabilityRegressionContracts(unittest.TestCase):
    def _diagnose(self, needs: dict[str, object], bundles: list[dict[str, object]]) -> dict[str, object]:
        return diagnose_ci_needs(run_id=CONTEXT["run_id"], needs=needs, evidence=bundles)

    def test_observability_tooling_meets_required_coverage_threshold(self) -> None:
        tests = ROOT / "distributed-backend" / "observability" / "tests"
        modules = [
            f"observability.tests.{path.stem}"
            for path in sorted(tests.glob("test_*.py"))
            if path.name != "test_regression_contracts.py"
        ]
        with tempfile.TemporaryDirectory() as temporary:
            data_file = Path(temporary) / ".coverage"
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "distributed-backend")
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "run",
                    f"--data-file={data_file}",
                    f"--rcfile={ROOT / 'distributed-backend' / 'observability' / '.coveragerc'}",
                    "-m",
                    "unittest",
                    "-q",
                    *modules,
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            report = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "report",
                    f"--data-file={data_file}",
                    f"--rcfile={ROOT / 'distributed-backend' / 'observability' / '.coveragerc'}",
                    "--fail-under=80",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(report.returncode, 0, report.stdout + report.stderr)

        workflow = (ROOT / ".github" / "workflows" / "verify.yaml").read_text(encoding="utf-8")
        match = re.search(r"observability/.coveragerc --fail-under=(\d+)", workflow)
        self.assertIsNotNone(match, "observability coverage gate is missing")
        self.assertGreaterEqual(int(match.group(1)), 80, "observability branch coverage remains below the required 80% floor")

    def test_observability_tooling_covers_all_diagnostic_branches(self) -> None:
        scenarios = {
            "architecture": ("architecture", "", '{"rule_id":"BOUNDARY","path":"x.go","line":1}\n', ""),
            "terraform": ("terraform init", "terraform", "", "Error: provider unavailable\nregistry.terraform.io/hashicorp/aws"),
            "buf_format": ("buf format", "buf", "diff -u proto/a.proto.orig proto/a.proto\n+++ proto/a.proto\n", ""),
            "govulncheck": ("vulnerability audit", "govulncheck", "GO-2026-0001\nFound in: crypto/tls@go1.26.0\nFixed in: crypto/tls@go1.26.5\n", ""),
            "gui": ("GUI contract", "pnpm", "", "Cannot find module 'vitest'"),
            "e2e": ("E2E", "pytest", 'E2E_SUMMARY={"collected_count":1,"passed_count":1,"duration_seconds":1}\n', ""),
        }
        observed: set[str] = set()
        for expected_type, (step, command, stdout, stderr) in scenarios.items():
            diagnostics = structured_diagnostics(step, command, stdout, stderr, 1)
            observed.update(str(diagnostic.get("type")) for diagnostic in diagnostics)
            self.assertIn(expected_type, observed, f"diagnostic branch {expected_type} produced no structured evidence")
        self.assertEqual(observed, set(scenarios))

    def test_o11y_aggregate_matches_repository_branch_and_exact_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "go.json"
            path.write_text(json.dumps(evidence("go")), encoding="utf-8")
            bundles, errors = load_evidence_directory(Path(temporary), CONTEXT)
        self.assertEqual(errors, [])
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["commit_sha"], CONTEXT["commit_sha"])
        self.assertEqual(bundles[0]["branch_ref"], CONTEXT["branch_ref"])

    def test_o11y_aggregate_rejects_stale_commit_evidence(self) -> None:
        bundle = evidence("go")
        bundle["commit_sha"] = "b" * 40
        bundle["artifact_digest"] = canonical_digest(bundle)
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "go.json").write_text(json.dumps(bundle), encoding="utf-8")
            bundles, errors = load_evidence_directory(Path(temporary), CONTEXT)
        self.assertEqual(bundles, [])
        self.assertTrue(any("commit_sha mismatch" in error for error in errors))

    def test_o11y_aggregate_records_run_id_and_attempt(self) -> None:
        bundle = evidence("go")
        self.assertEqual(bundle["run_id"], CONTEXT["run_id"])
        self.assertEqual(bundle["run_attempt"], CONTEXT["run_attempt"])

    def test_o11y_aggregate_records_workflow_definition_digest(self) -> None:
        bundle = evidence("go")
        digest = bundle.get("workflow_definition_digest")
        self.assertRegex(str(digest or ""), r"^sha256:[0-9a-f]{64}$")

    def test_o11y_aggregate_records_artifact_digests(self) -> None:
        bundle = evidence("go")
        self.assertEqual(bundle["artifact_digest"], canonical_digest(bundle))

    def test_o11y_aggregate_records_generation_timestamp(self) -> None:
        diagnosis = self._diagnose({"go": {"result": "success"}}, [evidence("go")])
        self.assertRegex(str(diagnosis["generated_at"]), r"^\d{4}-\d{2}-\d{2}T")

    def test_o11y_aggregate_rejects_unsigned_or_unverifiable_evidence(self) -> None:
        bundle = evidence("go")
        bundle.pop("signature")
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "go.json").write_text(json.dumps(bundle), encoding="utf-8")
            accepted, errors = load_evidence_directory(Path(temporary), CONTEXT)
        self.assertEqual(accepted, [], "unsigned producer evidence was accepted as authoritative")
        self.assertTrue(any("signature" in error for error in errors))

    def test_o11y_historical_fixtures_are_labeled_non_current(self) -> None:
        bundle = evidence("go")
        bundle["provenance"] = {"historical": True}
        sign_evidence(bundle)
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "historical.json").write_text(json.dumps(bundle), encoding="utf-8")
            accepted, errors = load_evidence_directory(Path(temporary), CONTEXT)
        self.assertEqual(accepted, [])
        self.assertTrue(any("historical" in error for error in errors))

    def test_o11y_report_distinguishes_artifact_completeness_from_execution_completeness(self) -> None:
        diagnosis = self._diagnose({"go": {"result": "success"}}, [evidence("go")])
        self.assertIn("artifact_completeness", diagnosis)
        self.assertIn("execution_completeness", diagnosis)
        self.assertNotEqual(diagnosis["artifact_completeness"], diagnosis["execution_completeness"])

    def test_o11y_report_marks_runtime_correctness_insufficient_when_e2e_did_not_run(self) -> None:
        diagnosis = self._diagnose(
            {"go": {"result": "failure"}, "e2e": {"result": "skipped"}},
            [evidence("go", "failure"), evidence("e2e", "skipped")],
        )
        self.assertEqual(diagnosis["test_execution"]["E2E_TEST"]["status"], "BLOCKED")
        self.assertEqual(diagnosis["product_status"], "UNRESOLVED")

    def test_o11y_report_marks_diagnostic_sufficiency_independently(self) -> None:
        diagnosis = self._diagnose({"go": {"result": "failure"}}, [evidence("go", "failure")])
        self.assertEqual(diagnosis["diagnostic_sufficiency"], "INSUFFICIENT")
        self.assertNotEqual(diagnosis["diagnostic_sufficiency"], diagnosis["analysis_status"])

    def test_o11y_report_marks_release_confidence_independently(self) -> None:
        diagnosis = self._diagnose({"go": {"result": "success"}}, [evidence("go")])
        self.assertIn("release_confidence", diagnosis)
        self.assertNotEqual(diagnosis["release_confidence"], diagnosis["product_status"])

    def test_o11y_report_does_not_claim_complete_evidence_after_build_failure(self) -> None:
        diagnosis = self._diagnose(
            {"go": {"result": "failure"}, "e2e": {"result": "skipped"}},
            [evidence("go", "failure"), evidence("e2e", "skipped")],
        )
        self.assertNotEqual(diagnosis["validation_result"], "passed")
        self.assertNotEqual(diagnosis["product_status"], "PASSED")

    def test_o11y_report_rejects_empty_readiness_measurements_as_complete(self) -> None:
        bundle = evidence("go")
        bundle["commands"].append({
            "step_name": "Readiness",
            "exit_code": 0,
            "diagnostics": [{"type": "readiness", "measurements": []}],
        })
        bundle["artifact_digest"] = canonical_digest(bundle)
        needs = {"go": {"result": "success"}}
        self.assertEqual(aggregate_exit_code(needs, [bundle]), 1)

    def test_o11y_report_rejects_stale_or_missing_source_paths(self) -> None:
        bundle = evidence("go", "failure")
        bundle["commands"][0]["diagnostics"] = [{"type": "go", "source_path": "missing/file.go", "line": 10}]
        bundle["artifact_digest"] = canonical_digest(bundle)
        diagnosis = self._diagnose({"go": {"result": "failure"}}, [bundle])
        self.assertTrue(any("source path" in item for item in diagnosis["missing_evidence"]))

    def test_o11y_report_preserves_timestamped_failure_chronology(self) -> None:
        later = evidence("go", "failure", "2026-07-10T00:02:00+00:00")
        earlier = evidence("python", "failure", "2026-07-10T00:01:00+00:00")
        diagnosis = self._diagnose(
            {"go": {"result": "failure"}, "python": {"result": "failure"}},
            [later, earlier],
        )
        self.assertEqual([event["component"] for event in diagnosis["events"]], ["python", "go"])
        self.assertLess(diagnosis["events"][0]["timestamp"], diagnosis["events"][1]["timestamp"])

    def test_o11y_report_uses_observed_failure_blocked_and_insufficient_evidence_classes(self) -> None:
        diagnosis = self._diagnose(
            {
                "go": {"result": "failure"},
                "e2e": {"result": "skipped"},
                "security": {"result": "success"},
            },
            [evidence("go", "failure")],
        )
        classes = {event["classification"] for event in diagnosis["events"]}
        self.assertIn("OBSERVED_FAILURE", classes)
        self.assertTrue({"SKIPPED_DUE_TO_DEPENDENCY", "BLOCKED"}.intersection(classes))
        self.assertIn("INSUFFICIENT_EVIDENCE", classes)


if __name__ == "__main__":
    unittest.main()
