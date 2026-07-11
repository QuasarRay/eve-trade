from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_architecture_boundaries import check_source_boundaries


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_detects_market_database_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gametrade").mkdir()
            market = root / "distributed-backend" / "src" / "market"
            gateway = root / "distributed-backend" / "src" / "gateway"
            market.mkdir(parents=True)
            gateway.mkdir(parents=True)
            (market / "bad.go").write_text('package market\nconst q = "UPDATE wallet SET x = 1"\n', encoding="utf-8")
            self.assertTrue(check_source_boundaries(root))

    def test_detects_game_domain_infrastructure_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "gametrade"
            market = root / "distributed-backend" / "src" / "market"
            gateway = root / "distributed-backend" / "src" / "gateway"
            game.mkdir(parents=True)
            market.mkdir(parents=True)
            gateway.mkdir(parents=True)
            (game / "bad.go").write_text('package gametrade\nimport "github.com/jackc/pgx/v5"\n', encoding="utf-8")
            self.assertTrue(check_source_boundaries(root))


if __name__ == "__main__":
    unittest.main()
