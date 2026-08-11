import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentinfra.atomic import AtomicWriteError, atomic_write_bytes
from agentinfra.locks import FileLock, LockError
from agentinfra.transaction import FileTransaction, Mutation, TransactionError, recover_named_transactions, recover_transaction


class TestAtomicWrite(unittest.TestCase):
    def test_atomic_write_accepts_lexical_alias_of_same_resolved_root(self):
        resolved_root = Path.cwd().resolve(strict=True)
        lexical_root = resolved_root
        if os.name == "nt":
            for codepoint in range(ord("A"), ord("Z") + 1):
                drive = Path(f"{chr(codepoint)}:\\")
                try:
                    relative = resolved_root.relative_to(drive.resolve(strict=True))
                    candidate = drive / relative
                    if candidate != resolved_root and candidate.resolve(strict=True) == resolved_root:
                        lexical_root = candidate
                        break
                except (OSError, ValueError):
                    continue
        if lexical_root == resolved_root:
            self.skipTest("host exposes no distinct lexical alias for the current workspace")
        runtime = lexical_root / ".aegis" / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime) as td:
            target = Path(td) / "alias-write"
            try:
                atomic_write_bytes(target, b"complete", root=resolved_root)
            except AtomicWriteError as exc:
                self.fail(f"same-resolved-root lexical alias was rejected as an escape: {exc}")
            self.assertEqual(target.read_bytes(), b"complete")

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are a POSIX host capability")
    def test_atomic_write_preserves_existing_posix_permission_bits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "tool"
            target.write_bytes(b"old")
            target.chmod(0o755)
            atomic_write_bytes(target, b"new", root=root)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_atomic_write_never_exposes_partial_target_contents(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "state.json"
            target.write_bytes(b"before")

            def fail(stage, _path):
                if stage == "before_replace":
                    raise OSError("seeded pre-replace failure")

            with self.assertRaisesRegex(OSError, "seeded"):
                atomic_write_bytes(target, b"after" * 1000, root=root, fault=fail)
            self.assertEqual(target.read_bytes(), b"before")
            self.assertEqual(list(root.glob(".state.json.*.tmp")), [])

    def test_atomic_write_rejects_target_symlink_when_policy_requires_real_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            real = root / "real"
            real.write_bytes(b"user")
            link = root / "link"
            try:
                link.symlink_to(real)
            except (OSError, NotImplementedError):
                self.skipTest("host cannot create test symlink")
            with self.assertRaises(AtomicWriteError):
                atomic_write_bytes(link, b"agent", root=root)
            self.assertEqual(real.read_bytes(), b"user")

    def test_atomic_write_concurrent_writers_never_produce_torn_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "concurrent"
            payloads = [bytes([index]) * 200_000 for index in range(1, 9)]
            barrier = threading.Barrier(len(payloads))
            errors = []

            def writer(payload):
                try:
                    barrier.wait()
                    atomic_write_bytes(target, payload, root=root)
                except BaseException as exc:  # failure is reported by the parent test thread
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(payload,)) for payload in payloads]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertIn(target.read_bytes(), payloads)


