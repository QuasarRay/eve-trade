#!/usr/bin/env python
"""Download local simulator dependencies with bounded fallback attempts."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def run_attempt(command: list[str], timeout_seconds: int) -> bool:
    print("+", " ".join(command), flush=True)
    try:
        subprocess.run(command, check=True, timeout=timeout_seconds)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"attempt failed: {exc}", flush=True)
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--wheel-dir", default=".downloads/python-wheels")
    args = parser.parse_args()

    wheel_dir = Path(args.wheel_dir)
    wheel_dir.mkdir(parents=True, exist_ok=True)

    pip_attempts = [
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "-r",
            "simulator/requirements.txt",
            "-d",
            str(wheel_dir),
        ],
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--prefer-binary",
            "-r",
            "simulator/requirements.txt",
            "-d",
            str(wheel_dir),
        ],
    ]
    for command in pip_attempts:
        if run_attempt(command, args.timeout):
            break
    else:
        return 1

    docker_attempts = [
        ["docker", "pull", "ghcr.io/embarkstudios/quilkin:0.9.0"],
        ["docker", "pull", "us-docker.pkg.dev/quilkin/release/quilkin:0.1.0"],
    ]
    for command in docker_attempts:
        if run_attempt(command, args.timeout):
            return 0

    print(
        "Quilkin image download failed; use scripts/quilkin_udp_fallback.py for local UDP proxy fallback.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
