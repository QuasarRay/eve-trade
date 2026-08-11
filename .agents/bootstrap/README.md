# Human-maintainer bootstrap material

`root-AGENTS.block.md` is source material for a human-reviewed merge into a target repository's root `AGENTS.md`. Ordinary Aegis tasks must not run an installer or modify the governing instruction file.

The trusted deployment sequence is:

1. verify/build `dist/aegis-governance` from source outside `.agents`;
2. back up target `.agents` and root instructions;
3. manually copy the artifact's `.agents` directory;
4. manually merge the delimited bootstrap block while preserving unrelated project instructions;
5. run deployed verification/audit/laws in a fresh session.

Legacy `install.py`/`uninstall.py` remain packaged for compatibility testing and trusted, out-of-band maintenance analysis. The operational CLI deliberately exposes no install/uninstall command, and Aegis-managed mutation guards reject writes to active `.agents` and governing `AGENTS.md` even when those functions are called inside an ordinary task.
