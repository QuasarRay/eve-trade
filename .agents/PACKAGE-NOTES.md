# Package notes

Extract this pack at the EVE-TRADE repository root.

The `.agents/` directory is the reusable source of truth. The root `AGENTS.md` loads the workflow, and `.codex/config.toml` registers the real subagent roles through `config_file` while enforcing one-thread concurrency.

## Structure

```text
.agents/00-ORCHESTRATION.md
.agents/01-DELEGATION-GATES.md
.agents/02-CONTEXT-PACKET.md
.agents/03-EVIDENCE-PACKET.md
.agents/04-CHECKPOINTS.md
.agents/05-EVE-TRADE-INVARIANTS.md
.agents/PACKAGE-NOTES.md
.agents/README.md
.agents/adversarial-verifier.toml
.agents/evidence-investigator.toml
.agents/final-auditor.toml
.agents/implementation-worker.toml
.codex/config.toml
AGENTS.md
```
