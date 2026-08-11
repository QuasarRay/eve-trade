from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .atomic import atomic_write_bytes
from .governance import _governance_tree_creation
from .locks import FileLock
from .security import confined_path


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
MUTABLE_PREFIXES = (
    ".agents/runtime/",
    ".agents/persistent/",
    ".agents/local-modules/",
    ".agents/laws/project/",
)
MUTABLE_EXACT = {".agents/project.md", ".agents/MANIFEST.sha256"}
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".swp"}
FORBIDDEN_NAMES = {".coverage", "coverage.xml", "subagent-lease.json", ".state.lock", ".evidence.lock"}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ManifestError(RuntimeError):
    pass


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str, *, require_agents: bool = True) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ManifestError(f"non-canonical manifest path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManifestError(f"unsafe manifest path: {value!r}")
    if require_agents and (not path.parts or path.parts[0] != ".agents"):
        raise ManifestError(f"manifest path is outside .agents distribution: {value!r}")
    return path.as_posix()


def is_mutable(relative: str) -> bool:
    return relative in MUTABLE_EXACT or any(relative.startswith(prefix) for prefix in MUTABLE_PREFIXES)


def is_forbidden(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    name = parts[-1] if parts else ""
    return bool(set(parts) & FORBIDDEN_PARTS) or Path(name).suffix.lower() in FORBIDDEN_SUFFIXES or name in FORBIDDEN_NAMES


def entries(root: Path):
    root = root.resolve(strict=True)
    agents = root / ".agents"
    if not agents.is_dir() or agents.is_symlink():
        raise ManifestError(".agents distribution root is missing or symlinked")
    for path in sorted(agents.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if is_mutable(relative) or is_forbidden(relative):
            continue
        if path.is_symlink():
            raise ManifestError(f"immutable distribution path is a symlink: {relative}")
        if path.is_file():
            yield relative, file_sha(path)


def render(root: Path) -> str:
    return "".join(f"{digest}  {relative}\n" for relative, digest in entries(root))


def parse(text: str) -> dict[str, str]:
    if text and not text.endswith("\n"):
        raise ManifestError("manifest must end with a newline")
    result: dict[str, str] = {}
    previous: str | None = None
    for line_no, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (\S(?:.*\S)?)", line)
        if not match:
            raise ManifestError(f"malformed manifest line {line_no}")
        digest, raw_path = match.groups()
        if not SHA_RE.fullmatch(digest):
            raise ManifestError(f"invalid SHA-256 digest at line {line_no}")
        relative = _safe_relative(raw_path)
        if relative in result:
            raise ManifestError(f"duplicate manifest path at line {line_no}: {relative}")
        if previous is not None and relative <= previous:
            raise ManifestError("manifest paths are not in strict deterministic order")
        if is_mutable(relative) or is_forbidden(relative):
            raise ManifestError(f"manifest contains excluded/generated path: {relative}")
        result[relative] = digest
        previous = relative
    if not result:
        raise ManifestError("manifest must not be empty")
    return result


def _forbidden_artifacts(root: Path) -> list[str]:
    agents = root / ".agents"
    found: list[str] = []
    for path in agents.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if any(relative.startswith(prefix) for prefix in (".agents/runtime/", ".agents/persistent/")):
            continue
        if is_forbidden(relative):
            found.append(relative)
    return sorted(found)


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release_anchor(root: Path) -> dict:
    path = root / "RELEASE.json"
    if path.is_symlink():
        raise ManifestError("release anchor must not be a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError("release anchor missing") from exc
    except Exception as exc:
        raise ManifestError(f"invalid release anchor JSON: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") not in {1, 2}
        or not SHA_RE.fullmatch(str(value.get("manifest_sha256", "")))
        or not VERSION_RE.fullmatch(str(value.get("version", "")))
    ):
        raise ManifestError("invalid release anchor schema")
    if value["schema"] == 2 and (
        not SHA_RE.fullmatch(str(value.get("content_sha256", "")))
        or value.get("source_authority") != "source-tree-outside-.agents"
        or value.get("deployment") != "manual-human-action-required"
    ):
        raise ManifestError("invalid source-deployment release anchor schema")
    return value


def _deployment_content_sha256(root: Path, expected: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for deployed_relative in sorted(expected):
        path = PurePosixPath(deployed_relative)
        if not path.parts or path.parts[0] != ".agents" or len(path.parts) < 2:
            raise ManifestError("release content path is outside .agents")
        source_relative = PurePosixPath(*path.parts[1:]).as_posix()
        payload = (root / deployed_relative).read_bytes()
        digest.update(
            source_relative.encode("utf-8")
            + b"\0"
            + str(len(payload)).encode("ascii")
            + b"\0"
            + payload
        )
    return digest.hexdigest()


def verify(root: Path, *, require_release_anchor: bool = False, expected_manifest_sha256: str | None = None):
    root = root.resolve(strict=True)
    manifest = root / ".agents" / "MANIFEST.sha256"
    try:
        if manifest.is_symlink():
            raise ManifestError("manifest must not be a symlink")
        expected = parse(manifest.read_text(encoding="utf-8"))
        actual = dict(entries(root))
        forbidden = _forbidden_artifacts(root)
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(key for key in expected.keys() & actual.keys() if expected[key] != actual[key])
        manifest_digest = manifest_sha256(manifest)
        if expected_manifest_sha256 is not None:
            if not SHA_RE.fullmatch(expected_manifest_sha256) or manifest_digest != expected_manifest_sha256:
                raise ManifestError("manifest does not match caller-supplied external trust anchor")
        anchor_detail = None
        anchor_path = root / "RELEASE.json"
        if require_release_anchor or anchor_path.exists():
            anchor = _release_anchor(root)
            if anchor.get("manifest_sha256") != manifest_digest:
                raise ManifestError("regeneratable manifest does not match release-bound anchor")
            version = (root / ".agents" / "VERSION").read_text(encoding="utf-8").strip()
            if anchor.get("version") != version:
                raise ManifestError("release anchor version does not match framework VERSION")
            if anchor.get("schema") == 2:
                content_digest = _deployment_content_sha256(root, expected)
                if anchor.get("content_sha256") != content_digest:
                    raise ManifestError("release content digest does not match deployed immutable files")
            anchor_detail = {
                "path": "RELEASE.json",
                "schema": anchor["schema"],
                "manifest_sha256": manifest_digest,
                "content_sha256": anchor.get("content_sha256"),
            }
        ok = not (missing or extra or changed or forbidden)
        return ok, {
            "missing": missing,
            "extra": extra,
            "changed": changed,
            "forbidden": forbidden,
            "manifest_sha256": manifest_digest,
            "release_anchor": anchor_detail,
        }
    except (OSError, ManifestError) as exc:
        return False, str(exc)


def regenerate(root: Path, *, maintenance_authorized: bool = False) -> dict:
    if not maintenance_authorized:
        raise ManifestError("manifest regeneration requires explicit maintenance authorization")
    root = root.resolve(strict=True)
    path = root / ".agents" / "MANIFEST.sha256"
    lock = FileLock(root / ".agents" / "persistent" / "manifest-regeneration.lock", "manifest-regeneration")
    lock.acquire()
    try:
        content = render(root).encode("utf-8")
        atomic_write_bytes(path, content, root=root, mode=0o644)
        # Deliberately do not update RELEASE.json: that separate, review-bound
        # anchor makes unreviewed manifest regeneration detectable.
        return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "entries": len(parse(content.decode("utf-8")))}
    finally:
        lock.release()


def write_release_anchor(root: Path, *, release_authorized: bool = False) -> dict:
    if not release_authorized:
        raise ManifestError("release-anchor update requires explicit release authorization")
    root = root.resolve(strict=True)
    manifest = root / ".agents" / "MANIFEST.sha256"
    parse(manifest.read_text(encoding="utf-8"))
    anchor = {
        "schema": 1,
        "version": (root / ".agents" / "VERSION").read_text(encoding="utf-8").strip(),
        "manifest_sha256": manifest_sha256(manifest),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_bytes(root / "RELEASE.json", (json.dumps(anchor, indent=2, sort_keys=True) + "\n").encode("utf-8"), root=root, mode=0o644)
    return anchor


def build_archive(root: Path, destination: Path) -> dict:
    root = root.resolve(strict=True)
    expected = dict(entries(root))
    manifest = root / ".agents" / "MANIFEST.sha256"
    parsed = parse(manifest.read_text(encoding="utf-8"))
    if parsed != expected:
        raise ManifestError("refusing to package a tree that does not match its manifest")
    _release_anchor(root)
    files = sorted(["RELEASE.json", ".agents/MANIFEST.sha256", *expected])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative in files:
                path = confined_path(root, relative, must_exist=True, reject_symlinks=True)
                info = zipfile.ZipInfo(relative, ZIP_TIMESTAMP)
                info.create_system = 3
                mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
                info.external_attr = ((mode or 0o644) & 0xFFFF) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes(), compresslevel=9)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(destination), "sha256": file_sha(destination), "members": len(files)}


def safe_extract(archive_path: Path, destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve(strict=True)
    with zipfile.ZipFile(archive_path) as archive:
        seen: set[str] = set()
        members = archive.infolist()
        for info in members:
            relative = _safe_relative(info.filename, require_agents=False)
            if relative in seen:
                raise ManifestError(f"duplicate archive member: {relative}")
            seen.add(relative)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ManifestError(f"archive symlink is forbidden: {relative}")
            confined_path(root, relative, reject_symlinks=True)
        for info in members:
            relative = _safe_relative(info.filename, require_agents=False)
            target = confined_path(root, relative, reject_symlinks=True)
            if target.exists():
                raise ManifestError(f"archive extraction refuses to overwrite: {relative}")
        with _governance_tree_creation(root):
            for info in members:
                relative = _safe_relative(info.filename, require_agents=False)
                target = confined_path(root, relative, reject_symlinks=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(
                    target,
                    archive.read(info),
                    root=root,
                    mode=((info.external_attr >> 16) & 0o777) or 0o644,
                )
    return sorted(seen)
