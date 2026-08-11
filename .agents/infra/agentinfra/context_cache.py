from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable

from .atomic import atomic_write_json
from .locks import FileLock
from .paths import cache_dir
from .security import confined_path, redact_text


SCHEMA = 3


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _parse(value):
    return datetime.fromisoformat(value) if value else None


def _source_key(source: str) -> str:
    return "external:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _stat_identity(path: Path) -> dict:
    stat = path.stat(follow_symlinks=False)
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "inode": getattr(stat, "st_ino", None),
        "device": getattr(stat, "st_dev", None),
        "symlink_target": str(path.readlink()) if path.is_symlink() else None,
    }


def _validate(data: dict) -> None:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("sources"), dict):
        raise RuntimeError("context cache schema is invalid")
    for key, entry in data["sources"].items():
        if not isinstance(key, str) or not isinstance(entry, dict) or entry.get("kind") not in {"file", "external", "summary"}:
            raise RuntimeError("context cache entry schema is invalid")
        if entry["kind"] == "file":
            if not all(isinstance(entry.get(field), str) for field in ("path", "sha256", "recorded_at")):
                raise RuntimeError("context cache file entry is malformed")
            if not isinstance(entry.get("stat"), dict) or not isinstance(entry.get("dependencies", []), list):
                raise RuntimeError("context cache file entry stat/dependencies are malformed")
        if entry["kind"] == "external":
            if not all(isinstance(entry.get(field), str) for field in ("source", "fingerprint", "recorded_at", "provenance")):
                raise RuntimeError("context cache external entry is malformed")
            if entry.get("valid_until") is not None and not isinstance(entry.get("valid_until"), str):
                raise RuntimeError("context cache external validity is malformed")
        if entry["kind"] == "summary":
            if not isinstance(entry.get("source"), str) or not isinstance(entry.get("sha256"), str) or not isinstance(entry.get("bytes"), int):
                raise RuntimeError("context cache summary entry is malformed")


