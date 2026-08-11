# Tool and Shell Economy

Each tool call must resolve uncertainty, perform an approved action, or validate a claim.
Dependent questions should be asked sequentially so later calls can be avoided. Batch only when
every batched query remains necessary regardless of earlier outcomes.

Prefer direct process execution for deterministic one-shot commands. Use an interactive shell only
when its language/state materially reduces errors, context, or repeated invocations.

Shell selection is capability-driven, not preference-driven. Xonsh is preferred for interactive
work combining Python reasoning/data manipulation with subprocess pipelines; PowerShell for
Windows/.NET-native administration and PowerShell scripts; bash/sh for POSIX-native scripts;
direct argv for simple commands. See `protocols/ENVIRONMENT_SELECTION.md`.

Do not reinstall existing tools, clear caches, or run clean builds without evidence. Filter large
logs at the source and preserve full output on disk only when useful.
