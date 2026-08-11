from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Mapping


SECRET_NAME = re.compile(r"(?i)(?:^|_)(?:api[_-]?key|token|secret|password|passwd|private[_-]?key|credential)(?:$|_)")
TEST_DETECTION_NAME = re.compile(
    r"(?i)(?:pytest|unittest|test_current|test_name|law_name|expected_(?:test|law)|(?:test|law)_expected)"
)
SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)(\b(?:api[_-]?key|token|secret|password|passwd)\b\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]


class SecurityError(RuntimeError):
    pass


def is_path_redirect(path: Path) -> bool:
    """Return true for symlinks and Windows reparse-point redirections.

    ``Path.is_symlink()`` is false for Windows directory junctions.  Treat every
    reparse point as a redirect at sensitive control-path boundaries instead of
    trying to maintain an incomplete allow-list of reparse tags.
    """

    # ``Path.is_symlink`` is portable, recognizes dangling links, and keeps
    # this guard usable with path-like control-file adapters.  Only Windows
    # needs the lower-level attribute check for directory junctions, which
    # ``Path.is_symlink`` deliberately does not classify as symlinks.
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_attribute)


def redact_text(value: str) -> str:
    text = value
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: match.group(1) + "[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


def redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in values.items():
        if SECRET_NAME.search(str(key)):
            out[str(key)] = "[REDACTED]"
        elif isinstance(value, str):
            out[str(key)] = redact_text(value)
        else:
            out[str(key)] = value
    return out


def minimal_subprocess_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    allow = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allow and not SECRET_NAME.search(key)}
    if extra:
        for key, value in extra.items():
            if SECRET_NAME.search(key):
                raise SecurityError(f"refusing to propagate secret-like environment variable: {key}")
            if TEST_DETECTION_NAME.search(key):
                continue
            env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("UNITTEST_CURRENT_TEST", None)
    return env


def confined_path(
    root: Path,
    value: str | Path,
    *,
    must_exist: bool = False,
    reject_symlinks: bool = True,
    allow_root: bool = False,
) -> Path:
    supplied_root = Path(root).absolute()
    root = Path(root).resolve(strict=True)
    raw = Path(value)
    if ".." in raw.parts:
        raise SecurityError(f"parent traversal is not allowed: {value}")
    candidate = raw if raw.is_absolute() else supplied_root / raw
    if reject_symlinks:
        try:
            lexical_relative = candidate.absolute().relative_to(supplied_root)
        except ValueError:
            lexical_relative = None
        if lexical_relative is not None:
            current = supplied_root
            for part in lexical_relative.parts:
                current = current / part
                if is_path_redirect(current):
                    raise SecurityError(f"redirected control path is not allowed: {current}")
    absolute = candidate.parent.resolve(strict=False) / candidate.name
    try:
        rel = absolute.relative_to(root)
    except ValueError as exc:
        raise SecurityError(f"path escapes root {root}: {value}") from exc
    if not allow_root and not rel.parts:
        raise SecurityError("root itself is not a valid confined child path")
    current = root
    for part in rel.parts:
        if part in {"", ".", ".."}:
            raise SecurityError(f"non-canonical path component: {part!r}")
        current = current / part
        if reject_symlinks and is_path_redirect(current):
            raise SecurityError(f"redirected control path is not allowed: {current}")
    if must_exist and not absolute.exists():
        raise SecurityError(f"required path does not exist: {absolute}")
    if absolute.exists():
        resolved = absolute.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise SecurityError(f"resolved path escapes root {root}: {value}") from exc
    return absolute


def ensure_private_control_file(path: Path) -> None:
    if is_path_redirect(path):
        raise SecurityError(f"sensitive control file is redirected: {path}")
    if os.name != "nt" and path.exists():
        mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
        if mode & 0o022:
            raise SecurityError(f"sensitive control file is group/world writable: {path} mode={mode:o}")
