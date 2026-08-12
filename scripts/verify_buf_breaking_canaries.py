#!/usr/bin/env python3
"""Prove the pinned Buf policy rejects representative wire-breaking changes."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Sequence


BASELINE_PROTO = """syntax = \"proto3\";
package eve.breaking_canary.v1;

message TradeRecord {
  string trade_id = 1;
  int64 quantity = 2;
}

enum TradeState {
  TRADE_STATE_UNSPECIFIED = 0;
  TRADE_STATE_OPEN = 1;
}
"""

BREAKING_CASES = {
    "changed_field_number": BASELINE_PROTO.replace("trade_id = 1", "trade_id = 3"),
    "incompatible_field_type": BASELINE_PROTO.replace("int64 quantity = 2", "string quantity = 2"),
    "removed_enum_value": BASELINE_PROTO.replace("  TRADE_STATE_OPEN = 1;\n", ""),
    "removed_field": BASELINE_PROTO.replace("  string trade_id = 1;\n", ""),
}

BUF_CONFIG = """version: v2
modules:
  - path: proto
breaking:
  use:
    - FILE
"""


def _write_module(directory: Path, proto: str) -> None:
    proto_directory = directory / "proto" / "eve" / "breaking_canary" / "v1"
    proto_directory.mkdir(parents=True)
    (directory / "buf.yaml").write_text(BUF_CONFIG, encoding="utf-8")
    (proto_directory / "canary.proto").write_text(proto, encoding="utf-8")


def verify_breaking_canaries(
    *,
    buf: str = "buf",
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[dict[str, object], ...]:
    with tempfile.TemporaryDirectory(prefix="eve-trade-buf-breaking-") as temporary:
        root = Path(temporary)
        baseline = root / "baseline"
        _write_module(baseline, BASELINE_PROTO)
        command_env = dict(os.environ)
        command_env.update(
            {
                "BUF_CACHE_DIR": str(root / "cache"),
                "BUF_CONFIG_DIR": str(root / "config"),
                "BUF_DATA_DIR": str(root / "data"),
            }
        )

        control = runner(
            [buf, "breaking", str(baseline), "--against", str(baseline)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=command_env,
        )
        if control.returncode != 0:
            raise RuntimeError(
                "Buf breaking control failed before mutation canaries: "
                + (control.stderr or control.stdout)
            )

        observations: list[dict[str, object]] = []
        for case, candidate_proto in BREAKING_CASES.items():
            candidate = root / case
            _write_module(candidate, candidate_proto)
            build = runner(
                [buf, "build", str(candidate)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=command_env,
            )
            if build.returncode != 0:
                raise RuntimeError(
                    f"Buf canary {case!r} is not a valid candidate schema: "
                    + (build.stderr or build.stdout)
                )
            result = runner(
                [buf, "breaking", str(candidate), "--against", str(baseline)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=command_env,
            )
            if result.returncode == 0:
                raise AssertionError(f"Buf accepted breaking canary {case!r}")
            observations.append(
                {
                    "case": case,
                    "candidate_build_exit_code": build.returncode,
                    "breaking_exit_code": result.returncode,
                }
            )
        return tuple(observations)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--buf", default="buf")
    args = parser.parse_args(argv)
    observations = verify_breaking_canaries(buf=args.buf)
    print(json.dumps({"canaries": observations}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