class TestFileTransaction(unittest.TestCase):
    def test_transaction_can_require_destination_absence_without_conflating_empty_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / ".aegis" / "state" / "transactions"
            state.mkdir(parents=True)
            target = root / "target"
            target.write_bytes(b"")
            transaction = FileTransaction(
                root,
                [Mutation(target, b"new", expected_exists=False)],
                state_dir=state,
                name="absence-precondition",
            )
            with self.assertRaisesRegex(TransactionError, "existence changed"):
                transaction.commit()
            self.assertEqual(target.read_bytes(), b"")

    def test_transaction_commit_is_all_or_nothing_across_multiple_managed_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".aegis" / "state" / "transactions").mkdir(parents=True)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"first-before")
            second.write_bytes(b"second-before")
            calls = 0

            def fail(stage, _journal):
                nonlocal calls
                if stage == "after_destination":
                    calls += 1
                    if calls == 1:
                        raise OSError("seeded transaction boundary")

            tx = FileTransaction(
                root,
                [Mutation(first, b"first-after"), Mutation(second, b"second-after")],
                state_dir=root / ".aegis" / "state" / "transactions",
                name="unit",
                fault=fail,
            )
            with self.assertRaisesRegex(OSError, "seeded"):
                tx.commit()
            self.assertEqual(first.read_bytes(), b"first-before")
            self.assertEqual(second.read_bytes(), b"second-before")

    def test_transaction_journal_is_durable_before_irreversible_persistent_commit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / ".aegis" / "state" / "transactions"
            state.mkdir(parents=True)
            target = root / "target"
            target.write_bytes(b"before")
            observed = []

            def inspect(stage, journal):
                if stage == "after_journal":
                    journals = list(state.glob("*/journal.json"))
                    observed.append((len(journals), target.read_bytes(), journal["phase"]))

            FileTransaction(
                root,
                [Mutation(target, b"after")],
                state_dir=state,
                name="journal-order",
                fault=inspect,
            ).commit()
            self.assertEqual(observed, [(1, b"before", "PREPARED")])

    def test_transaction_recovery_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / ".aegis" / "state" / "transactions"
            state.mkdir(parents=True)
            target = root / "target"
            target.write_bytes(b"before")
            journal = FileTransaction(
                root,
                [Mutation(target, b"after")],
                state_dir=state,
                name="recover",
            ).commit()
            journal_path = state / journal["id"] / "journal.json"
            first = recover_transaction(journal_path, expected_root=root)
            second = recover_transaction(journal_path, expected_root=root)
            self.assertEqual(first["phase"], "COMMITTED")
            self.assertEqual(second["phase"], "COMMITTED")
            self.assertEqual(target.read_bytes(), b"after")

    def test_transaction_recovery_rejects_rollback_bytes_not_bound_to_before_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / ".aegis" / "state" / "transactions"
            state.mkdir(parents=True)
            target = root / "target"
            target.write_bytes(b"before")
            journal = FileTransaction(root, [Mutation(target, b"after")], state_dir=state, name="corrupt").commit()
            journal_path = state / journal["id"] / "journal.json"
            value = json.loads(journal_path.read_text(encoding="utf-8"))
            value["records"][0]["before_base64"] = "YXR0YWNrZXItYnl0ZXM="
            journal_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(TransactionError, "rollback bytes do not match"):
                recover_transaction(journal_path, expected_root=root, force_rollback=True)
            self.assertEqual(target.read_bytes(), b"after")

    def test_transaction_recovery_preflights_every_destination_before_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / ".aegis" / "state" / "transactions"
            state.mkdir(parents=True)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"first-before")
            second.write_bytes(b"second-before")
            journal = FileTransaction(
                root,
                [Mutation(first, b"first-after"), Mutation(second, b"second-after")],
                state_dir=state,
                name="recovery-preflight",
            ).commit()
            journal_path = state / journal["id"] / "journal.json"
            first.write_bytes(b"ambiguous-external-edit")

            with self.assertRaisesRegex(TransactionError, "ambiguous destination drift"):
                recover_transaction(journal_path, expected_root=root, force_rollback=True)

            self.assertEqual(first.read_bytes(), b"ambiguous-external-edit")
            self.assertEqual(second.read_bytes(), b"second-after")

    def test_named_recovery_preflights_every_journal_before_any_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / ".aegis" / "state" / "transactions"
            state.mkdir(parents=True)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"first-before")
            second.write_bytes(b"second-before")
            first_journal = FileTransaction(
                root,
                [Mutation(first, b"first-after")],
                state_dir=state,
                name="a-install",
            ).commit()
            second_journal = FileTransaction(
                root,
                [Mutation(second, b"second-after")],
                state_dir=state,
                name="b-uninstall",
            ).commit()
            first_path = state / first_journal["id"] / "journal.json"
            second_path = state / second_journal["id"] / "journal.json"
            for path in (first_path, second_path):
                value = json.loads(path.read_text(encoding="utf-8"))
                value["phase"] = "APPLYING"
                path.write_text(json.dumps(value), encoding="utf-8")
            second.write_bytes(b"ambiguous-external-edit")

            with self.assertRaisesRegex(TransactionError, "ambiguous destination drift"):
                recover_named_transactions(
                    state,
                    expected_root=root,
                    names=("a-install", "b-uninstall"),
                )

            self.assertEqual(first.read_bytes(), b"first-after")
            self.assertEqual(second.read_bytes(), b"ambiguous-external-edit")
            self.assertEqual(json.loads(first_path.read_text(encoding="utf-8"))["phase"], "APPLYING")
            self.assertEqual(json.loads(second_path.read_text(encoding="utf-8"))["phase"], "APPLYING")

    def test_transaction_lock_has_unique_owner_nonce_not_pid_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lock"
            first = FileLock(path, "first")
            owner = first.acquire()
            self.assertRegex(owner["nonce"], r"^[0-9a-f]{32}$")
            with self.assertRaises(LockError):
                FileLock(path, "second").release(nonce=owner["nonce"])
            with self.assertRaises(LockError):
                first.release(nonce="0" * 32)
            first.release(nonce=owner["nonce"])
