# Debug-Runbook Bridge

Agent Workflow Review does not integrate with the debug-runbook runtime in
v0.1. It only exports seed drafts for humans to review.

## Export Rules

`session-review export --target debug_runbook_seed` only works when:

- the candidate target is `debug_runbook_seed`;
- the candidate has a latest manual decision of `staged` or `promoted`;
- `export_allowed=true`.

## Output

The exported YAML contains:

- candidate metadata;
- manual decision metadata;
- trigger signals;
- recommended path;
- anti-patterns;
- fallback rules;
- open review items.

The seed is intentionally incomplete. It should be reviewed, edited, tested
against future sessions, and only then promoted into a runbook system.
