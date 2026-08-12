from __future__ import annotations

FAULT_CATEGORIES = {24, 36, 44, 46, 54, 64, 66, 101}
EDGE_CATEGORIES = {3, 4, 5, 6, 7, 28, 29, 30, 31, 48, 61, 76, 78, 79, 86}
TRADE_CATEGORIES = {
    8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23,
    32, 33, 41, 42, 43, 47, 58, 59, 60, 62, 63, 82, 83, 87, 94, 95, 96, 97,
}
REPO_CATEGORIES = {
    25, 26, 27, 34, 35, 37, 38, 45, 49, 50, 51, 52, 53, 55, 56, 57,
    65, 67, 68, 69, 70, 71, 72, 73, 74, 75, 77, 80, 81, 84, 85, 88, 89,
    90, 91, 92, 93, 98, 99, 100,
}


def runner_for(category: int) -> str:
    if category in FAULT_CATEGORIES:
        return "fault"
    if category in EDGE_CATEGORIES:
        return "edge"
    if category in TRADE_CATEGORIES:
        return "trade"
    if category in REPO_CATEGORIES:
        return "repo"
    raise KeyError(f"no runner assigned to category {category}")
