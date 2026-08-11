"""Canonical 200 regression stage that exports diagnostics before propagating failure."""
from __future__ import annotations
from _common import REPO_ROOT, StageFailure, sh, stage, with_env, with_source
from _images import GOLANG_IMAGE, POSTGRES_IMAGE, PYTHON_BOOKWORM_IMAGE, RUST_IMAGE

DB_URL = "postgresql://eve:eve-test@postgres:5432/eve_trade_test?sslmode=disable"

async def run(dag):
    db = (dag.container().from_(POSTGRES_IMAGE)
          .with_env_variable("POSTGRES_USER", "eve")
          .with_env_variable("POSTGRES_PASSWORD", "eve-test")
          .with_env_variable("POSTGRES_DB", "eve_trade_test")
          .with_exposed_port(5432).as_service())
    go_toolchain = dag.container().from_(GOLANG_IMAGE)
    rust_toolchain = dag.container().from_(RUST_IMAGE)
    ctr = dag.container().from_(PYTHON_BOOKWORM_IMAGE)
    ctr = (ctr.with_directory("/usr/local/go", go_toolchain.directory("/usr/local/go"))
           .with_directory("/usr/local/cargo", rust_toolchain.directory("/usr/local/cargo"))
           .with_directory("/usr/local/rustup", rust_toolchain.directory("/usr/local/rustup"))
           .with_env_variable("CARGO_HOME", "/usr/local/cargo")
           .with_env_variable("RUSTUP_HOME", "/usr/local/rustup")
           .with_env_variable("PATH", "/usr/local/go/bin:/usr/local/cargo/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"))
    ctr = sh(ctr, ["apt-get update", "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends protobuf-compiler git ca-certificates", "rm -rf /var/lib/apt/lists/*"])
    ctr = with_source(ctr, dag, include_git=True).with_service_binding("postgres", db)
    ctr = with_env(ctr, {"EVE_TRADE_TEST_DATABASE_URL": DB_URL, "ENCORERUNTIME_NOPANIC": "1", "RUST_BACKTRACE": "1"})
    ctr = sh(ctr, [
        "python -m pip install --disable-pip-version-check -r simulator/requirements-test.txt -r distributed-backend/observability/requirements-test.txt -r distributed-backend/tests/e2e/requirements.txt",
        "mkdir -p artifacts/canonical-regressions",
        "set +e; python scripts/run_canonical_regressions.py --output-dir artifacts/canonical-regressions; rc=$?; set -e; printf '%s\\n' \"$rc\" > artifacts/canonical-regressions/.ci-exit-code; exit 0",
    ])
    await ctr.sync()
    await ctr.directory("/src/artifacts/canonical-regressions").export(str(REPO_ROOT / "artifacts" / "canonical-regressions"))
    rc = int((await ctr.file("/src/artifacts/canonical-regressions/.ci-exit-code").contents()).strip())
    report_root = REPO_ROOT / "artifacts" / "canonical-regressions"
    reports = sorted(str(path.relative_to(REPO_ROOT)) for path in report_root.rglob("*") if path.is_file())
    payload = {"report_dir": "artifacts/canonical-regressions", "report_files": reports[:200], "exit_code": rc}
    if rc:
        raise StageFailure(f"canonical regression suite failed with exit code {rc}; reports were exported", payload=payload)
    return payload

if __name__ == "__main__":
    stage("canonical-regression", run)
