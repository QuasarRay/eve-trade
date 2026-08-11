# Optional Python Metaprogramming Extension

`mcpyrate` and `unpythonic` are optional extension dependencies for authors who want declarative DSLs,
compile-time transformations, functional utilities, or generated adapters. They are never required for
framework bootstrap, state recovery, law execution, audit, or Codex installation.

This split is intentional: metaprogramming may increase modularity for sophisticated local extensions, but
making recovery infrastructure depend on macro expansion would reduce portability and make failures harder
to diagnose.

Rules for extensions:

- keep the stable `agentinfra` public boundary plain Python;
- isolate macro syntax behind optional modules;
- generated artifacts must be reproducible from committed sources;
- provide a non-magical inspection/debug path;
- generated or macro-expanded code may not lower Max reasoning, enable parallel subagents, or permit nested delegation;
- never use macros to hide network access, test bypasses, destructive commands, or instruction-precedence
  changes;
- do not introduce `mcpyrate`/`unpythonic` unless the extension becomes materially clearer or less repetitive.
