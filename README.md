# Agent Workflow Review

CLI-first Agent workflow review and experience incubation layer.

This project reviews real Codex session JSONL and produces auditable improvement
candidates for MCP tools, skills, subagent patterns, and debug-runbook seeds.

It intentionally does not auto-publish runbooks, skills, MCP tools, or workflow
hooks. v0 keeps human review in the promotion path.

## v0 Flow

```bash
session-review review --profile examples/session-review/review-profile.toml --since-days 7
session-review extract --packets .codex-local/session-review/review-packets.json
session-review decide --candidate <id> --decision staged --reason "manual review passed"
session-review export --candidate <id> --target debug_runbook_seed
```

## Scope

- Parse Codex session JSONL into `ReviewCase` and `ReviewPacket`.
- Redact raw session material and keep opaque `RawRef` links.
- Run deterministic v0 reviewers for MCP efficacy, skill utility, subagent value,
  shell fallback, and path stability.
- Generate `ImprovementCandidate` records with manual promotion lifecycle.
- Export only staged/promoted `debug_runbook_seed` drafts.

## Non-goals

- No dashboard.
- No automatic publishing.
- No direct `debug-runbook` runtime integration.
- No complex DSL.
- No cross-project automatic experience migration.
