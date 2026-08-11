"""Dependency-free fail-closed contracts for the GitHub/Dagger integration."""
from __future__ import annotations

import json
from pathlib import Path
import re


GITHUB = Path(__file__).resolve().parents[2]
DAGGER = GITHUB / "dagger"
WORKFLOW = GITHUB / "workflows" / "verify.yaml"
text = WORKFLOW.read_text(encoding="utf-8")

checkout_pins = re.findall(r"actions/checkout@([0-9a-f]{40})", text)
assert checkout_pins, "workflow has no immutable checkout pin"
assert text.count("persist-credentials: false") == len(checkout_pins), (
    "every checkout must disable persisted credentials before .git enters Dagger"
)

assert "./.github/actions/ci-evidence" in text
assert re.search(r"actions/download-artifact@[0-9a-f]{40}", text)
assert "pattern: o11y-producer-*" in text
assert "merge-multiple: true" in text
assert "if-no-files-found: error" in (GITHUB / "actions" / "ci-evidence" / "action.yml").read_text(encoding="utf-8")
assert "include-hidden-files: true" in (GITHUB / "actions" / "ci-evidence" / "action.yml").read_text(encoding="utf-8")

o11y = text.split("  o11y-aggregate:", 1)[1].split("  release-verification:", 1)[0]
assert "if: ${{ always() }}" in o11y
assert "reliability-contract" not in o11y
assert "OBS_CI_NEEDS_JSON: ${{ toJson(needs) }}" in o11y

release = text.split("  release-verification:", 1)[1]
bootstrap_pos = release.index("python3 .github/dagger/_bootstrap.py")
build_pos = release.index(".github/dagger/release.py build")
token_pos = release.index("GHCR_TOKEN:")
verify_pos = release.index(".github/dagger/release.py verify")
assert bootstrap_pos < build_pos < token_pos < verify_pos
assert "persist-credentials: false" in release
publisher = release.split("GHCR_TOKEN:", 1)[1].split("      - id: release", 1)[0]
assert ".github/" not in publisher
assert "scripts/" not in publisher
assert publisher.count("docker push") == 3
assert "docker:27-cli@sha256:851f91d241214e7c6db86513b270d58776379aacc5eb9c4a87e5b47115e3065c" in publisher
assert publisher.rindex("unset GHCR_TOKEN") < publisher.index("install -m 0600")

release_source = (DAGGER / "release.py").read_text(encoding="utf-8")
assert "GHCR_TOKEN" not in release_source
assert "--require-hashes" in release_source
assert (DAGGER / "requirements-release.txt").is_file()

e2e = text.split("  e2e:", 1)[1].split("  o11y-aggregate:", 1)[0]
for secret_name in ("HONEYCOMB_API_KEY", "HONEYCOMB_CONFIGURATION_KEY", "SENTRY_DSN"):
    assert secret_name not in e2e
    assert secret_name not in (DAGGER / "e2e.py").read_text(encoding="utf-8")

o11y_source = (DAGGER / "o11y.py").read_text(encoding="utf-8")
assert "_validate_evidence.py" in o11y_source
assert "envelope_exit" in o11y_source
validator_source = (DAGGER / "_validate_evidence.py").read_text(encoding="utf-8")
assert '"artifact_digest"' in validator_source
assert '"signature"' in validator_source
assert "missing mandatory fields" in validator_source

bootstrap = (DAGGER / "_bootstrap.py").read_text(encoding="utf-8")
assert "install.sh" not in bootstrap
assert "--require-hashes" in bootstrap
assert re.search(r'DAGGER_ARCHIVE_SHA256\s*=\s*"[0-9a-f]{64}"', bootstrap)
assert re.search(r'DAGGER_BINARY_SHA256\s*=\s*"[0-9a-f]{64}"', bootstrap)
assert re.search(r'DAGGER_ENGINE\s*=\s*"[^\"]+@sha256:[0-9a-f]{64}"', bootstrap)
assert (DAGGER / "requirements-bootstrap.txt").is_file()
assert not (DAGGER / "_generate_evidence.py").exists()

policy = json.loads((GITHUB / "action-runtime-policy.json").read_text(encoding="utf-8"))
for pin in set(re.findall(r"(?:actions/(?:checkout|upload-artifact|download-artifact))@([0-9a-f]{40})", text)):
    assert pin in policy, f"missing reviewed runtime policy for action pin {pin}"
    assert policy[pin]["runtime"] in {"node20", "node24"}
    assert policy[pin]["supported_on_ubuntu_24_04"] is True

for stale in (
    "EXHAUSTIVE_EXECUTION.txt",
    "FINAL_VALIDATION.txt",
    "HYPOTHESIS_EXECUTION.txt",
    "RELIABILITY_REPORT.md",
    "STATIC_EXECUTION.txt",
):
    assert not (DAGGER / "tests" / stale).exists(), f"stale bundled proof must not be committed: {stale}"

print("static reliability and supply-chain contract OK")
