# Dagger CI reliability contract

GitHub Actions owns scheduling, permissions, timeouts, independent runner failure domains, secrets, durable artifacts, and the isolated registry push. Dagger owns build, test, scan, E2E, aggregate, and release artifact verification.

The integration deliberately keeps repository-native evidence outside Dagger's supplementary stage summaries:

1. each logical producer creates a current-run start marker;
2. `ci_evidence.py run` observes the actual Dagger launcher command and redacts its output;
3. an `always()` finalizer signs and uploads the producer bundle;
4. `o11y-aggregate` downloads those artifacts and fails closed on missing, corrupt, stale, or incomplete evidence;
5. `reliability-contract` remains an independent release gate and is not invented as an observability producer.

Supply-chain boundaries are explicit: GitHub actions use immutable commits, checkout credentials are not persisted, the Dagger SDK is installed from a complete hash lock, the CLI archive and extracted binary are checksummed, the Dagger engine and stage images use OCI digests, and downloaded Kind/kubectl executables are checksummed. E2E uses the local collector and receives no telemetry credentials. Release images are built locally by an uncredentialed Dagger stage; only a dedicated inline workflow step receives the registry token and pipes it into a digest-pinned, source-free Docker CLI publisher. The host step unsets the token before copying the resulting immutable image lock to a separate hash-locked Dagger verifier.

Before aggregation, `_validate_evidence.py` independently requires every producer envelope to contain a complete collector marker, canonical digest, and workflow-bound signature. Missing either integrity field is a hard failure even if the repository aggregator would otherwise accept it.

Terraform keeps three non-cancelling matrix children (`fail-fast: false`). Go is split into evidenced Dagger phases so the aggregate has direct passing observations for tests, the race detector, and the vulnerability audit.

Local dependency-free contract:

```bash
python3 .github/dagger/tests/static_contract.py
```

Full reliability suite (requires the pinned test dependencies):

```bash
PYTHONPATH=.github/dagger/tests python3 -m pytest -q .github/dagger/tests
PYTHONPATH=.github/dagger/tests python3 .github/dagger/tests/run_exhaustive_reliability_model.py
```

Full stage execution requires Linux x86_64, CPython 3.12 for the host bootstrap, and a running Docker-compatible container runtime.
