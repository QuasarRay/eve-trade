#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eve_trade_hypothesis.catalog import load_catalog  # noqa: E402
from eve_trade_hypothesis.evidence_specs import build_evidence_spec, spec_to_dict  # noqa: E402
from eve_trade_hypothesis.semantic_overrides import SEMANTIC_OVERRIDE_FINDINGS  # noqa: E402


def main() -> int:
    catalog = load_catalog()
    out = {}
    for category_id, category in catalog["categories"].items():
        for name in category["names"]:
            spec = spec_to_dict(build_evidence_spec(int(category_id), name))
            if name in SEMANTIC_OVERRIDE_FINDINGS:
                spec["audited_direct_override_findings"] = list(SEMANTIC_OVERRIDE_FINDINGS[name])
                spec["requires_semantic_validation"] = True
            out[name] = spec
    path = ROOT / "evidence_specs.json"
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} evidence specs to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