class ContextLedger:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.path = cache_dir(self.root) / "context-ledger.json"
        self.lock = FileLock(cache_dir(self.root) / ".context-ledger.lock", "context-ledger")

    def load(self):
        if not self.path.exists():
            return {"schema": SCHEMA, "sources": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError(f"context cache corruption: {exc}") from exc
        _validate(data)
        return data

    def _save(self, data):
        _validate(data)
        atomic_write_json(self.path, data, mode=0o600)

    def _confined(self, path: Path) -> Path:
        return confined_path(self.root, path, must_exist=True, reject_symlinks=False)

    def _consistent_digest(self, path: Path) -> tuple[str, dict]:
        for _ in range(3):
            before = _stat_identity(path)
            digest = sha256_file(path) if not path.is_symlink() else hashlib.sha256(str(path.readlink()).encode()).hexdigest()
            middle = _stat_identity(path)
            if before != middle:
                continue
            confirm = sha256_file(path) if not path.is_symlink() else hashlib.sha256(str(path.readlink()).encode()).hexdigest()
            after = _stat_identity(path)
            if middle == after and digest == confirm:
                return digest, after
        raise RuntimeError(f"context source changed while it was being fingerprinted: {path}")

    def record_file(self, path: Path, conclusion: str = "", dependencies: Iterable[Path] = ()):
        path = self._confined(path)
        dependency_records = []
        for dependency in dependencies:
            dependency = self._confined(dependency)
            digest, identity = self._consistent_digest(dependency)
            dependency_records.append({"path": str(dependency), "sha256": digest, "stat": identity})
        digest, identity = self._consistent_digest(path)
        self.lock.acquire()
        try:
            commit_digest, commit_identity = self._consistent_digest(path)
            if commit_identity != identity or commit_digest != digest:
                raise RuntimeError(f"context source changed before cache commit: {path}")
            for dependency in dependency_records:
                dependency_path = Path(dependency["path"])
                current_digest, current_identity = self._consistent_digest(dependency_path)
                if current_digest != dependency["sha256"] or current_identity != dependency["stat"]:
                    raise RuntimeError(f"context dependency changed before cache commit: {dependency_path}")
            data = self.load()
            key = str(path)
            data["sources"][key] = {
                "kind": "file",
                "path": key,
                "sha256": digest,
                "stat": identity,
                "dependencies": dependency_records,
                "conclusion": redact_text(conclusion),
                "recorded_at": _now().isoformat(),
            }
            self._save(data)
            return data["sources"][key]
        finally:
            self.lock.release()

    def check_file(self, path: Path):
        path = confined_path(self.root, path, reject_symlinks=False)
        data = self.load()
        old = data["sources"].get(str(path))
        if not old:
            return {"known": False, "fresh": False}
        if not path.exists() and not path.is_symlink():
            return {"known": True, "fresh": False, "reason": "missing"}
        current_stat = _stat_identity(path)
        dependencies_fresh = True
        for dependency in old.get("dependencies", []):
            dep_path = confined_path(self.root, dependency["path"], reject_symlinks=False)
            if not dep_path.exists() or _stat_identity(dep_path) != dependency.get("stat"):
                dependencies_fresh = False
                break
        if current_stat == old.get("stat") and dependencies_fresh:
            return {
                "known": True,
                "fresh": True,
                "sha256": old["sha256"],
                "previous": old["sha256"],
                "conclusion": old.get("conclusion", ""),
                "verified_by": "unchanged-filesystem-identity",
            }
        digest, _ = self._consistent_digest(path)
        return {
            "known": True,
            "fresh": digest == old.get("sha256") and dependencies_fresh,
            "sha256": digest,
            "previous": old.get("sha256"),
            "dependencies_fresh": dependencies_fresh,
            "conclusion": old.get("conclusion", ""),
            "verified_by": "content-hash",
        }

    def record_external(
        self,
        source: str,
        fingerprint: str,
        conclusion: str = "",
        ttl_seconds: int | float | None = None,
        *,
        provenance: str = "caller-asserted",
    ):
        if not isinstance(source, str) or not source.strip() or not isinstance(fingerprint, str) or not fingerprint.strip():
            raise ValueError("external source and fingerprint are required")
        if ttl_seconds is not None and (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds < 0
        ):
            raise ValueError("external TTL must be finite and non-negative")
        if provenance not in {"caller-asserted", "http-etag", "content-sha256", "versioned-source"}:
            raise ValueError("unsupported external fingerprint provenance")
        current = _now()
        valid_until = (current + timedelta(seconds=ttl_seconds)).isoformat() if ttl_seconds is not None else None
        key = _source_key(source)
        self.lock.acquire()
        try:
            data = self.load()
            data["sources"][key] = {
                "kind": "external",
                "source": source,
                "fingerprint": fingerprint,
                "provenance": provenance,
                "verified": provenance != "caller-asserted",
                "conclusion": redact_text(conclusion),
                "recorded_at": current.isoformat(),
                "valid_until": valid_until,
            }
            self._save(data)
            return data["sources"][key]
        finally:
            self.lock.release()

    def check_external(self, source: str, fingerprint: str | None = None):
        old = self.load()["sources"].get(_source_key(source))
        if not old or old.get("source") != source:
            return {"known": False, "fresh": False}
        until = _parse(old.get("valid_until"))
        ttl_fresh = until is None or _now() <= until
        fingerprint_fresh = fingerprint is not None and fingerprint == old.get("fingerprint")
        if fingerprint is None:
            fresh = until is not None and ttl_fresh and bool(old.get("verified"))
            reason = "ttl+verified-provenance" if fresh else "fingerprint required or unverified provenance"
        else:
            fresh = fingerprint_fresh and ttl_fresh
            reason = "fingerprint+ttl" if fresh else "fingerprint mismatch or expired ttl"
        return {
            "known": True,
            "fresh": fresh,
            "reason": reason,
            "previous": old.get("fingerprint"),
            "valid_until": old.get("valid_until"),
            "verified_provenance": bool(old.get("verified")),
            "conclusion": old.get("conclusion", ""),
        }

    def record_log_summary(self, source: str, raw: bytes, failure_region: str = "") -> dict:
        digest = hashlib.sha256(raw).hexdigest()
        summary = {
            "kind": "summary",
            "source": source,
            "sha256": digest,
            "bytes": len(raw),
            "failure_region": redact_text(failure_region[-4000:]),
            "recorded_at": _now().isoformat(),
        }
        self.lock.acquire()
        try:
            data = self.load()
            data["sources"]["summary:" + hashlib.sha256(source.encode()).hexdigest()] = summary
            self._save(data)
            return summary
        finally:
            self.lock.release()
