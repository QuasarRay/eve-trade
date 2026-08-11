from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from .atomic import atomic_write_json
from .security import is_path_redirect, minimal_subprocess_env


PACKAGE_MARKERS = {
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Makefile",
}
LOCKFILES = {
    "Cargo.lock", "poetry.lock", "Pipfile.lock", "uv.lock", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "go.sum", "Gemfile.lock", "composer.lock",
}
GENERATED_NAMES = {"generated", "build", "dist", "target", "out", "htmlcov", ".pytest_cache", "__pycache__"}
VENDOR_NAMES = {"vendor", "node_modules", "third_party", "third-party"}
REFERENCE_NAMES = {"reference", "references", "oracle", "oracles"}
SCAN_EXCLUDED = {".git", ".aegis", "node_modules", "__pycache__"}


class DiscoveryError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=False, timeout=20,
            env=minimal_subprocess_env(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _git_facts(root: Path) -> dict:
    probe = _run_git(root, "rev-parse", "--show-toplevel")
    if probe is None or probe.returncode != 0:
        return {"available": False, "head": None, "top_level": None, "worktrees": [], "submodules": [], "dirty_tracked": [], "untracked": []}
    top = Path(probe.stdout.decode("utf-8", "replace").strip()).resolve()
    head_probe = _run_git(root, "rev-parse", "--verify", "HEAD")
    head = head_probe.stdout.decode("ascii", "replace").strip() if head_probe and head_probe.returncode == 0 else None
    status_probe = _run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    dirty: list[str] = []
    untracked: list[str] = []
    if status_probe and status_probe.returncode == 0:
        records = [item for item in status_probe.stdout.split(b"\0") if item]
        index = 0
        while index < len(records):
            raw = records[index]
            index += 1
            if len(raw) < 4:
                continue
            marker = raw[:2].decode("ascii", "replace")
            path = raw[3:].decode("utf-8", "surrogateescape").replace("\\", "/")
            if "R" in marker or "C" in marker:
                index += 1
            (untracked if marker == "??" else dirty).append(path)
    worktrees: list[str] = []
    worktree_probe = _run_git(root, "worktree", "list", "--porcelain")
    if worktree_probe and worktree_probe.returncode == 0:
        for line in worktree_probe.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("worktree "):
                worktrees.append(str(Path(line[9:]).resolve()))
    submodules: list[str] = []
    modules_probe = _run_git(root, "submodule", "status", "--recursive")
    if modules_probe and modules_probe.returncode == 0:
        for line in modules_probe.stdout.decode("utf-8", "replace").splitlines():
            parts = line.lstrip("-+U ").split()
            if len(parts) >= 2:
                submodules.append(parts[1].replace("\\", "/"))
    return {
        "available": True,
        "head": head,
        "top_level": str(top),
        "worktrees": sorted(set(worktrees)),
        "submodules": sorted(set(submodules)),
        "dirty_tracked": sorted(set(dirty)),
        "untracked": sorted(set(untracked)),
    }


def discover_repository(root: Path) -> dict:
    project = Path(root).resolve(strict=True)
    package_roots: list[str] = []
    nested: list[str] = []
    governance: list[str] = []
    runtime: list[str] = []
    instructions: list[str] = []
    generated: list[str] = []
    vendor: list[str] = []
    references: list[str] = []
    redirects: list[str] = []
    lockfiles: list[str] = []

    for directory, names, files in os.walk(project, topdown=True, followlinks=False):
        base = Path(directory)
        relative_dir = base.relative_to(project)
        names[:] = sorted(names)
        contains_git_directory = ".git" in names
        for name in list(names):
            path = base / name
            relative = path.relative_to(project).as_posix()
            if is_path_redirect(path):
                redirects.append(relative)
                names.remove(name)
                continue
            if name == ".agents":
                governance.append(relative)
            elif name == ".aegis":
                runtime.append(relative)
                names.remove(name)
                continue
            if name in GENERATED_NAMES:
                generated.append(relative)
            if name in VENDOR_NAMES:
                vendor.append(relative)
            if name in REFERENCE_NAMES:
                references.append(relative)
            if name in SCAN_EXCLUDED:
                names.remove(name)
        if relative_dir != Path(".") and ".git" in files:
            nested.append(relative_dir.as_posix())
        if relative_dir != Path(".") and contains_git_directory:
            nested.append(relative_dir.as_posix())
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(project).as_posix()
            if is_path_redirect(path):
                redirects.append(relative)
            if name == "AGENTS.md":
                instructions.append(relative)
            if name in PACKAGE_MARKERS:
                package_roots.append(relative)
            if name in LOCKFILES:
                lockfiles.append(relative)

    git = _git_facts(project)
    nested = sorted(set(nested) - {"."})
    body = {
        "schema": 1,
        "repository_root": str(project),
        "git": {key: git[key] for key in ("available", "head", "top_level")},
        "worktrees": git["worktrees"],
        "submodules": git["submodules"],
        "nested_repositories": nested,
        "package_roots": sorted(set(package_roots)),
        "governance_roots": sorted(set(governance)),
        "runtime_roots": sorted(set(runtime)),
        "instruction_hierarchy": sorted(set(instructions)),
        "generated_directories": sorted(set(generated)),
        "vendor_directories": sorted(set(vendor)),
        "reference_repositories": sorted(set(references)),
        "redirect_boundaries": sorted(set(redirects)),
        "dirty_tracked": git["dirty_tracked"],
        "untracked": git["untracked"],
        "lockfiles": sorted(set(lockfiles)),
    }
    body["digest"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


def write_discovery_artifact(root: Path, artifact: dict) -> Path:
    project = Path(root).resolve(strict=True)
    supplied = dict(artifact)
    claimed = supplied.pop("digest", None)
    actual = hashlib.sha256(_canonical(supplied)).hexdigest()
    if claimed != actual:
        raise DiscoveryError("repository discovery artifact digest mismatch")
    path = project / ".aegis" / "audit" / f"repository-discovery-{actual}.json"
    atomic_write_json(path, artifact, root=project, mode=0o600)
    return path
