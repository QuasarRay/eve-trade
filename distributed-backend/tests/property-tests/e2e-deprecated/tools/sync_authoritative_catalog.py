#!/usr/bin/env python3
"""Restore generated registry identity from the authoritative Markdown catalog."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROPERTY_ROOT = ROOT.parent
CATALOG_MD = PROPERTY_ROOT / "tests-to-implement.md"
CATALOG_JSON = ROOT / "eve_trade_hypothesis" / "catalog.json"
GENERATED = ROOT / "eve_trade_hypothesis" / "generated"
OLD_REWRITE = (
    "test_issue_response_does_not_echo_client_item_type_claim_when_it_differs_"
    "from_authoritative_source_stack_item_type"
)
AUTHORITATIVE_NAME = "test_issue_rejects_item_stack_with_wrong_item_type_claim"


def _replace_json_value(value):
    if value == OLD_REWRITE:
        return AUTHORITATIVE_NAME
    if isinstance(value, list):
        return [_replace_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            (AUTHORITATIVE_NAME if key == OLD_REWRITE else key): _replace_json_value(item)
            for key, item in value.items()
        }
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    authoritative = re.findall(
        r"`(test_[a-z0-9_]+)`", CATALOG_MD.read_text(encoding="utf-8")
    )
    catalog = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    catalog = _replace_json_value(catalog)
    combined = set(catalog["existing"])
    combined.update(
        name for category in catalog["categories"].values() for name in category["names"]
    )
    if combined != set(authoritative):
        raise RuntimeError(
            "catalog repair did not restore exact identity: "
            f"missing={sorted(set(authoritative) - combined)}, "
            f"extra={sorted(combined - set(authoritative))}"
        )
    outputs: dict[Path, str] = {
        CATALOG_JSON: json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    }

    for category_key, category in catalog["categories"].items():
        path = GENERATED / f"test_category_{int(category_key):03d}.py"
        content = (
            "from eve_trade_hypothesis.registry import register_category\n\n"
            f"CATEGORY_ID = {int(category_key)}\n"
            f"CONTRACT_NAMES = {category['names']!r}\n\n"
            "register_category(globals(), CATEGORY_ID, CONTRACT_NAMES)\n"
        )
        outputs[path] = content

    for filename in ("audit_resolution_manifest.json", "semantic_override_manifest.json"):
        path = ROOT / filename
        document = _replace_json_value(json.loads(path.read_text(encoding="utf-8")))
        if filename == "audit_resolution_manifest.json":
            document["renamed_contracts"] = {}
            for record in document["records"]:
                if record["name"] == AUTHORITATIVE_NAME:
                    record["original_name"] = AUTHORITATIVE_NAME
        outputs[path] = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    stale = [str(path) for path, content in outputs.items() if not path.exists() or path.read_text(encoding="utf-8") != content]
    if args.check:
        if stale:
            raise SystemExit("authoritative catalog outputs are stale: " + ", ".join(stale))
    else:
        for path, content in outputs.items():
            path.write_text(content, encoding="utf-8")
    print(f"restored exact authoritative catalog identity for {len(authoritative)} contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
