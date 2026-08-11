from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
from typing import Callable

from .paths import persistent_dir, runtime_dir, tasks_dir
from .security import is_path_redirect
from .transaction import FaultHook, FileTransaction, Mutation, TransactionError


class RuntimeMigrationError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RuntimeMigrationPlan:
    mutations: tuple[Mutation, ...]
    copies: tuple[dict, ...]
    already_current: tuple[str, ...]

    def public(self) -> dict:
        return {
            "copy_count": len(self.copies),
            "already_current": list(self.already_current),
            "copies": list(self.copies),
        }


def _destination(root: Path, source: Path) -> Path:
    agents = root / ".agents"
    relative = source.relative_to(agents)
    if relative.parts[0] == "runtime":
        tail = Path(*relative.parts[1:])
        if tail.parts and tail.parts[0] == "tasks":
            return tasks_dir(root) / Path(*tail.parts[1:])
        return runtime_dir(root) / "legacy" / tail
    if relative.parts[0] == "persistent":
        return persistent_dir(root) / Path(*relative.parts[1:])
    raise RuntimeMigrationError(f"unsupported legacy runtime source: {source}")


def plan_runtime_migration(root: Path) -> RuntimeMigrationPlan:
    project = Path(root).resolve(strict=True)
    mutations: list[Mutation] = []
    copies: list[dict] = []
    current: list[str] = []
    for legacy_root in (project / ".agents" / "runtime", project / ".agents" / "persistent"):
        if not legacy_root.exists():
            continue
        if not legacy_root.is_dir() or is_path_redirect(legacy_root):
            raise RuntimeMigrationError(f"legacy runtime root is redirected or not a directory: {legacy_root}")
        for source in sorted(legacy_root.rglob("*"), key=lambda item: item.relative_to(project).as_posix()):
            if is_path_redirect(source):
                raise RuntimeMigrationError(f"legacy runtime migration refuses redirected source: {source}")
            if source.is_dir():
                continue
            if not source.is_file():
                raise RuntimeMigrationError(f"legacy runtime migration refuses non-file source: {source}")
            destination = _destination(project, source)
            payload = source.read_bytes()
            relative_source = source.relative_to(project).as_posix()
            relative_destination = destination.relative_to(project).as_posix()
            if destination.exists():
                if not destination.is_file() or is_path_redirect(destination):
                    raise RuntimeMigrationError(f"runtime migration destination is redirected or not a file: {relative_destination}")
                if destination.read_bytes() != payload:
                    raise RuntimeMigrationError(f"runtime migration collision at {relative_destination}")
                current.append(relative_destination)
                continue
            copies.append(
                {
                    "source": relative_source,
                    "destination": relative_destination,
                    "sha256": _sha(payload),
                    "size": len(payload),
                }
            )
            mutations.append(
                Mutation(
                    destination,
                    payload,
                    expected_exists=False,
                    mode=stat.S_IMODE(source.stat(follow_symlinks=False).st_mode),
                )
            )
    return RuntimeMigrationPlan(tuple(mutations), tuple(copies), tuple(sorted(current)))


def migrate_runtime(
    root: Path,
    *,
    apply: bool = False,
    fault: FaultHook | None = None,
) -> dict:
    project = Path(root).resolve(strict=True)
    plan = plan_runtime_migration(project)
    report = {"applied": False, "dry_run": not apply, **plan.public()}
    if not apply or not plan.mutations:
        return report
    # Re-plan at commit time so source/destination drift is never overwritten.
    fresh = plan_runtime_migration(project)
    if fresh.copies != plan.copies:
        raise RuntimeMigrationError("legacy runtime changed while migration was being planned")
    try:
        FileTransaction(
            project,
            fresh.mutations,
            state_dir=persistent_dir(project) / "transactions",
            name="runtime-layout-migration",
            fault=fault,
        ).commit(retain=False)
    except (TransactionError, OSError, RuntimeError) as exc:
        raise RuntimeMigrationError(f"runtime migration transaction failed: {exc}") from exc
    return {"applied": True, "dry_run": False, **fresh.public()}
