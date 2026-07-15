# Local vs CI parity diff

- Local: `.o11y\runs\local-20260627T154557Z-9e14916a-24a14f`
- CI: `.o11y\runs\local-20260627T155717Z-9e14916a-6547df`

| Field | Local | CI | Likely impact |
|---|---|---|---|
| git.sha | `9e14916a304c97d0a7b1954a9615b61265fe3f50` | `9e14916a304c97d0a7b1954a9615b61265fe3f50` |  |
| git.dirty | `True` | `True` |  |
| git.branch | `main` | `main` |  |
| python.version | `Python 3.14.5` | `Python 3.14.5` |  |
| go.version | `go version go1.26.4 windows/amd64` | `go version go1.26.4 windows/amd64` |  |
| rust.version | `rustc 1.95.0 (59807616e 2026-04-14)` | `rustc 1.95.0 (59807616e 2026-04-14)` |  |
| docker.version | `29.5.2` | `29.5.2` |  |
| docker.compose.version | `5.1.4` | `5.1.4` |  |
| os.name | `Windows-11-10.0.26200-SP0` | `Windows-11-10.0.26200-SP0` |  |
| docker.compose_config_hash | `bde3529dae3483c57f7eda65794f54ca8ef46d11a5199a766652bf5d764f7264` | `9d3daad5bfcc58fd5496fb05578d6be97b21292561c66f61f28c6876fa79bdd9` | Docker configuration, version, or image digest differs. |
| db.schema_hash | `None` | `None` |  |
| db.migration_hash | `09cfd1bf4902af70243507c9c30fada151f24ae9c6032dc9c3da7a305afaf4a6` | `09cfd1bf4902af70243507c9c30fada151f24ae9c6032dc9c3da7a305afaf4a6` |  |
| protobuf.generated_hash | `822c3e1eb4ac4f71971c05529dd08e5b86614bb81c8db90732ffa2cc317b224f` | `822c3e1eb4ac4f71971c05529dd08e5b86614bb81c8db90732ffa2cc317b224f` |  |
| pytest.collected | `None` | `0` | Pytest collection or first failure differs. |
| pytest.first_failure | `None` | `` | Pytest collection or first failure differs. |
| env.OBSERVABILITY_RUN_ID.present | `False` | `True` | Environment variable presence differs; values remain redacted. |
| pipeline.command_sequence | `[]` | `['observability-unit-tests']` | Observed command sequence differs between local and CI. |
