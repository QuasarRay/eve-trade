# Xonsh Adapter Policy

Xonsh is a first-class **interactive** environment when installed, especially for workflows that repeatedly
mix Python data manipulation with subprocess commands. It is not automatically superior for every command.

Choose the environment by total expected cost and correctness:

- direct argv/process invocation for deterministic one-shot commands;
- Xonsh for interactive mixed Python + shell exploration where it eliminates glue scripts, quoting errors,
  repeated shell launches, or context switching;
- PowerShell for Windows-native object/registry/Visual Studio/.NET operations;
- Bash/POSIX shell for POSIX-native build scripts and tooling whose semantics are already expressed there;
- project-native wrappers (`cargo`, `uv`, `ninja`, etc.) where bypassing an interactive shell is clearer.

Do not translate a working project command into Xonsh merely for stylistic consistency. Environment switches
must have a concrete expected benefit in reasoning quality, correctness, reproducibility, or tool/context cost.
Never assume Xonsh is installed on a different machine; detect it.
