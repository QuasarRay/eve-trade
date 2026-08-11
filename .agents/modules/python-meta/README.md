# Optional mcpyrate / unpythonic Extension

The core intentionally does not require these packages. If a project already uses them, or an agent
needs a local DSL for generating repetitive law/module declarations, this module authorizes them as
an opt-in extension.

Use metaprogramming to remove repetitive declarations, not to obscure control flow, safety checks,
or the law runner. Generated artifacts must remain inspectable and reproducible without asking the
LLM to re-derive them.

Install project-locally if desired:

`python -m pip install mcpyrate unpythonic`
