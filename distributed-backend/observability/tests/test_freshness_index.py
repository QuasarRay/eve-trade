from __future__ import annotations

import multiprocessing
import subprocess
import tempfile
import unittest
from pathlib import Path

from observability.ci.freshness import ANCESTOR, DIRTY_WORKTREE_MISMATCH, DIVERGED, EXACT, SOURCE_CHANGED, UNKNOWN, classify_freshness
from observability.ci.provenance import build_provenance_envelope, collect_run_provenance
from observability.ci.run_index import find_current_exact_record, latest_completed_record, load_index, read_latest_pointer, update_run_index


class FreshnessTests(unittest.TestCase):
    def test_exact_ancestor_diverged_dirty_and_unknown_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_repo(root)
            first = _sha(root)
            first_provenance = _provenance(root, "first")
            self.assertEqual(classify_freshness(first_provenance, root=root).state, EXACT)

            (root / "file.txt").write_text("changed\n", encoding="utf-8")
            self.assertEqual(classify_freshness(first_provenance, root=root).state, DIRTY_WORKTREE_MISMATCH)
            subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=root, check=True)
            second = _sha(root)
            self.assertNotEqual(first, second)
            self.assertEqual(classify_freshness(first_provenance, root=root).state, ANCESTOR)

            subprocess.run(["git", "checkout", "-q", "-b", "side", first], cwd=root, check=True)
            (root / "file.txt").write_text("side\n", encoding="utf-8")
            subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "side"], cwd=root, check=True)
            second_provenance = dict(first_provenance, full_head_sha=second)
            self.assertEqual(classify_freshness(second_provenance, root=root).state, DIVERGED)

        self.assertEqual(classify_freshness({}, root=Path.cwd()).state, UNKNOWN)

    def test_source_changed_run_is_never_exact_even_when_finish_matches_current_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_repo(root)
            start = _provenance(root, "changed-run")
            (root / "file.txt").write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=root, check=True)
            finish = _provenance(root, "changed-run")
            envelope = build_provenance_envelope(
                run_id="changed-run",
                start_provenance=start,
                finish_provenance=finish,
                source_stability="CHANGED",
                run_status="SOURCE_CHANGED_DURING_RUN",
            )

            freshness = classify_freshness(envelope, root=root)

            self.assertEqual(freshness.state, SOURCE_CHANGED)
            self.assertNotEqual(freshness.state, EXACT)

    def test_in_progress_or_unknown_stability_is_not_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_repo(root)
            start = _provenance(root, "in-progress")
            envelope = build_provenance_envelope(
                run_id="in-progress",
                start_provenance=start,
                source_stability="UNKNOWN",
                run_status="IN_PROGRESS",
            )

            freshness = classify_freshness(envelope, root=root)

            self.assertEqual(freshness.state, UNKNOWN)
            self.assertNotEqual(freshness.state, EXACT)


