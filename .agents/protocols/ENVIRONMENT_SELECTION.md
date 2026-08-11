# Execution Environment Selection

Select the execution surface deliberately. The objective is not shell preference; it is maximum correctness
and reasoning quality per total credit/tool/bandwidth cost while retaining Max model reasoning.

Decision order:

1. If one deterministic subprocess is sufficient, invoke it directly—least quoting and least state.
2. If a sustained interactive workflow combines Python objects/logic and shell commands, prefer Xonsh when
   available and its syntax is natural for the operation.
3. Use PowerShell for Windows-native administration/object pipelines and tooling expecting PowerShell.
4. Use Bash/sh for POSIX-native scripts and ecosystems expecting POSIX semantics.
5. Preserve a working environment within a phase; do not switch shells without an expected reduction in
   errors, context/tool calls, or duplicated glue.

Environment choice is reversible and should not alter project semantics. Record project-specific shell
requirements in the project overlay rather than the generic core.
