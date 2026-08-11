from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import tomllib

from .paths import persistent_dir
from .security import is_path_redirect
from .transaction import FileTransaction, Mutation, TransactionError


SOURCE_DIRECTORIES = (
    "bin",
    "bootstrap",
    "core",
    "infra",
    "laws",
    "modules",
    "protocols",
    "scripts",
    "templates",
    "tests-to-impl",
)
SOURCE_FILES = (
    "VERSION",
    "README.md",
    "CHANGELOG.md",
    "INDEX.md",
    "MIGRATION.md",
    "framework.toml",
    "workspace-policy.toml",
)
GENERATED_SOURCE_DIRECTORIES = frozenset(
    {"__pycache__", ".hypothesis", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class ReleaseSourceError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_version(root: Path) -> str:
    path = Path(root).resolve(strict=True) / "VERSION"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReleaseSourceError(f"canonical VERSION is unavailable: {exc}") from exc
    if not _VERSION_RE.fullmatch(value):
        raise ReleaseSourceError(f"canonical VERSION is not strict semantic version: {value!r}")
    return value


def _toml_version(path: Path, table: str) -> str:
    try:
        with path.open("rb") as stream:
            value = tomllib.load(stream)
        version = value[table]["version"]
    except Exception as exc:
        raise ReleaseSourceError(f"cannot read version from {path}: {exc}") from exc
    return str(version)


def validate_version_consistency(root: Path) -> dict:
    project = Path(root).resolve(strict=True)
    version = canonical_version(project)
    observations: dict[str, str | None] = {
        "VERSION": version,
        "framework.toml": _toml_version(project / "framework.toml", "framework"),
        "infra/pyproject.toml": _toml_version(project / "infra" / "pyproject.toml", "project"),
    }
    heading = (project / "README.md").read_text(encoding="utf-8").splitlines()[0]
    match = re.fullmatch(r"# Aegis Framework ([0-9]+\.[0-9]+\.[0-9]+)", heading)
    observations["README.md"] = match.group(1) if match else None
    release = project / "RELEASE.json"
    if release.is_file():
        try:
            observations["RELEASE.json"] = str(json.loads(release.read_text(encoding="utf-8"))["version"])
        except Exception as exc:
            raise ReleaseSourceError(f"invalid RELEASE.json: {exc}") from exc
    for manifest in sorted((project / "modules").glob("*/module.toml")) if (project / "modules").is_dir() else ():
        if manifest.parent.name == "example-agent":
            continue
        observations[manifest.relative_to(project).as_posix()] = _toml_version(manifest, "module")
    mismatches = {name: found for name, found in observations.items() if found != version}
    return {"ok": not mismatches, "version": version, "observations": observations, "mismatches": mismatches}


def _source_files(root: Path) -> list[Path]:
    selected: list[Path] = []
    for relative in SOURCE_FILES:
        path = root / relative
        if path.is_file():
            selected.append(path)
    for relative in SOURCE_DIRECTORIES:
        base = root / relative
        if not base.exists():
            continue
        if not base.is_dir() or is_path_redirect(base):
            raise ReleaseSourceError(f"source distribution directory is redirected or invalid: {relative}")
        for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            source_relative = path.relative_to(base)
            if (
                any(part in GENERATED_SOURCE_DIRECTORIES for part in source_relative.parts)
                or path.suffix in {".pyc", ".pyo"}
            ):
                continue
            if is_path_redirect(path):
                raise ReleaseSourceError(f"source distribution contains redirect: {path.relative_to(root)}")
            if path.is_file():
                selected.append(path)
            elif not path.is_dir():
                raise ReleaseSourceError(f"source distribution contains unsupported entry: {path.relative_to(root)}")
    unique = sorted(set(selected), key=lambda item: item.relative_to(root).as_posix())
    if not unique:
        raise ReleaseSourceError("source distribution inventory is empty")
    return unique


def _manifest(entries: list[tuple[str, bytes]]) -> bytes:
    return "".join(f"{_sha(payload)}  .agents/{relative}\n" for relative, payload in entries).encode("utf-8")


def _validate_destination(root: Path, destination: Path) -> Path:
    target = destination.resolve(strict=False)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ReleaseSourceError("deployment build destination must be inside the source workspace") from exc
    if not relative.parts or relative.parts[0].casefold() in {".agents", ".aegis"}:
        raise ReleaseSourceError("deployment build cannot target active governance or runtime state")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ReleaseSourceError("deployment build destination must be absent or an empty directory")
    return target


def build_deployment_tree(root: Path, destination: Path) -> dict:
    project = Path(root).resolve(strict=True)
    target = _validate_destination(project, Path(destination))
    version_report = validate_version_consistency(project)
    if not version_report["ok"]:
        raise ReleaseSourceError(f"source version metadata is inconsistent: {version_report['mismatches']}")
    entries = [(path.relative_to(project).as_posix(), path.read_bytes()) for path in _source_files(project)]
    manifest = _manifest(entries)
    manifest_sha256 = _sha(manifest)
    content_sha256 = _sha(
        b"".join(
            relative.encode("utf-8") + b"\0" + str(len(payload)).encode("ascii") + b"\0" + payload
            for relative, payload in entries
        )
    )
    release = {
        "schema": 2,
        "version": version_report["version"],
        "manifest_sha256": manifest_sha256,
        "content_sha256": content_sha256,
        "source_authority": "source-tree-outside-.agents",
        "deployment": "manual-human-action-required",
    }
    mutations: list[Mutation] = []
    for relative, payload in entries:
        source = project / relative
        mutations.append(
            Mutation(
                target / ".agents" / relative,
                payload,
                expected_exists=False,
                mode=stat.S_IMODE(source.stat(follow_symlinks=False).st_mode),
            )
        )
    mutations.extend(
        (
            Mutation(target / ".agents" / "MANIFEST.sha256", manifest, expected_exists=False, mode=0o644),
            Mutation(
                target / "RELEASE.json",
                (json.dumps(release, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                expected_exists=False,
                mode=0o644,
            ),
        )
    )
    try:
        FileTransaction(
            project,
            mutations,
            state_dir=persistent_dir(project) / "transactions",
            name="source-deployment-build",
        ).commit(retain=False)
    except (TransactionError, OSError) as exc:
        raise ReleaseSourceError(f"deployment build transaction failed: {exc}") from exc
    verified = verify_deployment_tree(target)
    if not verified["ok"]:
        raise ReleaseSourceError(f"deployment build postcondition failed: {verified}")
    return {
        "source_root": str(project),
        "destination": str(target),
        "version": version_report["version"],
        "file_count": len(entries),
        "manifest_sha256": manifest_sha256,
        "content_sha256": content_sha256,
        "manual_deployment_required": True,
    }


def verify_deployment_tree(destination: Path) -> dict:
    target = Path(destination).resolve(strict=True)
    agents = target / ".agents"
    manifest_path = agents / "MANIFEST.sha256"
    try:
        manifest = manifest_path.read_bytes()
        declared: dict[str, str] = {}
        for line in manifest.decode("utf-8").splitlines():
            digest, relative = line.split("  ", 1)
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or not relative.startswith(".agents/"):
                raise ValueError("invalid manifest line")
            declared[relative] = digest
        actual_files = {
            path.relative_to(target).as_posix()
            for path in agents.rglob("*")
            if path.is_file() and path != manifest_path
        }
        mismatches = [
            relative
            for relative, digest in declared.items()
            if not (target / relative).is_file() or _sha((target / relative).read_bytes()) != digest
        ]
        extras = sorted(actual_files - set(declared))
        release = json.loads((target / "RELEASE.json").read_text(encoding="utf-8"))
        metadata_ok = release.get("manifest_sha256") == _sha(manifest)
        return {
            "ok": not mismatches and not extras and metadata_ok,
            "declared": len(declared),
            "mismatches": sorted(mismatches),
            "extras": extras,
            "metadata_ok": metadata_ok,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "declared": 0, "mismatches": [], "extras": []}
