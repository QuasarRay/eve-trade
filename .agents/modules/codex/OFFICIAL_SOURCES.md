# Codex adapter source registry

Checked: 2026-08-08.

Host adapters are version-sensitive. Before changing managed Codex keys, verify them against current
first-party Codex source/documentation and then verify the effective live client after installation.

Current source anchors used by this adapter:

- OpenAI Codex `config_toml.rs` — `AgentsToml`, custom roles, default subagent model/reasoning and V1 depth:
  https://github.com/openai/codex/blob/main/codex-rs/config/src/config_toml.rs
- OpenAI Codex `openai_models.rs` — current reasoning-effort enum includes `Max`:
  https://github.com/openai/codex/blob/main/codex-rs/protocol/src/openai_models.rs

Important current runtime caveats are intentionally treated as verification risks rather than stable API:
MultiAgentV2 may use a different total-session concurrency setting than V1, role/model/effort application has
varied across clients, and some released builds have had V2 regressions. Do not encode issue reports as
permanent truth; re-check current source/runtime when maintaining this adapter.
