"""Auditable topology constants for the GitHub/Dagger hybrid pipeline."""

TIMEOUTS = {
    "reliability-contract": 10,
    "proto": 15,
    "go": 35,
    "rust-trade-settlement": 30,
    "terraform": 20,
    "kubernetes": 15,
    "python": 20,
    "architecture": 10,
    "gui-contract": 10,
    "security": 30,
    "canonical-regression": 60,
    "e2e": 60,
    "o11y-aggregate": 10,
    "release-verification": 60,
}

TERRAFORM_PROVIDER_MATRIX = {
    "eks": "terraform_eks.py",
    "gke": "terraform_gke.py",
    "talos-omni": "terraform_talos_omni.py",
}

INDEPENDENT_ROOT_JOBS = (
    "reliability-contract",
    "proto",
    "rust-trade-settlement",
    "terraform",
    "kubernetes",
    "python",
    "architecture",
    "gui-contract",
    "security",
    "canonical-regression",
)

LOGICAL_PRODUCER_JOBS = (
    "proto",
    "go",
    "rust-trade-settlement",
    "terraform",
    "kubernetes",
    "python",
    "architecture",
    "gui-contract",
    "security",
    "canonical-regression",
    "e2e",
)

LAUNCHERS = {
    "reliability-contract": "reliability_contract.py",
    "proto": "proto.py",
    "go": "go.py <phase>",
    "rust-trade-settlement": "rust.py",
    "terraform": "matrix:terraform_{eks,gke,talos_omni}.py",
    "kubernetes": "kubernetes.py",
    "python": "python_checks.py",
    "architecture": "architecture.py",
    "gui-contract": "gui.py",
    "security": "security.py",
    "canonical-regression": "canonical_regression.py",
    "e2e": "e2e.py",
    "o11y-aggregate": "o11y.py",
    "release-verification": "release.py build -> isolated workflow publisher -> release.py verify",
}
