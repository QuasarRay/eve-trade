from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    path = Path(__file__).with_name("catalog.json")
    return json.loads(path.read_text(encoding="utf-8"))


def proposed_names() -> set[str]:
    catalog = load_catalog()
    return {
        name
        for category in catalog["categories"].values()
        for name in category["names"]
    }


def existing_names() -> set[str]:
    return set(load_catalog()["existing"])


def category_for(name: str) -> int | None:
    for key, category in load_catalog()["categories"].items():
        if name in category["names"]:
            return int(key)
    return None
