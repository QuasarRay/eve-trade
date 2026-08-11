from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from build_traceability import build, project_root
from scenarios import run_requirement

ROOT = project_root(HERE)


REGISTRY = build(ROOT)


class TestSpecificationLaws(unittest.TestCase):
    """Exact-name adapters to cached adversarial semantic-family batteries."""


def _adapter(requirement: dict):
    def test(self):
        result = run_requirement(ROOT, requirement)
        if result.outcome in {"UNAVAILABLE", "NOT_APPLICABLE"}:
            self.skipTest(f"{result.capability_status}: {result.detail}")
        self.assertEqual(result.outcome, "PASS", result.detail)
        self.assertGreater(result.oracle_count, 0, "a zero-oracle requirement is vacuous")
        self.assertEqual(len(result.evidence_digest), 64)
    test.__name__ = requirement["name"]
    test.__qualname__ = f"TestSpecificationLaws.{requirement['name']}"
    test.__doc__ = f"{requirement['source_file']}:{requirement['source_line']} -> {requirement['scenario']}"
    return test


for _requirement in REGISTRY["requirements"]:
    if _requirement["source_file"][:2] not in {"00", "01", "02"}:
        setattr(TestSpecificationLaws, _requirement["name"], _adapter(_requirement))

del _requirement
