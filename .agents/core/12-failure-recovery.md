# Failure Recovery

After a failed hypothesis: preserve evidence, state what it disproved, update the model, and choose
the next action by information value. Do not immediately stack another speculative edit.

Thrashing indicators: repeated same command without relevant change; repeated edits to one function
without new evidence; oscillating implementations; widening scope because a local guess failed;
multiple children asked essentially the same question.

Escalate scope gradually. Retry only when failure can be transient and bound retries explicitly.
A pass after retry does not retroactively make the first failure irrelevant.

Do not label implementation defects as environmental blockers. If blocked, complete independently
verifiable work, record exact blocker/evidence, and leave a coherent state.
