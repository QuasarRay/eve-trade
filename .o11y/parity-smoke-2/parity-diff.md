# Local vs CI parity diff

- Local: `.o11y\runs\local-20260627T154557Z-9e14916a-24a14f`
- CI: `.o11y\runs\local-20260627T155717Z-9e14916a-6547df`

| Field | Local | CI | Likely impact |
|---|---|---|---|
| docker.compose_config_hash | `bde3529dae3483c57f7eda65794f54ca8ef46d11a5199a766652bf5d764f7264` | `9d3daad5bfcc58fd5496fb05578d6be97b21292561c66f61f28c6876fa79bdd9` | Docker configuration, version, or image digest differs. |
| pytest.collected | `None` | `0` | Pytest collection or first failure differs. |
| pytest.first_failure | `None` | `` | Pytest collection or first failure differs. |
| env.OBSERVABILITY_RUN_ID.present | `False` | `True` | Environment variable presence differs; values remain redacted. |
| pipeline.command_sequence | `[]` | `['observability-unit-tests']` | Observed command sequence differs between local and CI. |
