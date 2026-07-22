from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "distributed-backend" / "src" / "trade-settlement" / "migrations"


def git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(arguments)} failed: {result.stderr}")
    return result.stdout.strip()


def historical_schema_commits() -> list[str]:
    path = "distributed-backend/src/trade-settlement/migrations/0002_merge_item_stack_constraints.sql"
    commits = git("log", "--all", "--diff-filter=A", "--format=%H", "--", path).splitlines()
    if not commits:
        raise AssertionError("repository history exposes no supported pre-collapse migration baseline")
    return commits


def migration_files_at(commit: str) -> list[str]:
    directory = "distributed-backend/src/trade-settlement/migrations"
    names = git("ls-tree", "--name-only", commit, directory).splitlines()
    if names == [directory]:
        names = git("ls-tree", "-r", "--name-only", commit, directory).splitlines()
    return sorted(name for name in names if name.endswith(".sql"))


def migration_at(commit: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git show {commit}:{path} failed: {result.stderr}")
    return result.stdout


class DatabaseFixture:
    sequence = 0

    def __init__(self) -> None:
        database_url = os.environ.get("EVE_TRADE_TEST_DATABASE_URL", "").strip()
        if not database_url:
            raise AssertionError("EVE_TRADE_TEST_DATABASE_URL is required for migration regression tests")
        type(self).sequence += 1
        self.schema = f"eve_trade_migration_test_{os.getpid()}_{type(self).sequence}"
        self.preapplied_migrations: set[str] = set()
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))

    def close(self) -> None:
        if not self.schema.startswith("eve_trade_migration_test_"):
            raise AssertionError(f"refusing to drop unexpected schema {self.schema!r}")
        self.connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.close()

    def apply_historical(self, commit: str) -> None:
        for path in migration_files_at(commit):
            self.connection.execute(migration_at(commit, path))
            self.preapplied_migrations.add(Path(path).name)

    def seed_representative_data(self) -> None:
        self.connection.execute(
            """
            INSERT INTO capsuleer (capsuleer_id, capsuleer_name) VALUES (1001, 'Seller');
            INSERT INTO region (region_id, region_name) VALUES (10000002, 'The Forge');
            INSERT INTO station (station_id, region_id, station_name) VALUES (60003760, 10000002, 'Jita');
            INSERT INTO item_type (item_type_id, item_type_name, category_name, group_name)
            VALUES (34, 'Tritanium', 'Material', 'Mineral');
            INSERT INTO idempotency_record (
                idempotency_key, request_fingerprint, request_kind, idempotency_state, created_by_service
            ) VALUES ('upgrade-request', 'sha256:upgrade', 'ISSUE', 'IN_PROGRESS', 'market');
            INSERT INTO request_attempt (
                request_id, idempotency_key, attempt_number, received_by_service, attempt_state
            ) VALUES ('10000000-0000-4000-8000-000000000001', 'upgrade-request', 1, 'trade-settlement', 'IN_PROGRESS');
            INSERT INTO settlement_batch (
                settlement_batch_id, request_id, idempotency_key, caused_by_capsuleer_id, batch_state, created_by_service
            ) VALUES (
                '10000000-0000-4000-8000-000000000002',
                '10000000-0000-4000-8000-000000000001',
                'upgrade-request', 1001, 'IN_PROGRESS', 'trade-settlement'
            );
            INSERT INTO settlement_step (
                settlement_step_id, settlement_batch_id, step_index, step_kind, step_payload, step_payload_hash, step_state
            ) VALUES (
                '10000000-0000-4000-8000-000000000003',
                '10000000-0000-4000-8000-000000000002',
                0, 'create_trade', '{}', 'sha256:step', 'COMPLETED'
            );
            INSERT INTO wallet (
                wallet_id, capsuleer_id, wallet_kind, isk_amount, wallet_state, wallet_version, wallet_checksum, checksum_algorithm
            ) VALUES (
                '10000000-0000-4000-8000-000000000004', 1001, 'PRIMARY', 1000, 'ACTIVE', 1, 'wallet-checksum', 'sha256-v1'
            );
            INSERT INTO item_stack (
                item_stack_id, owner_id, item_type_id, station_id, quantity, stack_state,
                stack_version, stack_checksum, checksum_algorithm
            ) VALUES (
                '10000000-0000-4000-8000-000000000005', 1001, 34, 60003760, 5, 'LOCKED',
                1, 'stack-checksum', 'sha256-v1'
            );
            INSERT INTO trade_instance (
                trade_instance_id, created_settlement_step_id, trade_kind, trade_state, issuer_id,
                item_type_id, station_id, total_quantity, remaining_quantity, unit_price_isk
            ) VALUES (
                '10000000-0000-4000-8000-000000000006',
                '10000000-0000-4000-8000-000000000003',
                'SELL', 'OPEN', 1001, 34, 60003760, 5, 5, 10
            );
            INSERT INTO item_stack_escrow (
                item_stack_escrow_id, trade_instance_id, owner_id, source_item_stack_id,
                item_type_id, station_id, quantity, created_settlement_step_id
            ) VALUES (
                '10000000-0000-4000-8000-000000000007',
                '10000000-0000-4000-8000-000000000006',
                1001, '10000000-0000-4000-8000-000000000005',
                34, 60003760, 5, '10000000-0000-4000-8000-000000000003'
            );
            INSERT INTO wallet_escrow (
                wallet_escrow_id, trade_instance_id, owner_id, source_wallet_id,
                isk_amount, created_settlement_step_id
            ) VALUES (
                '10000000-0000-4000-8000-000000000008',
                '10000000-0000-4000-8000-000000000006',
                1001, '10000000-0000-4000-8000-000000000004',
                0, '10000000-0000-4000-8000-000000000003'
            );
            INSERT INTO item_stack_ledger (
                settlement_step_id, item_stack_id, item_type_id, owner_id, station_id, entry_kind,
                quantity_delta, quantity_before, quantity_after, stack_version_before, stack_version_after,
                stack_checksum_before, stack_checksum_after
            ) VALUES (
                '10000000-0000-4000-8000-000000000003',
                '10000000-0000-4000-8000-000000000005', 34, 1001, 60003760, 'TRANSFER_TO_ESCROW',
                0, 5, 5, 0, 1, 'GENESIS', 'stack-checksum'
            );
            """
        )

    def apply_current(self) -> None:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in self.preapplied_migrations:
                continue
            self.connection.execute(path.read_text(encoding="utf-8"))


