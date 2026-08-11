import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentinfra.migration import UpgradePlan
from agentinfra.transaction import Mutation


class TestMigration(unittest.TestCase):
    def test_upgrade_plan_uses_transaction_mutation_path_contract(self):
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "managed"
            plan = UpgradePlan("4.0.0", "4.0.0", (Mutation(destination, b"new"),), (), ())
            self.assertEqual(plan.public()["destinations"], [str(destination)])


if __name__ == "__main__":
    unittest.main()
