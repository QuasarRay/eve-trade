# Xonsh Adapter

Use Xonsh when Python-native control flow/data manipulation plus subprocess execution can reduce
shell/Python switching, quoting mistakes, or repeated tool calls. Do not force it over existing
project-native PowerShell/bash scripts.

`rc.xsh` installs a lightweight `agentctl` alias for an interactive Xonsh session. Source it from
the repository root or your session; it does not modify the user's global Xonsh configuration.
