from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401


SHA_A = "a" * 64
SHA_B = "b" * 64


def scope_module():
    return importlib.import_module("agentinfra.scope")


def controls():
    return importlib.import_module("agentinfra.controls")


def compiled_scope(*, user_dirty=(), nested=(), references=()) -> dict:
    return scope_module().compile_write_scope(
        allow=["src/**", "tests/**", "infra/property_tests/**"],
        deny=["vendor/**"],
        test_paths=["tests/**", "infra/property_tests/**"],
        production_paths=["src/**"],
        generated_paths=["generated/**"],
        reference_paths=list(references),
        user_dirty=list(user_dirty),
        nested_repositories=list(nested),
        governance_digest=SHA_A,
    )


class ScopeAndControlsProperties(unittest.TestCase):
    @given(
        spelling=st.sampled_from((
            ".agents/core/policy.md",
            ".AGENTS/core/policy.md",
            ".Agents\\core\\policy.md",
            "AGENTS.md",
            "src/AGENTS.md",
            "src\\agents.md",
        )),
        phase=st.sampled_from(("TEST_DESIGN", "IMPLEMENTATION")),
    )
    def test_governance_is_unconditionally_denied_under_aliases(self, spelling: str, phase: str) -> None:
        self.assertFalse(
            scope_module()._static_scope_allows(
                compiled_scope(),
                spelling,
                phase=phase,
            )
        )

    @given(path=st.sampled_from(("src/main.py", "src/lib/deep.py", "tests/test_main.py", "infra/property_tests/test_x.py")))
    def test_test_design_and_implementation_have_distinct_authority(self, path: str) -> None:
        scope = compiled_scope()
        is_test = path.startswith(("tests/", "infra/property_tests/"))
        self.assertEqual(
            scope_module()._static_scope_allows(scope, path, phase="TEST_DESIGN"),
            is_test,
        )
        self.assertEqual(
            scope_module()._static_scope_allows(scope, path, phase="IMPLEMENTATION"),
            not is_test,
        )

    def test_dynamic_implementation_authority_cannot_be_embedded_in_static_scope(self) -> None:
        scope = compiled_scope()
        self.assertNotIn("baseline_authorized", scope)
        self.assertEqual(
            set(scope),
            scope_module()._STATIC_SCOPE_FIELDS,
        )
        import inspect

        parameters = inspect.signature(scope_module().write_authorized).parameters
        self.assertEqual(set(parameters), {"root", "path", "task_id"})
        tampered = dict(scope)
        tampered["governance_digest"] = SHA_B
        with self.assertRaises(scope_module().ScopeError):
            scope_module().validate_write_scope(tampered)

    @given(boundary=st.sampled_from(("dirty.txt", "nested", "reference")))
    def test_user_dirty_nested_and_reference_boundaries_override_allow(self, boundary: str) -> None:
        values = {
            "dirty.txt": dict(user_dirty=["src/dirty.txt"]),
            "nested": dict(nested=["src/nested"]),
            "reference": dict(references=["src/reference/**"]),
        }[boundary]
        path = {
            "dirty.txt": "src/dirty.txt",
            "nested": "src/nested/file.py",
            "reference": "src/reference/model.py",
        }[boundary]
        self.assertFalse(
            scope_module()._static_scope_allows(
                compiled_scope(**values), path, phase="IMPLEMENTATION"
            )
        )

    @given(
        field=st.sampled_from(("files", "lines_added", "lines_deleted", "generated_churn", "lockfile_churn")),
        limit=st.integers(min_value=0, max_value=20),
    )
    def test_change_budget_is_fail_closed_at_every_dimension(self, field: str, limit: int) -> None:
        budget = {name: 100 for name in ("files", "lines_added", "lines_deleted", "generated_churn", "lockfile_churn")}
        budget[field] = limit
        observed = {name: 0 for name in budget}
        observed[field] = limit + 1
        with self.assertRaises(scope_module().ScopeError):
            scope_module().enforce_change_budget(budget, observed)
        observed[field] = limit
        scope_module().enforce_change_budget(budget, observed)

    @given(
        field=st.sampled_from((
            "public_apis", "public_types", "dependencies", "unsafe_blocks", "threads_executors",
            "global_mutable_state", "feature_flags", "environment_variables", "network_ports",
            "external_services", "persistent_formats", "database_objects", "operational_requirements",
        )),
        count=st.integers(min_value=1, max_value=5),
    )
    def test_semantic_expansion_requires_budget_and_adr(self, field: str, count: int) -> None:
        budget = {field: count - 1}
        with self.assertRaises(scope_module().ScopeError):
            scope_module().enforce_semantic_budget(budget, {field: count})
        decision = {"id": "ADR-1", "category": field, "decision": "bounded choice", "alternatives": ["none"], "reason": "required", "coupling": ["contract"], "evidence": ["E-1"], "risks": ["compatibility"], "compatibility_impact": "none"}
        controls().require_architectural_decisions({field: count}, [decision])
        with self.assertRaises(controls().ControlError):
            controls().require_architectural_decisions({field: count}, [])

    @given(scope=st.sampled_from(("development", "production")))
    def test_dependency_delta_is_explicit_and_classified(self, scope: str) -> None:
        delta = {
            "name": "hypothesis" if scope == "development" else "runtime-lib",
            "scope": scope,
            "direct": True,
            "reason": "property testing" if scope == "development" else "required runtime capability",
            "transitive_impact": ["sortedcontainers"] if scope == "development" else ["runtime-child"],
            "lifecycle_scripts_assessed": True,
            "lockfiles": [],
        }
        with self.assertRaises(controls().ControlError):
            controls().validate_dependency_delta(delta, task_classes=[])
        controls().validate_dependency_delta(delta, task_classes=["DEPENDENCY_CHANGE"])

    @given(
        hostile=st.sampled_from((
            "if os.getenv('PYTEST_CURRENT_TEST'): return expected",
            "if 'test_' in inspect.stack()[1].function: return True",
            "try:\n    validate(value)\nexcept Exception:\n    pass",
            "validation_enabled = False",
            "time.sleep(1)  # force ordering",
        ))
    )
    def test_anti_cheating_tripwires_detect_test_aware_or_disabled_validation(self, hostile: str) -> None:
        self.assertTrue(controls().scan_anti_cheating(hostile))
        self.assertEqual(controls().scan_anti_cheating("def add(a, b):\n    return a + b\n"), [])


if __name__ == "__main__":
    unittest.main()
