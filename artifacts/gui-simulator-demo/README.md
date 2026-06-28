# GUI simulator QA demo artifacts

This directory contains the first-draft recorded manual-QA pass for the GUI simulator.

## Artifacts

- `video/gui-simulator-qa.webm` — 1440×900, 25 fps, 2:47 recorded pass.
- `video/gui-simulator-qa.vtt` — timed captions for the video.
- `screenshots/` — 19 full-page checkpoint images.
- `narration.md` — narration text generated from the recorded checkpoints.
- `run-summary.md` and `run-results.json` — machine- and human-readable assertion evidence.
- `report.md` — feature inventory, scenario coverage, gaps, issues, and recommendations.
- `logs/` — browser console output, Compose service logs for the recording window, and fatal-signature scan.

## One-command deterministic run (Windows)

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-gui-demo.ps1 -ResetData
```

`-ResetData` deletes the local `eve-trade` PostgreSQL and RabbitMQ Docker volumes before recreating the seeded world. The command starts/builds the stack, installs the pinned Playwright dependency and Chromium when needed, runs the QA pass, records video, captures screenshots/logs, and rewrites the generated artifacts.

Fast rerun when the stack, Node dependencies, browser, and seed capacity are already available:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-gui-demo.ps1 -NoBuild -SkipInstall
```

Skip the intentional Market-service outage scenario with `-SkipOutage`.

## Manual commands

```powershell
docker compose up -d --build
# Open http://127.0.0.1:8000
pnpm install --frozen-lockfile
pnpm exec playwright install chromium
pnpm run gui:demo
```

Stop the stack with `docker compose down`. Add `-v` only when the local seeded data should also be deleted.
