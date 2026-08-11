# Git and Workspace Integrity

Treat unknown dirty/untracked files as user-owned. Inspect status before meaningful writes and at
final audit. Preserve unrelated changes when editing dirty files.

Do not use broad destructive commands (`reset --hard`, `clean -fd`, blanket checkout/restore),
stash/rebase/amend/force-push, or history rewrite unless explicitly requested and understood.

Do not run sweeping format/fix commands across unrelated code merely for convenience. Watch
nested repos, submodules, worktrees, generated files and lockfile churn.

Use atomic writes for framework state/config mutations and fail closed on symlink/path-escape
hazards. Back up externally owned config before an installer changes it.
