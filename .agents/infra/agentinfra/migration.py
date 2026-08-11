from __future__ import annotations

"""Transactional upgrade support for an installed Aegis framework tree.

Only release-declared immutable framework files are replaced.  Project-owned
extensions, project laws, runtime state, persistent recovery state, and the root
``AGENTS.md`` are deliberately outside the mutation set.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Callable

from .manifest import entries, parse, verify as verify_manifest
from .paths import persistent_dir, runtime_dir
from .security import confined_path
from .transaction import FileTransaction, Mutation


class UpgradeError(RuntimeError):
    pass


VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?$")
PRESERVED_PREFIXES = (
    ".agents/local-modules/",
    ".agents/laws/project/",
    ".agents/runtime/",
    ".agents/persistent/",
)
PRESERVED_EXACT = {".agents/project.md", "AGENTS.md"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version(root: Path) -> tuple[int, int, int]:
    path = root / ".agents" / "VERSION"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpgradeError(f"framework version is unavailable: {path}: {exc}") from exc
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise UpgradeError(f"invalid framework version: {value!r}")
    return tuple(int(match.group(index)) for index in range(1, 4))


def _preserved(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    return normalized in PRESERVED_EXACT or any(normalized.startswith(prefix) for prefix in PRESERVED_PREFIXES)


def _old_manifest_paths(target: Path) -> set[str]:
    path = target / ".agents" / "MANIFEST.sha256"
    if not path.is_file():
        return set()
    try:
        return set(parse(path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise UpgradeError(f"existing framework manifest is malformed: {exc}") from exc


def _generated_artifacts(target: Path) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    directories: list[Path] = []
    agents = target / ".agents"
    if not agents.exists():
        return files, directories
    for path in agents.rglob("*"):
        relative = path.relative_to(target).as_posix()
        if _preserved(relative) and not (path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}):
            continue
        if path.is_symlink():
            raise UpgradeError(f"upgrade refuses symlinked framework path: {relative}")
        if path.is_file() and path.suffix in {".pyc", ".pyo"}:
            files.append(path)
        elif path.is_dir() and path.name == "__pycache__":
            directories.append(path)
    return files, sorted(directories, key=lambda item: len(item.parts), reverse=True)


def _legacy_backup(target: Path, value: object, expected_sha256: object, destination: Path) -> tuple[Mutation, str]:
    if not isinstance(value, str) or not value:
        raise UpgradeError("legacy persistent journal lacks an original backup path")
    raw_path = Path(value)
    try:
        if raw_path.is_absolute():
            relative = raw_path.resolve(strict=True).relative_to(target)
            source = confined_path(target, relative, must_exist=True, reject_symlinks=True)
        else:
            source = confined_path(target, raw_path, must_exist=True, reject_symlinks=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise UpgradeError(f"legacy backup path is not confined to the project: {value}: {exc}") from exc
    payload = source.read_bytes()
    if not isinstance(expected_sha256, str) or _sha(payload) != expected_sha256:
        raise UpgradeError(f"legacy backup hash does not match journal: {source}")
    relative_destination = destination.relative_to(target).as_posix()
    current = destination.read_bytes() if destination.is_file() else None
    mutation = Mutation(
        destination,
        payload,
        expected_sha256=_sha(current) if current is not None else None,
        expected_exists=current is not None,
        mode=0o600,
    )
    return mutation, relative_destination


def _legacy_journal_mutations(target: Path, source_version: str) -> list[Mutation]:
    mutations: list[Mutation] = []
    specifications = (
        ("bootstrap", runtime_dir(target) / "bootstrap-install.json", persistent_dir(target) / "install-state" / "bootstrap" / "install.json"),
        ("codex", runtime_dir(target) / "codex-install.json", persistent_dir(target) / "install-state" / "codex" / "install.json"),
    )
    for kind, legacy_path, durable_path in specifications:
        if not legacy_path.exists():
            continue
        if durable_path.exists():
            raise UpgradeError(f"ambiguous {kind} recovery state exists in both runtime and persistent storage")
        try:
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise UpgradeError(f"invalid legacy {kind} install journal: {exc}") from exc
        if not isinstance(data, dict):
            raise UpgradeError(f"legacy {kind} install journal is not an object")
        if kind == "bootstrap":
            if data.get("schema") not in {1, 2}:
                raise UpgradeError("unsupported legacy bootstrap journal schema")
            backup_relative = None
            backup_mutation = None
            if not data.get("created_target"):
                backup_destination = durable_path.parent / "backups" / "migrated-original.bin"
                backup_mutation, backup_relative = _legacy_backup(
                    target, data.get("backup"), data.get("original_sha256"), backup_destination
                )
                mutations.append(backup_mutation)
            durable = {
                "schema": 2,
                "framework_version": source_version,
                "destination": "AGENTS.md",
                "created_target": bool(data.get("created_target")),
                "original_sha256": data.get("original_sha256"),
                "original_mode": data.get("original_mode"),
                "installed_sha256": data.get("installed_sha256"),
                "installed_block_sha256": data.get("installed_block_sha256"),
                "backup": backup_relative,
                "upgrade_count": int(data.get("upgrade_count", 0)),
            }
            required_hashes = (durable["original_sha256"], durable["installed_sha256"], durable["installed_block_sha256"])
            if any(not isinstance(value, str) or len(value) != 64 for value in required_hashes):
                raise UpgradeError("legacy bootstrap journal lacks required recovery hashes")
        else:
            if data.get("schema") not in {2, 3} or not isinstance(data.get("files"), list):
                raise UpgradeError("unsupported legacy Codex journal schema")
            records = []
            for index, record in enumerate(data["files"]):
                if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                    raise UpgradeError("legacy Codex journal has malformed file record")
                confined_path(target, record["path"], reject_symlinks=True)
                migrated = dict(record)
                if not record.get("created_file"):
                    destination = durable_path.parent / "backups" / f"migrated-original-{index:03d}.bin"
                    backup_mutation, relative = _legacy_backup(
                        target, record.get("backup"), record.get("original_sha256"), destination
                    )
                    mutations.append(backup_mutation)
                    migrated["backup"] = relative
                records.append(migrated)
            durable = {
                "schema": 3,
                "framework_version": source_version,
                "schema_probe": data.get("schema_probe", {"available": False, "capability": "UNOBSERVABLE", "reason": "migrated legacy journal"}),
                "files": records,
                "upgrade_count": int(data.get("upgrade_count", 0)),
            }
        encoded = (json.dumps(durable, indent=2, sort_keys=True) + "\n").encode("utf-8")
        mutations.append(Mutation(durable_path, encoded, expected_sha256=None, expected_exists=False, mode=0o600))
        legacy_raw = legacy_path.read_bytes()
        mutations.append(Mutation(legacy_path, None, expected_sha256=_sha(legacy_raw), expected_exists=True))
    return mutations


@dataclass(frozen=True)
class UpgradePlan:
    source_version: str
    target_version: str
    mutations: tuple[Mutation, ...]
    remove_empty_directories: tuple[Path, ...]
    preserved_paths: tuple[str, ...]

    def public(self) -> dict:
        return {
            "source_version": self.source_version,
            "target_version": self.target_version,
            "mutation_count": len(self.mutations),
            "destinations": [str(item.path) for item in self.mutations],
            "remove_empty_directories": [str(path) for path in self.remove_empty_directories],
            "preserved_paths": list(self.preserved_paths),
        }


def plan_upgrade(source: Path, target: Path) -> UpgradePlan:
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    if source == target:
        raise UpgradeError("source release and upgrade target must be distinct roots")
    source_ok, detail = verify_manifest(source, require_release_anchor=True)
    if not source_ok:
        raise UpgradeError(f"source release is not trusted: {detail}")
    source_version_tuple = _version(source)
    target_version_tuple = _version(target)
    if target_version_tuple > source_version_tuple:
        raise UpgradeError(
            f"refusing framework downgrade from {'.'.join(map(str, target_version_tuple))} "
            f"to {'.'.join(map(str, source_version_tuple))}"
        )

    declared = dict(entries(source))
    old_paths = _old_manifest_paths(target)
    preserved_paths = sorted(
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file() and _preserved(path.relative_to(target).as_posix())
    )
    mutations: list[Mutation] = []
    for relative in sorted(declared):
        if _preserved(relative):
            raise UpgradeError(f"source manifest attempts to own preserved project path: {relative}")
        destination = confined_path(target, relative, reject_symlinks=True)
        source_path = confined_path(source, relative, must_exist=True, reject_symlinks=True)
        desired = source_path.read_bytes()
        current = destination.read_bytes() if destination.is_file() else None
        if current != desired:
            mutations.append(
                Mutation(
                    destination,
                    desired,
                    expected_sha256=_sha(current) if current is not None else None,
                    expected_exists=current is not None,
                    mode=source_path.stat().st_mode & 0o777,
                )
            )
    for relative in sorted(old_paths - set(declared)):
        if _preserved(relative):
            continue
        destination = confined_path(target, relative, reject_symlinks=True)
        if destination.is_file():
            current = destination.read_bytes()
            mutations.append(Mutation(destination, None, expected_sha256=_sha(current), expected_exists=True))

    manifest_source = source / ".agents" / "MANIFEST.sha256"
    manifest_destination = confined_path(target, ".agents/MANIFEST.sha256", reject_symlinks=True)
    manifest_current = manifest_destination.read_bytes() if manifest_destination.is_file() else None
    manifest_desired = manifest_source.read_bytes()
    if manifest_current != manifest_desired:
        mutations.append(
            Mutation(
                manifest_destination,
                manifest_desired,
                expected_sha256=_sha(manifest_current) if manifest_current is not None else None,
                expected_exists=manifest_current is not None,
                mode=0o644,
            )
        )

    mutations.extend(_legacy_journal_mutations(target, ".".join(map(str, source_version_tuple))))
    generated_files, generated_directories = _generated_artifacts(target)
    destinations = {item.path.resolve(strict=False) for item in mutations}
    for path in generated_files:
        if path.resolve(strict=False) not in destinations:
            raw = path.read_bytes()
            mutations.append(Mutation(path, None, expected_sha256=_sha(raw), expected_exists=True))
    return UpgradePlan(
        source_version=".".join(map(str, source_version_tuple)),
        target_version=".".join(map(str, target_version_tuple)),
        mutations=tuple(mutations),
        remove_empty_directories=tuple(generated_directories),
        preserved_paths=tuple(preserved_paths),
    )


def upgrade(
    source: Path,
    target: Path,
    *,
    apply: bool = False,
    fault: Callable[[str, dict], None] | None = None,
) -> dict:
    source = source.resolve(strict=True)
    target = target.resolve(strict=True)
    initial = plan_upgrade(source, target)
    report = {"applied": False, "dry_run": not apply, **initial.public()}
    if not apply or not initial.mutations:
        return report

    # Re-plan immediately before commit so edits made during the dry-run window
    # become expected-hash conflicts rather than overwritten user work.
    fresh = plan_upgrade(source, target)
    initial_shape = [(str(item.path), item.expected_sha256, item.expected_exists, _sha(item.data) if item.data is not None else None) for item in initial.mutations]
    fresh_shape = [(str(item.path), item.expected_sha256, item.expected_exists, _sha(item.data) if item.data is not None else None) for item in fresh.mutations]
    if initial_shape != fresh_shape:
        raise UpgradeError("upgrade target changed while the operation was being planned")
    try:
        FileTransaction(
            target,
            list(fresh.mutations),
            state_dir=persistent_dir(target) / "transactions",
            name="framework-upgrade",
            fault=fault,
        ).commit(retain=False)
    except BaseException as exc:
        raise UpgradeError(f"framework upgrade transaction failed: {exc}") from exc
    for directory in fresh.remove_empty_directories:
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            pass
    return {"applied": True, "dry_run": False, **fresh.public()}
