# Project Law Tests

Place project-specific behavioral laws in `.agents/laws/project/*.toml` or point `agentctl law
run` at another law file. Keep laws stable across implementation refactors.

Copy `template.toml` and replace the examples. Law commands should call production APIs/entrypoints
rather than a fake test-only implementation.
