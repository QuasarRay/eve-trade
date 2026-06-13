from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest


def service_target(env_name: str, default: str) -> str:
    import os

    value = os.environ.get(env_name) or default
    parsed = urlparse(value)
    if parsed.scheme:
        value = parsed.netloc
    else:
        value = value.rstrip("/")
    return value or default


def split_target(target: str) -> tuple[str, int]:
    if ":" not in target:
        raise ValueError(f"expected host:port target, got {target!r}")
    host, port = target.rsplit(":", 1)
    return host.strip("[]"), int(port)


def require_tcp_service(target: str, service_name: str, timeout_seconds: float = 1.5) -> None:
    try:
        host, port = split_target(target)
    except ValueError as exc:
        pytest.skip(f"{service_name} target is invalid: {exc}")
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return
    except OSError as exc:
        pytest.skip(f"{service_name} is not reachable at {target}: {exc}")
