from __future__ import annotations

import re

from helpers.paths import E2E_ROOT, REPO_ROOT


def test_lifecycle_coverage_matrix_mentions_every_canonical_step() -> None:
    lifecycle = (REPO_ROOT / "Architecture" / "Trade Request Lifecycle" / "v1.md").read_text(
        encoding="utf-8"
    )
    matrix = (E2E_ROOT / "docs" / "coverage_matrix.md").read_text(encoding="utf-8")

    lifecycle_steps = {
        match.strip()
        for match in re.findall(r"(@[^\n=]+?)\s*=>", lifecycle)
        if match.strip().startswith("@")
    }
    assert lifecycle_steps, "canonical lifecycle did not yield any @ steps"

    missing = sorted(step for step in lifecycle_steps if step not in matrix)
    assert not missing, "coverage matrix is missing lifecycle steps: " + repr(missing)


def test_e2e_taxonomy_declares_ownership_and_review_rules() -> None:
    taxonomy = (E2E_ROOT / "docs" / "taxonomy.md").read_text(encoding="utf-8")

    for required in [
        "E2E",
        "Contract",
        "Integration Gate",
        "Chaos",
        "Reviewer Checklist",
        "EVE_TRADE_E2E_PRODUCTION_GATE=true",
    ]:
        assert required in taxonomy


def test_ci_integration_entrypoint_enforces_production_e2e_gate() -> None:
    pipeline = (REPO_ROOT / "ci-cd" / "pipeline.py").read_text(encoding="utf-8")
    gitlab = (
        REPO_ROOT / "ci-cd" / "gitlab" / "eve-trade.gitlab-ci.yml"
    ).read_text(encoding="utf-8")

    assert "pytest distributed-backend/tests/e2e -q" in pipeline
    assert '"EVE_TRADE_E2E_PRODUCTION_GATE", "true"' in pipeline
    assert "eve_trade_e2e" in pipeline
    assert "EVE_TRADE_E2E_ALLOW_SKIPS" not in gitlab
    assert "python ci-cd/pipeline.py integration" in gitlab
