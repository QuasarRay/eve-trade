from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import pytest
import yaml


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def assert_ok(self) -> "CommandResult":
        assert self.returncode == 0, (
            f"command failed ({self.returncode}): {' '.join(self.argv)}\n"
            f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
        )
        return self


class RepoInspector:
    def __init__(self, root: Path, *, strict: bool):
        self.root = root.resolve()
        self.strict = strict

    def require_repo(self) -> None:
        if (self.root / "go.mod").exists() and (self.root / "distributed-backend").exists():
            return
        self.unavailable(f"eve-trade checkout not found at {self.root}; set EVE_TRADE_REPO_ROOT")

    def unavailable(self, reason: str) -> None:
        if self.strict:
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def existing_paths(self, patterns: Sequence[str]) -> list[Path]:
        self.require_repo()
        results: list[Path] = []
        for pattern in patterns:
            results.extend(p for p in self.root.glob(pattern) if p.is_file())
        return sorted(set(results))

    def require_paths(self, patterns: Sequence[str], *, purpose: str) -> list[Path]:
        paths = self.existing_paths(patterns)
        if not paths:
            self.unavailable(f"no repository files found for {purpose}: {patterns}")
        return paths

    def read(self, relative: str | Path) -> str:
        path = relative if isinstance(relative, Path) else self.root / relative
        return path.read_text(encoding="utf-8", errors="replace")

    def all_text(self, patterns: Sequence[str]) -> str:
        return "\n".join(self.read(p) for p in self.existing_paths(patterns))

    def grep(self, pattern: str, patterns: Sequence[str], *, flags: int = re.I | re.M) -> list[tuple[Path, re.Match[str]]]:
        regex = re.compile(pattern, flags)
        hits: list[tuple[Path, re.Match[str]]] = []
        for path in self.existing_paths(patterns):
            for match in regex.finditer(self.read(path)):
                hits.append((path, match))
        return hits

    def assert_contains(self, patterns: Sequence[str], needle: str | re.Pattern[str], *, reason: str) -> None:
        paths = self.require_paths(patterns, purpose=reason)
        if isinstance(needle, str):
            ok = any(needle in self.read(p) for p in paths)
        else:
            ok = any(needle.search(self.read(p)) for p in paths)
        assert ok, reason

    def assert_not_contains(self, patterns: Sequence[str], needle: str | re.Pattern[str], *, reason: str) -> None:
        for p in self.existing_paths(patterns):
            text = self.read(p)
            if isinstance(needle, str):
                assert needle not in text, f"{reason}: found in {p.relative_to(self.root)}"
            else:
                assert not needle.search(text), f"{reason}: found in {p.relative_to(self.root)}"

    def yaml_documents(self, patterns: Sequence[str]) -> list[tuple[Path, dict[str, Any]]]:
        docs: list[tuple[Path, dict[str, Any]]] = []
        for path in self.existing_paths(patterns):
            try:
                parsed = list(yaml.safe_load_all(self.read(path)))
            except yaml.YAMLError as exc:
                raise AssertionError(f"invalid YAML in {path.relative_to(self.root)}: {exc}") from exc
            for doc in parsed:
                if isinstance(doc, dict):
                    docs.append((path, doc))
        return docs

    def workflow_files(self) -> list[Path]:
        return self.existing_paths([".github/workflows/*.yml", ".github/workflows/*.yaml"])

    def workflow_yaml(self) -> list[tuple[Path, dict[str, Any]]]:
        return self.yaml_documents([".github/workflows/*.yml", ".github/workflows/*.yaml"])

    def workflow_run_commands(self) -> list[str]:
        commands: list[str] = []
        for _, workflow in self.workflow_yaml():
            jobs = workflow.get("jobs") or {}
            if not isinstance(jobs, dict):
                continue
            for job in jobs.values():
                if not isinstance(job, dict):
                    continue
                for step in job.get("steps") or []:
                    if isinstance(step, dict) and isinstance(step.get("run"), str):
                        commands.append(step["run"])
                    if isinstance(step, dict) and step.get("uses") == "./.github/actions/ci-evidence":
                        with_ = step.get("with") or {}
                        if isinstance(with_, dict) and isinstance(with_.get("command"), str):
                            commands.append(with_["command"])
        return commands

    def workflow_uses(self) -> list[str]:
        uses: list[str] = []
        for _, workflow in self.workflow_yaml():
            jobs = workflow.get("jobs") or {}
            if not isinstance(jobs, dict):
                continue
            for job in jobs.values():
                if not isinstance(job, dict):
                    continue
                for step in job.get("steps") or []:
                    if isinstance(step, dict) and isinstance(step.get("uses"), str):
                        uses.append(step["uses"])
        return uses

    def kubernetes_documents(self) -> list[tuple[Path, dict[str, Any]]]:
        patterns = [
            "distributed-backend/ci-cd/**/*.yaml",
            "distributed-backend/ci-cd/**/*.yml",
            "infra/**/*.yaml",
            "infra/**/*.yml",
        ]
        return self.yaml_documents(patterns)

    def terraform_roots(self) -> list[Path]:
        self.require_repo()
        roots: set[Path] = set()
        for tf in self.root.rglob("*.tf"):
            if ".terraform" in tf.parts:
                continue
            roots.add(tf.parent)
        return sorted(roots)

    def dockerfiles(self) -> list[Path]:
        self.require_repo()
        return sorted({p for p in self.root.rglob("Dockerfile*") if p.is_file()})

    def requirements_files(self) -> list[Path]:
        self.require_repo()
        return sorted(p for p in self.root.rglob("requirements*.txt") if p.is_file())

    def python_test_names(self, patterns: Sequence[str] = ("**/test_*.py",)) -> dict[str, Path]:
        names: dict[str, Path] = {}
        for path in self.existing_paths(patterns):
            try:
                tree = ast.parse(self.read(path), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    names[node.name] = path
        return names

    def go_test_names(self) -> dict[str, Path]:
        names: dict[str, Path] = {}
        regex = re.compile(r"(?m)^func\s+(Test[A-Za-z0-9_]+)\s*\(")
        for path in self.existing_paths(["**/*_test.go"]):
            for m in regex.finditer(self.read(path)):
                names[m.group(1)] = path
        return names

    def run(self, argv: Sequence[str], *, cwd: Path | None = None, timeout: int = 120, env: dict[str, str] | None = None, require_tool: bool = True) -> CommandResult:
        if not argv:
            raise ValueError("empty command")
        exe = argv[0]
        if require_tool and shutil.which(exe) is None:
            self.unavailable(f"required tool {exe!r} is not installed")
        merged = os.environ.copy()
        if env:
            merged.update(env)
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd or self.root),
            env=merged,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr)

    def git_diff_clean(self, *paths: str) -> None:
        if shutil.which("git") is None or not (self.root / ".git").exists():
            self.unavailable("git checkout metadata is required for diff-based contract")
        argv = ["git", "diff", "--exit-code", "--", *paths] if paths else ["git", "diff", "--exit-code"]
        self.run(argv, require_tool=True).assert_ok()

    def normalized_texts(self, patterns: Sequence[str]) -> dict[str, str]:
        return {
            str(p.relative_to(self.root)): re.sub(r"\s+", " ", self.read(p)).strip()
            for p in self.existing_paths(patterns)
        }

    def find_files_named(self, names: Iterable[str]) -> list[Path]:
        wanted = set(names)
        return sorted(p for p in self.root.rglob("*") if p.is_file() and p.name in wanted)
