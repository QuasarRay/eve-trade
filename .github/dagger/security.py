"""Independent dependency/source security gates."""
from __future__ import annotations

from _common import sh, stage, with_source
from _images import GOLANG_IMAGE, PYTHON_SLIM_IMAGE, RUST_IMAGE, TRIVY_IMAGE


async def run(dag) -> None:
    import anyio

    async def go_audit() -> None:
        ctr = with_source(dag.container().from_(GOLANG_IMAGE), dag)
        ctr = sh(
            ctr,
            [
                "go version",
                "go install golang.org/x/vuln/cmd/govulncheck@v1.5.0",
                "govulncheck -version",
                "govulncheck ./...",
            ],
        )
        await ctr.sync()

    async def rust_audit() -> None:
        ctr = with_source(dag.container().from_(RUST_IMAGE), dag)
        ctr = ctr.with_workdir("/src/distributed-backend/src/trade-settlement")
        ctr = sh(
            ctr,
            [
                "cargo install cargo-audit --locked --version 0.22.2",
                # RUSTSEC-2023-0071 has no fixed release and rsa is retained only
                # by SQLx's inactive MySQL lockfile branch in the current repo.
                "cargo audit --deny warnings --ignore RUSTSEC-2023-0071",
            ],
        )
        await ctr.sync()

    async def python_audit() -> None:
        ctr = with_source(dag.container().from_(PYTHON_SLIM_IMAGE), dag)
        ctr = sh(
            ctr,
            [
                "python -m pip install --disable-pip-version-check 'pip-audit==2.10.1'",
                "pip-audit --requirement simulator/requirements.txt",
                "pip-audit --requirement simulator/requirements-test.txt",
                "pip-audit --requirement distributed-backend/tests/e2e/requirements.txt",
                "pip-audit --requirement distributed-backend/observability/requirements.txt",
                "pip-audit --requirement distributed-backend/observability/requirements-test.txt",
            ],
        )
        await ctr.sync()

    async def trivy_fs() -> None:
        ctr = dag.container().from_(TRIVY_IMAGE).with_entrypoint([])
        ctr = with_source(ctr, dag)
        ctr = ctr.with_exec(
            [
                "trivy",
                "fs",
                "--scanners",
                "vuln,secret,misconfig",
                "--severity",
                "HIGH,CRITICAL",
                "--ignore-unfixed",
                "--ignorefile",
                ".trivyignore.yaml",
                "--show-suppressed",
                "--exit-code",
                "1",
                "--no-progress",
                ".",
            ]
        )
        await ctr.sync()

    failures = {}

    async def guarded(name, fn):
        try:
            await fn()
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"

    async with anyio.create_task_group() as tg:
        tg.start_soon(guarded, "go-audit", go_audit)
        tg.start_soon(guarded, "rust-audit", rust_audit)
        tg.start_soon(guarded, "python-audit", python_audit)
        tg.start_soon(guarded, "trivy-fs", trivy_fs)

    if failures:
        raise RuntimeError("security subcheck failures: " + repr(failures))
    return {"subchecks": ["go-audit", "rust-audit", "python-audit", "trivy-fs"]}


if __name__ == "__main__":
    stage("security", run)