class RunIndexTests(unittest.TestCase):
    def test_latest_completed_is_not_current_exact_when_newer_run_is_historical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".o11y" / "runs" / "old-current").mkdir(parents=True)
            (root / ".o11y" / "runs" / "new-historical").mkdir(parents=True)
            update_run_index(
                root,
                {
                    "run_id": "old-current",
                    "run_status": "COMPLETE",
                    "run_started_at": "2026-01-01T00:00:00+00:00",
                    "full_head_sha": "current",
                    "short_head_sha": "current",
                },
            )
            update_run_index(
                root,
                {
                    "run_id": "new-historical",
                    "run_status": "COMPLETE",
                    "run_started_at": "2026-01-02T00:00:00+00:00",
                    "full_head_sha": "historical",
                    "short_head_sha": "historical",
                },
            )

            latest, errors = latest_completed_record(root)
            exact, exact_errors = find_current_exact_record(root, {"full_head_sha": "current", "worktree_dirty": False})

            self.assertFalse(errors)
            self.assertFalse(exact_errors)
            self.assertEqual(latest["run_id"], "new-historical")
            self.assertEqual(exact["run_id"], "old-current")

    def test_source_changed_and_analysis_failed_runs_are_not_current_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".o11y" / "runs" / "changed").mkdir(parents=True)
            (root / ".o11y" / "runs" / "failed").mkdir(parents=True)
            update_run_index(
                root,
                {
                    "run_id": "changed",
                    "run_status": "SOURCE_CHANGED_DURING_RUN",
                    "run_started_at": "2026-01-01T00:00:00+00:00",
                    "full_head_sha": "current",
                    "short_head_sha": "current",
                    "source_stability": "CHANGED",
                },
            )
            update_run_index(
                root,
                {
                    "run_id": "failed",
                    "run_status": "ANALYSIS_FAILED",
                    "run_started_at": "2026-01-02T00:00:00+00:00",
                    "full_head_sha": "current",
                    "short_head_sha": "current",
                    "source_stability": "UNCHANGED",
                },
            )

            exact, errors = find_current_exact_record(root, {"full_head_sha": "current", "worktree_dirty": False})

            self.assertFalse(errors)
            self.assertIsNone(exact)

    def test_dirty_fingerprint_mismatch_is_not_current_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".o11y" / "runs" / "dirty").mkdir(parents=True)
            update_run_index(
                root,
                {
                    "run_id": "dirty",
                    "run_status": "COMPLETE",
                    "run_started_at": "2026-01-01T00:00:00+00:00",
                    "full_head_sha": "current",
                    "short_head_sha": "current",
                    "worktree_dirty": True,
                    "worktree_diff_fingerprint_if_dirty": "old",
                    "source_stability": "UNCHANGED",
                },
            )

            exact, errors = find_current_exact_record(
                root,
                {
                    "full_head_sha": "current",
                    "worktree_dirty": True,
                    "worktree_diff_fingerprint_if_dirty": "new",
                },
            )

            self.assertFalse(errors)
            self.assertIsNone(exact)

    def test_concurrent_process_writers_do_not_drop_index_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(8):
                (root / ".o11y" / "runs" / f"run-{index}").mkdir(parents=True)
            context = multiprocessing.get_context("spawn")
            processes = [
                context.Process(target=_write_index_record_process, args=(str(root), f"run-{index}", index))
                for index in range(8)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)

            value, errors = load_index(root)

            self.assertFalse(errors)
            self.assertEqual({item["run_id"] for item in value["runs"]}, {f"run-{index}" for index in range(8)})

    def test_stale_absolute_pointer_and_malformed_index_are_not_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pointer = root / ".o11y" / "runs" / "latest-local.txt"
            pointer.parent.mkdir(parents=True)
            pointer.write_text("C:\\repo\\.o11y\\runs\\old\n", encoding="utf-8")
            run_id, pointer_errors = read_latest_pointer(root)
            self.assertEqual(run_id, "")
            self.assertTrue(pointer_errors)

            (root / ".o11y" / "index.json").write_text("{not-json", encoding="utf-8")
            value, index_errors = load_index(root)
            self.assertEqual(value["runs"], [])
            self.assertTrue(index_errors)


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    (root / "file.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)


def _sha(root: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _provenance(root: Path, run_id: str) -> dict[str, object]:
    return collect_run_provenance(root, run_id=run_id, run_started_at="2026-01-01T00:00:00+00:00", run_finished_at="2026-01-01T00:00:01+00:00", status="COMPLETE")


def _write_index_record_process(root_text: str, run_id: str, index: int) -> None:
    update_run_index(
        Path(root_text),
        {
            "run_id": run_id,
            "run_status": "COMPLETE",
            "run_started_at": f"2026-01-01T00:00:{index:02d}+00:00",
            "full_head_sha": "current",
            "short_head_sha": "current",
            "source_stability": "UNCHANGED",
        },
    )


if __name__ == "__main__":
    unittest.main()
