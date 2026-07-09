from __future__ import annotations

import json
import unittest
from pathlib import Path

from observability.ci.classify_failure import classify_failure
from observability.ci.diagnosis import DATABASE_COLLECTOR_EVENT, KUBERNETES_COLLECTOR_EVENT, command_result_from_dict, diagnose_run, pytest_summary_from_dict


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "diagnosis"


class DiagnosisFixtureTests(unittest.TestCase):
    def test_adversarial_fixture_corpus(self) -> None:
        for path in sorted(FIXTURE_DIR.glob("*.json")):
            with self.subTest(path=path.name):
                fixture = json.loads(path.read_text(encoding="utf-8"))
                command = command_result_from_dict(
                    fixture["command"],
                    stdout=fixture.get("stdout", ""),
                    stderr=fixture.get("stderr", ""),
                )
                diagnosis = diagnose_run(
                    run_id=f"fixture-{fixture['name']}",
                    command=fixture.get("requested_command", "test"),
                    results=[command],
                    pytest_summary=pytest_summary_from_dict(fixture.get("pytest")),
                    missing_evidence=fixture.get("missing_evidence", []),
                )
                expected = fixture["expected"]
                primary = diagnosis["primary_diagnosis"]
                dimensions = primary.get("category_dimensions", {})
                if "stage" in expected:
                    self.assertEqual(dimensions.get("stage"), expected["stage"])
                if "mechanism" in expected:
                    self.assertEqual(dimensions.get("mechanism"), expected["mechanism"])
                if "external_system" in expected:
                    self.assertEqual(dimensions.get("external_system"), expected["external_system"])
                if "product_status" in expected:
                    self.assertEqual(diagnosis["product_status"], expected["product_status"])
                if "harness_status" in expected:
                    self.assertEqual(diagnosis["harness_status"], expected["harness_status"])
                if "validation_result" in expected:
                    self.assertEqual(diagnosis["validation_result"], expected["validation_result"])
                if "false_green_risk" in expected:
                    self.assertIn(expected["false_green_risk"], {item["risk"] for item in diagnosis["false_green_risks"]})
                if "root_event_type" in expected:
                    self.assertEqual(diagnosis["earliest_causal_failure"]["event_type"], expected["root_event_type"])
                if "test_category" in expected:
                    truth = diagnosis["test_execution"][expected["test_category"]]
                    self.assertEqual(truth["status"], expected["test_status"])
                    self.assertNotIn(truth["status"], expected.get("forbidden_test_statuses", []))
                forbidden_text = json.dumps(diagnosis, sort_keys=True)
                for forbidden in expected.get("forbidden", []):
                    self.assertNotEqual(dimensions.get("stage"), forbidden)
                    self.assertNotEqual(dimensions.get("mechanism"), forbidden)
                    self.assertNotEqual(dimensions.get("component"), forbidden)
                    if forbidden in {"docker-networking", "database", "application E2E bug", "Kubernetes", "Docker networking"}:
                        self.assertIn(forbidden.lower(), forbidden_text.lower())

    def test_legacy_classifier_handles_historical_go_mod_eof_without_docker_root_cause(self) -> None:
        fixture = json.loads((FIXTURE_DIR / "go_mod_unexpected_eof.json").read_text(encoding="utf-8"))
        result = classify_failure(logs=fixture["stdout"] + "\n" + fixture["stderr"])

        self.assertEqual(result.failure_family, "dependency-resolution/network-transport")
        self.assertIn("proxy.golang.org", result.suspected_services)
        self.assertIn("docker-networking", result.unsupported_diagnoses or [])

    def test_database_collector_metadata_does_not_classify_unknown_command_failure(self) -> None:
        command = command_result_from_dict({"name": "pytest", "stage": "test", "exit_code": 1}, stderr="process exited 1")

        diagnosis = diagnose_run(
            run_id="collector-db",
            command="test",
            results=[command],
            database={"error": "postgres:5432 connection refused"},
        )
        dimensions = diagnosis["primary_diagnosis"]["category_dimensions"]

        self.assertEqual(dimensions["mechanism"], "UNKNOWN")
        self.assertNotEqual(dimensions["stage"], "DATABASE")
        self.assertIn(DATABASE_COLLECTOR_EVENT, {event.get("event_source") for event in diagnosis["events"]})

    def test_kubernetes_collector_metadata_is_context_not_root_cause(self) -> None:
        command = command_result_from_dict({"name": "pytest", "stage": "test", "exit_code": 1}, stderr="process exited 1")

        diagnosis = diagnose_run(
            run_id="collector-k8s",
            command="test",
            results=[command],
            kubernetes={"error": "pod crashloopbackoff"},
        )
        root = diagnosis["most_supported_root_cause_event"]

        self.assertEqual(diagnosis["primary_diagnosis"]["category_dimensions"]["mechanism"], "UNKNOWN")
        self.assertNotEqual(root.get("event_source"), KUBERNETES_COLLECTOR_EVENT)
        self.assertIn(KUBERNETES_COLLECTOR_EVENT, {event.get("event_source") for event in diagnosis["events"]})

    def test_structured_pytest_assertion_beats_irrelevant_network_log_line(self) -> None:
        command = command_result_from_dict(
            {"name": "pytest", "stage": "test", "exit_code": 1},
            stdout="debug: old network fixture said connection refused\nE   AssertionError: expected 2 got 1",
        )
        pytest_summary = pytest_summary_from_dict(
            {
                "first_failing_test_nodeid": "tests/test_prices.py::test_total",
                "failed_count": 1,
                "failure_message": "AssertionError: expected 2 got 1",
                "assertion_text": "assert 1 == 2",
            }
        )

        diagnosis = diagnose_run(run_id="assertion-mixed", command="test", results=[command], pytest_summary=pytest_summary)
        dimensions = diagnosis["primary_diagnosis"]["category_dimensions"]

        self.assertEqual(dimensions["mechanism"], "APPLICATION_TEST_FAILURE")
        self.assertEqual(diagnosis["product_status"], "FAILED")
        self.assertEqual(diagnosis["most_supported_root_cause_event"]["event_source"], "TEST_EVENT")


if __name__ == "__main__":
    unittest.main()
