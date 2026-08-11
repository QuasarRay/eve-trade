"""Dependency-free exhaustive companion to the Hypothesis scheduling suite."""
from __future__ import annotations

from itertools import product

from workflow_model import (
    NEW,
    NEW_PATH,
    ORIGINAL,
    PRODUCER_JOBS,
    executable_runs_in_workflow,
    new_release_allowed,
    release_allowed,
    root_jobs,
    would_run,
)


RESULTS = ("success", "failure", "cancelled", "skipped")
checks = 0

# The aggregate is an always-run diagnosis/gate for every producer outcome pattern.
for values in product(RESULTS, repeat=len(PRODUCER_JOBS)):
    outcomes = dict(zip(PRODUCER_JOBS, values))
    assert would_run(ORIGINAL, "o11y-aggregate", outcomes)
    assert would_run(NEW, "o11y-aggregate", outcomes)
    checks += 2

# Terraform's three matrix children cannot cancel one another.
assert NEW["terraform"].matrix_fail_fast is False
assert set(NEW["terraform"].matrix_rows) == {"eks", "gke", "talos-omni"}
checks += 2

# Go's protobuf dependency is preserved exactly.
for result in RESULTS:
    outcomes = {"proto": result}
    assert would_run(NEW, "go", outcomes) == would_run(ORIGINAL, "go", outcomes)
    checks += 1

# E2E gating is identical for every original product prerequisite result.
assert NEW["e2e"].needs == ORIGINAL["e2e"].needs
for values in product(RESULTS, repeat=len(NEW["e2e"].needs)):
    outcomes = dict(zip(NEW["e2e"].needs, values))
    assert would_run(NEW, "e2e", outcomes) == would_run(ORIGINAL, "e2e", outcomes)
    checks += 1

# Release remains a strict subset of the original gate.
for event, ref, reliability, e2e, o11y, same in product(
    ("push", "pull_request", "workflow_dispatch"),
    ("refs/heads/main", "refs/heads/experimental", "refs/pull/1/merge"),
    RESULTS,
    RESULTS,
    RESULTS,
    (False, True),
):
    sha = "a" * 40
    verified = sha if same else "b" * 40
    original = release_allowed(event, ref, sha, e2e, o11y, verified)
    actual = new_release_allowed(event, ref, sha, reliability, e2e, o11y, verified)
    assert actual == (reliability == "success" and original)
    assert not actual or original
    checks += 2

# Reliability infrastructure failure cannot suppress product E2E diagnostics.
assert "reliability-contract" not in NEW["e2e"].needs
for reliability in ("failure", "cancelled", "skipped"):
    outcomes = {dependency: "success" for dependency in NEW["e2e"].needs}
    outcomes["reliability-contract"] = reliability
    assert would_run(NEW, "e2e", outcomes)
    checks += 1

# Timeouts are preserved/tightened and the additional root is intentional.
for name in set(ORIGINAL) & set(NEW):
    assert NEW[name].timeout <= ORIGINAL[name].timeout
    checks += 1
assert root_jobs(ORIGINAL) < root_jobs(NEW)
assert root_jobs(NEW) - root_jobs(ORIGINAL) == {"reliability-contract"}
checks += 2

runs = executable_runs_in_workflow(NEW_PATH.read_text(encoding="utf-8"))
assert runs.count("|") == 1
assert runs and all(
    run == "|"
    or run == "python3 .github/dagger/_bootstrap.py"
    or (
        run.startswith(".dagger-ci-venv/bin/python .github/dagger/")
        and (run.endswith(".py") or run.endswith(".py build") or run.endswith(".py verify"))
    )
    for run in runs
)
checks += 2

print(f"exhaustive reliability model passed: {checks:,} assertions")