class MigrationRegressionContracts(unittest.TestCase):
    def fixture(self, commit: str, *, seed: bool = True) -> Iterator[DatabaseFixture]:
        fixture = DatabaseFixture()
        try:
            fixture.apply_historical(commit)
            if seed:
                fixture.seed_representative_data()
            yield fixture
        finally:
            fixture.close()

    def test_database_migrations_upgrade_from_every_supported_deployed_schema(self) -> None:
        commits = historical_schema_commits()
        self.assertGreater(len(commits), 0)
        for commit in commits:
            with self.subTest(commit=commit):
                for fixture in self.fixture(commit, seed=False):
                    fixture.apply_current()
                    fixture.apply_current()

    def test_database_migrations_are_forward_only(self) -> None:
        current = {path.name: path.read_text(encoding="utf-8") for path in MIGRATIONS.glob("*.sql")}
        self.assertTrue(current)
        for commit in historical_schema_commits():
            with self.subTest(commit=commit):
                for historical_path in migration_files_at(commit):
                    name = Path(historical_path).name
                    self.assertIn(name, current, f"historical migration {name} was deleted or squashed")
                    self.assertEqual(current[name], migration_at(commit, historical_path), f"historical migration {name} was rewritten")

    def test_database_migration_upgrade_preserves_existing_settlement_data(self) -> None:
        for fixture in self.fixture(historical_schema_commits()[0]):
            before = fixture.connection.execute(
                "SELECT idempotency_key, request_fingerprint FROM idempotency_record ORDER BY idempotency_key"
            ).fetchall()
            fixture.apply_current()
            after = fixture.connection.execute(
                "SELECT idempotency_key, request_fingerprint FROM idempotency_record ORDER BY idempotency_key"
            ).fetchall()
            self.assertEqual(after, before)

    def test_database_migration_upgrade_preserves_ledger_invariants(self) -> None:
        for fixture in self.fixture(historical_schema_commits()[0]):
            fixture.apply_current()
            fixture.connection.execute(
                "SELECT check_item_stack_ledger_projection_invariant('10000000-0000-4000-8000-000000000005')"
            )
            with self.assertRaises(psycopg.Error):
                fixture.connection.execute("UPDATE item_stack_ledger SET quantity_after = 99")

    def test_database_migration_upgrade_preserves_escrow_invariants(self) -> None:
        for fixture in self.fixture(historical_schema_commits()[0]):
            fixture.apply_current()
            fixture.connection.execute(
                "SELECT check_trade_remaining_quantity_invariant('10000000-0000-4000-8000-000000000006')"
            )
            with self.assertRaises(psycopg.Error):
                fixture.connection.execute(
                    "UPDATE trade_instance SET remaining_quantity=4 WHERE trade_instance_id='10000000-0000-4000-8000-000000000006'"
                )

    def test_database_migration_upgrade_preserves_projection_invariants(self) -> None:
        for fixture in self.fixture(historical_schema_commits()[0]):
            fixture.apply_current()
            fixture.connection.execute(
                "SELECT check_item_stack_ledger_projection_invariant('10000000-0000-4000-8000-000000000005')"
            )
            with self.assertRaises(psycopg.Error):
                fixture.connection.execute(
                    "UPDATE item_stack SET stack_checksum='corrupt' WHERE item_stack_id='10000000-0000-4000-8000-000000000005'"
                )


if __name__ == "__main__":
    unittest.main()
