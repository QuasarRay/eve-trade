# Context, Cache, and Bandwidth Economy

Treat context transfer as a budgeted resource.

## Freshness ledger

For expensive/long tasks, record content hashes or equivalent freshness evidence for important
files/sources. Reuse conclusions while their dependencies remain unchanged. Invalidate a
conclusion when its source, relevant generated output, config, dependency version, caller
contract, or upstream decision changes.

Do not reread a file because a phase or child changed. Reread because evidence was invalidated or
exact text is needed.

## Retrieval ladder

Prefer exact symbol/term -> relevant range -> file -> module -> directory -> repository scan.
Avoid transmitting generated trees, dependency caches, binaries, lockfiles, or huge logs unless
they are the subject of the task.

For external sources, retain URL/version/date/conclusion and refresh only when freshness matters.

## Compaction

At phase boundaries compact to: objective, acceptance gates, verified facts, decisions, risks,
changed files, executed commands/results, active uncertainties, next falsifier. Do not replay
historical narration.

`agentctl context record/check` fingerprints local files without injecting their contents into model context.
For external sources, `external-record/external-check` can retain a version/ETag/content fingerprint and an
optional TTL. A TTL is a reuse hint, not proof of freshness when the task explicitly requires current data.
