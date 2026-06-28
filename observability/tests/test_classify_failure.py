from __future__ import annotations

import unittest

from observability.ci.classify_failure import classify_failure


class ClassificationTests(unittest.TestCase):
    def test_zero_quantity_acceptance_maps_to_validation_family(self) -> None:
        result = classify_failure(
            nodeid="distributed-backend/tests/e2e/test_trade_lifecycle.py::test_accepting_trade_rejects_zero_quantity",
            assertion="Expected rejection but quantity_requested=0 was accepted",
        )

        self.assertEqual(result.failure_family, "accept-validation")
        self.assertIn("market", result.suspected_services)
        self.assertGreaterEqual(result.confidence, 0.6)
        self.assertTrue(result.likely_solution_files)


if __name__ == "__main__":
    unittest.main()

