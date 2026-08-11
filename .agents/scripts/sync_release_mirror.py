from __future__ import annotations

"""Compatibility entry point for the source-authoritative deployment builder.

The historical implementation copied active ``.agents`` back into source.
That direction is constitutionally invalid.  This command now only builds a
deterministic, human-deployable tree outside active governance.
"""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra"))

from agentinfra.release_source import build_deployment_tree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Aegis governance from authoritative source")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve(strict=True)
    destination = args.destination or root / "dist" / "aegis-governance"
    result = build_deployment_tree(root, destination)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
