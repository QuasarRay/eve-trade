"""Portable local artifact storage with optional S3-compatible upload."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .redaction import redact_text


class RunStorage:
    def __init__(self, run_dir: Path, *, strict: bool = False) -> None:
        self.run_dir = run_dir.resolve()
        self.strict = strict
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def path(self, relative: str | Path) -> Path:
        candidate = (self.run_dir / relative).resolve()
        if candidate != self.run_dir and self.run_dir not in candidate.parents:
            raise ValueError(f"artifact path escapes run directory: {relative}")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate

    def write_text(self, relative: str | Path, text: str) -> Path:
        target = self.path(relative)
        self._atomic_write(target, text.encode("utf-8"))
        return target

    def write_json(self, relative: str | Path, value: Any) -> Path:
        return self.write_text(relative, json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")

    def copy(self, source: Path, relative: str | Path) -> Path:
        target = self.path(relative)
        if source.resolve() == target.resolve():
            return target
        shutil.copy2(source, target)
        return target

    def bundle(self, destination: Path | None = None) -> Path:
        destination = destination or self.run_dir.with_suffix(".zip")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(self.run_dir.rglob("*")):
                if item.is_file():
                    archive.write(item, item.relative_to(self.run_dir.parent))
        return destination

    def upload_optional(self, bundle: Path | None = None) -> str | None:
        backend = os.getenv("OBS_STORAGE_BACKEND", "local").strip().lower()
        if backend in ("", "local"):
            return None
        if backend != "s3":
            return self._fail_or_none(f"unsupported OBS_STORAGE_BACKEND={backend}")
        bucket = os.getenv("OBS_S3_BUCKET", "").strip()
        if not bucket:
            return self._fail_or_none("OBS_S3_BUCKET is required for S3 storage")
        try:
            import boto3  # type: ignore[import-not-found]

            artifact = bundle or self.bundle()
            prefix = os.getenv("OBS_S3_PREFIX", "eve-trade-observability").strip("/")
            key = f"{prefix}/{artifact.name}" if prefix else artifact.name
            boto3.client("s3", region_name=os.getenv("AWS_REGION") or None).upload_file(
                str(artifact), bucket, key
            )
            return f"s3://{bucket}/{key}"
        except Exception as exc:  # best-effort boundary
            return self._fail_or_none(f"S3 upload failed: {exc}")

    def _fail_or_none(self, message: str) -> None:
        message = redact_text(message)
        self.write_text("storage-error.txt", message + "\n")
        if self.strict:
            raise RuntimeError(message)
        return None

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(target)
