"""Hash-locked Dagger bootstrap for GitHub's Ubuntu 24.04 x64 runner."""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request


DAGGER_VERSION = "0.21.8"
DAGGER_ARCHIVE_SHA256 = "53e226c7da8fb75171e58c35759d736d961ce8b3a12db0baa7b7107954fccc5a"
DAGGER_BINARY_SHA256 = "c6d08ba2edf34583844eecbf3ae0897127c0cc8377d151da56c66997cb503db2"
DAGGER_ENGINE = "registry.dagger.io/engine:v0.21.8@sha256:c9c1a0a6546380983d42e8d75adde070a2a0935c54b498d8bc9045d9cb2ee336"
DAGGER_ARCHIVE_URL = (
    "https://github.com/dagger/dagger/releases/download/"
    f"v{DAGGER_VERSION}/dagger_v{DAGGER_VERSION}_linux_amd64.tar.gz"
)
SDK_DISTRIBUTION = "dagger-io"
ROOT = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
VENV = ROOT / ".dagger-ci-venv"
BIN_DIR = ROOT / ".dagger-ci-bin"
SDK_LOCK = ROOT / ".github" / "dagger" / "requirements-bootstrap.txt"
MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024


def _run(*args: str, **kwargs: object) -> None:
    subprocess.run(args, check=True, **kwargs)


def _retry_run(*args: str, attempts: int = 3, **kwargs: object) -> None:
    for attempt in range(1, attempts + 1):
        try:
            _run(*args, **kwargs)
            return
        except (OSError, subprocess.CalledProcessError):
            if attempt == attempts:
                raise
            time.sleep(attempt * 2)


def _download_with_retries(url: str, path: Path, attempts: int = 3) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "eve-trade-ci-bootstrap/1"})
    for attempt in range(1, attempts + 1):
        path.unlink(missing_ok=True)
        try:
            total = 0
            with urllib.request.urlopen(request, timeout=60) as response, path.open("xb") as stream:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("Dagger CLI archive exceeds the bootstrap size limit")
                    stream.write(chunk)
            return
        except (OSError, RuntimeError, urllib.error.URLError):
            path.unlink(missing_ok=True)
            if attempt == attempts:
                raise
            time.sleep(attempt * 2)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_platform() -> None:
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise RuntimeError(
            "hash-locked Dagger bootstrap requires CPython 3.12; "
            f"got {platform.python_implementation()} {platform.python_version()}"
        )
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise RuntimeError(
            "hash-locked Dagger bootstrap supports only GitHub Ubuntu x64; "
            f"got {platform.system()} {platform.machine()}"
        )


def _inside_managed_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def _installed_sdk_version() -> str | None:
    try:
        return importlib.metadata.version(SDK_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return None


def _install_locked_sdk(python: Path) -> None:
    if not SDK_LOCK.is_file():
        raise RuntimeError(f"Dagger SDK lock file is missing: {SDK_LOCK}")
    _retry_run(
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--require-hashes",
        "--only-binary=:all:",
        "--requirement",
        str(SDK_LOCK),
    )


def ensure_sdk() -> None:
    if _inside_managed_venv():
        if _installed_sdk_version() != DAGGER_VERSION:
            _install_locked_sdk(Path(sys.executable))
        if _installed_sdk_version() != DAGGER_VERSION:
            raise RuntimeError("hash-locked Dagger Python SDK installation failed")
        return

    venv_python = VENV / "bin" / "python"
    if not venv_python.is_file():
        _run(sys.executable, "-m", "venv", str(VENV))
    _install_locked_sdk(venv_python)
    os.execve(str(venv_python), [str(venv_python), *sys.argv], os.environ.copy())


def _dagger_version(path: Path) -> str | None:
    try:
        result = subprocess.run(
            [str(path), "version"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def _install_cli(target: Path) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dagger-download-", dir=ROOT) as temp_dir:
        archive_path = Path(temp_dir) / "dagger.tar.gz"
        candidate = Path(temp_dir) / "dagger"
        _download_with_retries(DAGGER_ARCHIVE_URL, archive_path)
        if _sha256(archive_path) != DAGGER_ARCHIVE_SHA256:
            raise RuntimeError("Dagger CLI archive checksum mismatch")
        with tarfile.open(archive_path, "r:gz") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.isfile() and Path(member.name).name == "dagger"
            ]
            if len(members) != 1:
                raise RuntimeError("Dagger CLI archive has an unexpected layout")
            source = archive.extractfile(members[0])
            if source is None:
                raise RuntimeError("Dagger CLI archive contains no executable data")
            with candidate.open("xb") as stream:
                while chunk := source.read(1024 * 1024):
                    stream.write(chunk)
        if _sha256(candidate) != DAGGER_BINARY_SHA256:
            raise RuntimeError("extracted Dagger CLI checksum mismatch")
        candidate.chmod(0o755)
        os.replace(candidate, target)


def ensure_cli() -> Path:
    target = BIN_DIR / "dagger"
    if not target.is_file() or _sha256(target) != DAGGER_BINARY_SHA256:
        _install_cli(target)
    version = _dagger_version(target)
    if not version or f"dagger v{DAGGER_VERSION}" not in version:
        raise RuntimeError(f"unexpected Dagger CLI version: {version!r}")
    os.environ["PATH"] = f"{BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["_EXPERIMENTAL_DAGGER_RUNNER_HOST"] = f"image://{DAGGER_ENGINE}"
    return target


def bootstrap() -> None:
    ensure_platform()
    ensure_sdk()
    ensure_cli()


if __name__ == "__main__":
    bootstrap()
