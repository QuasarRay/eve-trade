"""Dagger pipeline for bounded simulator dependency downloads.

Run with:
    python dagger/pipeline.py

The pipeline intentionally separates Python dependency download from Quilkin image
pulling so a stalled game-proxy download does not block preparing the simulator.
"""

from __future__ import annotations

import asyncio


async def main() -> None:
    try:
        import dagger
    except ImportError as exc:
        raise SystemExit(
            "dagger-io is not installed. Run scripts/downloads/fetch_simulator_deps.py "
            "as the standard-library fallback."
        ) from exc

    async with dagger.Connection(dagger.Config(log_output=None)) as client:
        source = client.host().directory(".", exclude=[".git", "target", "distributed-backend/src/trade-settlement/target"])
        wheels = (
            client.container()
            .from_("python:3.13-slim")
            .with_directory("/src", source)
            .with_workdir("/src")
            .with_exec(["python", "-m", "pip", "download", "-r", "simulator/requirements.txt", "-d", "/out/wheels"])
            .directory("/out")
        )
        await wheels.export(".downloads/dagger-python")

        # Pull one Quilkin image in a separate step. If this fails, Python wheels
        # are still exported and the fallback UDP proxy can be used locally.
        await client.container().from_("ghcr.io/embarkstudios/quilkin:0.9.0").sync()


if __name__ == "__main__":
    asyncio.run(main())
