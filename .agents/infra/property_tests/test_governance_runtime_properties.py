from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from hypothesis import given, strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from .hypothesis_profiles import settings  # noqa: F401  # profile side effect
from agentinfra.atomic import AtomicWriteError, atomic_write_bytes
from agentinfra.governance import GovernanceViolation, assert_mutation_allowed
from agentinfra.paths import install_state_dir, persistent_dir, runtime_dir, tasks_dir
from agentinfra.state_store import StateStore
from agentinfra.transaction import FileTransaction, Mutation, TransactionError


SAFE_TEXT = st.text(
    alphabet=st.characters(categories=("Ll", "Lu", "Nd"), include_characters=" -_é雪"),
    min_size=1,
    max_size=40,
).filter(str.strip)
SAFE_LEAF = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=1,
    max_size=16,
)
MUTATION_OPERATION = st.sampled_from((
    "direct write",
    "relative write",
    "delete",
    "rename source",
    "rename destination",
    "code generation",
    "formatter rewrite",
    "git mv",
    "child-agent write",
    "replace parent",
))


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_dir():
            body = b"D\0"
        elif path.is_file():
            body = b"F\0" + path.read_bytes()
        else:
            body = b"O\0"
        digest.update(relative + b"\0" + body)
    return digest.hexdigest()


def _fixture_root(directory: str) -> Path:
    root = Path(directory)
    agents = root / ".agents"
    agents.mkdir()
    (agents / "framework.toml").write_text("[framework]\nversion='4.0.0'\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("immutable instructions\n", encoding="utf-8")
    return root


class GovernanceRuntimeProperties(unittest.TestCase):
    @given(operation=MUTATION_OPERATION, leaf=SAFE_LEAF)
    def test_governance_guard_rejects_every_managed_mutation_vector(self, operation: str, leaf: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = _fixture_root(directory)
            targets = (
                Path(".agents") / "core" / leaf,
                root / ".agents" / "core" / leaf,
                Path("nested") / "AGENTS.md",
            )
            for target in targets:
                with self.assertRaises(GovernanceViolation):
                    assert_mutation_allowed(root, target, operation=operation)

    @given(title=SAFE_TEXT)
    def test_state_store_writes_runtime_only_under_dot_aegis(self, title: str) -> None:
        """AEGIS-I001: ordinary task creation cannot mutate deployed governance."""

        with tempfile.TemporaryDirectory(prefix="aegis property ") as directory:
            root = _fixture_root(directory)
            before = _tree_digest(root / ".agents")
            task = StateStore(root).create(title)
            self.assertEqual(_tree_digest(root / ".agents"), before)
            self.assertTrue((root / ".aegis" / "tasks" / task["id"] / "state.json").is_file())
            self.assertFalse((root / ".agents" / "runtime").exists())
            self.assertFalse((root / ".agents" / "persistent").exists())

    @given(leaf=SAFE_LEAF)
    def test_transaction_rejects_every_agents_destination(self, leaf: str) -> None:
        """AEGIS-I001: the shared transaction path denies .agents destinations."""

        with tempfile.TemporaryDirectory() as directory:
            root = _fixture_root(directory)
            target = root / ".agents" / leaf / "payload.txt"
            with self.assertRaises(TransactionError):
                FileTransaction(
                    root,
                    [Mutation(target, b"forbidden", expected_exists=False)],
                    state_dir=root / ".aegis" / "state" / "transactions",
                    name="governance-denial",
                ).commit(retain=False)
            self.assertFalse(target.exists())

    def test_atomic_write_rejects_active_governing_instruction(self) -> None:
        """AEGIS-I001: direct framework atomic writes protect root instructions."""

        with tempfile.TemporaryDirectory() as directory:
            root = _fixture_root(directory)
            target = root / "AGENTS.md"
            before = target.read_bytes()
            with self.assertRaises(AtomicWriteError):
                atomic_write_bytes(target, b"rewritten", root=root)
            self.assertEqual(target.read_bytes(), before)

    @given(subdirectory=SAFE_LEAF)
    def test_runtime_helpers_never_resolve_below_agents(self, subdirectory: str) -> None:
        """Runtime helpers are rooted in mutable .aegis, including aliases."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / subdirectory
            root.mkdir()
            for candidate in (
                runtime_dir(root),
                tasks_dir(root),
                persistent_dir(root),
                install_state_dir(root),
            ):
                relative = candidate.relative_to(root)
                self.assertEqual(relative.parts[0], ".aegis")
                self.assertNotIn(".agents", relative.parts)


if __name__ == "__main__":
    unittest.main()
