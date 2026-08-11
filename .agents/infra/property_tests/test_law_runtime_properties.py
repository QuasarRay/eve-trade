from __future__ import annotations

import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401
from agentinfra.law_runtime import (
    LawStatusError,
    collect_law,
    complete_law,
    resolve_law_layout,
    run_unittest_module_isolated,
    start_law,
    summarize_law_records,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


class LawRuntimeProperties(unittest.TestCase):
    def test_isolated_result_reports_each_exact_test_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            infra = Path(directory) / "infra"
            tests = infra / "tests"
            tests.mkdir(parents=True)
            module = tests / "test_mixed.py"
            module.write_text(
                "import unittest\n"
                "class Mixed(unittest.TestCase):\n"
                "    def test_pass(self): self.assertTrue(True)\n"
                "    @unittest.skip('host capability unavailable')\n"
                "    def test_unavailable(self): self.fail('must not execute')\n",
                encoding="utf-8",
            )

            result = run_unittest_module_isolated(infra, module, category="unit")

            self.assertEqual(result["schema"], 2)
            exact = {item["method"]: item for item in result["tests"]}
            self.assertEqual(set(exact), {"test_pass", "test_unavailable"})
            self.assertEqual(exact["test_pass"]["outcome"], "PASS")
            self.assertEqual(exact["test_pass"]["capability_status"], "AVAILABLE")
            self.assertEqual(exact["test_unavailable"]["outcome"], "SKIP")
            self.assertNotEqual(exact["test_unavailable"]["capability_status"], "AVAILABLE")
            self.assertIn("host capability unavailable", exact["test_unavailable"]["detail"])

    def test_assurance_module_executes_outside_parent_import_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source" / "infra" / "tests"
            deployed = root / "deployed" / "infra" / "tests"
            source.mkdir(parents=True)
            deployed.mkdir(parents=True)
            source_test = source / "test_collision.py"
            deployed_test = deployed / "test_collision.py"
            source_test.write_text(
                "import unittest\n"
                "class Collision(unittest.TestCase):\n"
                "    def test_source_only(self): self.fail('parent cache reused')\n",
                encoding="utf-8",
            )
            deployed_test.write_text(
                "import unittest\n"
                "class Collision(unittest.TestCase):\n"
                "    def test_deployed(self): self.assertEqual('deployed', 'deployed')\n",
                encoding="utf-8",
            )
            spec = importlib.util.spec_from_file_location("test_collision", source_test)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            sys.modules["test_collision"] = module
            try:
                spec.loader.exec_module(module)
                result = run_unittest_module_isolated(
                    deployed.parent,
                    deployed_test,
                    category="unit",
                )
            finally:
                sys.modules.pop("test_collision", None)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["collected"], 1)
            self.assertEqual(result["successful_oracles"], 1)
            self.assertEqual(result["module"], "test_collision.py")

    def test_source_layout_wins_and_results_are_outside_governance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("infra/tests", "infra/laws", "infra/law_tests", "tests-to-impl"):
                (root / relative).mkdir(parents=True)
            (root / ".agents" / "infra" / "tests").mkdir(parents=True)
            layout = resolve_law_layout(root)
            self.assertEqual(layout.mode, "source")
            self.assertEqual(layout.unit_tests, root / "infra" / "tests")
            self.assertEqual(layout.law_definitions, root / "infra" / "laws")
            self.assertEqual(layout.law_tests, root / "infra" / "law_tests")
            self.assertEqual(layout.specification, root / "tests-to-impl")
            self.assertEqual(layout.results, root / ".aegis" / "law-results")
            self.assertFalse(layout.results.is_relative_to(root / ".agents"))

    def test_clean_deployment_layout_is_read_only_and_results_use_aegis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                ".agents/infra/tests",
                ".agents/infra/laws",
                ".agents/infra/law_tests",
                ".agents/tests-to-impl",
            ):
                (root / relative).mkdir(parents=True)
            layout = resolve_law_layout(root)
            self.assertEqual(layout.mode, "deployment")
            self.assertTrue(layout.unit_tests.is_relative_to(root / ".agents"))
            self.assertEqual(layout.results, root / ".aegis" / "law-results")

    @given(
        outcome=st.sampled_from(("PASSED", "FAILED", "UNAVAILABLE", "BLOCKED", "JUSTIFIED_SKIP")),
        oracle_count=st.integers(min_value=0, max_value=3),
    )
    def test_completion_is_truthful_and_definition_bound(self, outcome: str, oracle_count: int) -> None:
        record = start_law(collect_law("law.one", definition_digest=SHA_A))
        kwargs = {
            "outcome": outcome,
            "oracle_count": oracle_count,
            "evidence_digest": SHA_B,
            "current_definition_digest": SHA_A,
            "detail": "executed observation",
            "capability_status": "AVAILABLE" if outcome in {"PASSED", "FAILED"} else "UNOBSERVABLE",
            "justification": "host boundary" if outcome in {"UNAVAILABLE", "BLOCKED", "JUSTIFIED_SKIP"} else None,
        }
        valid = oracle_count > 0
        if valid:
            completed = complete_law(record, **kwargs)
            self.assertTrue(completed["collected"])
            self.assertTrue(completed["started"])
            self.assertTrue(completed["completed"])
            self.assertEqual(completed["outcome"], outcome)
        else:
            with self.assertRaises(LawStatusError):
                complete_law(record, **kwargs)

    def test_unavailable_blocked_or_unstarted_is_never_pass(self) -> None:
        collected = collect_law("law.collected", definition_digest=SHA_A)
        unavailable = complete_law(
            start_law(collect_law("law.unavailable", definition_digest=SHA_A)),
            outcome="UNAVAILABLE",
            oracle_count=1,
            evidence_digest=SHA_B,
            current_definition_digest=SHA_A,
            detail="capability absent",
            capability_status="MISSING",
            justification="tool not installed",
        )
        summary = summarize_law_records([collected, unavailable])
        self.assertFalse(summary["ok"])
        self.assertEqual(summary["collected"], 2)
        self.assertEqual(summary["started"], 1)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["passed"], 0)
        self.assertEqual(summary["unavailable"], 1)
        self.assertEqual(summary["unexecuted"], 1)

    def test_definition_change_invalidates_completion(self) -> None:
        record = start_law(collect_law("law.changed", definition_digest=SHA_A))
        with self.assertRaisesRegex(LawStatusError, "definition"):
            complete_law(
                record,
                outcome="PASSED",
                oracle_count=1,
                evidence_digest=SHA_B,
                current_definition_digest=SHA_B,
                detail="would otherwise pass",
                capability_status="AVAILABLE",
            )


if __name__ == "__main__":
    unittest.main()
