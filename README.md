# Agent Workflow Review

CLI-first Agent workflow review and experience incubation layer.

It reviews real Codex session JSONL and produces auditable improvement
candidates for MCP tools, skills, subagent patterns, and debug-runbook seeds.
It is not a dashboard and does not auto-publish workflow changes.

## Quick Start

```bash
git clone https://github.com/UnCooe/agent-workflow-review.git
cd agent-workflow-review
uv run pytest
```

Run the bundled redacted demo:

```bash
uv run session-review review \
  --profile examples/session-review/review-profile.toml \
  --reviewer-pack examples/session-review/reviewer-pack.toml \
  --session examples/session-review/sessions/redacted-session.jsonl

uv run session-review extract \
  --packets .codex-local/session-review/review-packets.json

uv run session-review list-candidates \
  --candidates .codex-local/session-review/improvement-candidates.json
```

Stage and export a candidate after manual review:

```bash
uv run session-review decide \
  --candidates .codex-local/session-review/improvement-candidates.json \
  --candidate <candidate_id> \
  --decision staged \
  --reason "manual review passed"

uv run session-review export \
  --candidates .codex-local/session-review/improvement-candidates.json \
  --candidate <candidate_id> \
  --target debug_runbook_seed
```

## Use In Another Project

Initialize a project-local workspace:

```bash
uv run session-review init --target /path/to/your-project
```

This creates:

```text
.session-review/
  review-profile.toml
  reviewer-pack.toml
  .gitignore
  output/
  exports/
```

Edit `.session-review/reviewer-pack.toml` to describe your project-specific MCP
tools, shell tools, subagent tools, skills, and task families. Then run:

```bash
uv run session-review review \
  --profile /path/to/your-project/.session-review/review-profile.toml \
  --reviewer-pack /path/to/your-project/.session-review/reviewer-pack.toml \
  --since-days 7
```

By default, auto-discovery tries to review only sessions that match the current
project root. If project ownership cannot be confirmed, the session is skipped
and a parser warning is recorded.

## Commands

- `session-review init`: create a project-local `.session-review/` workspace.
- `session-review review`: parse Codex session JSONL into cases, packets,
  findings, a Markdown report, and debug artifact.
- `session-review extract`: aggregate findings into improvement candidates.
- `session-review list-candidates`: print candidates as a table or JSON.
- `session-review decide`: record a manual lifecycle decision.
- `session-review export`: export a staged/promoted `debug_runbook_seed` draft.

## Safety Defaults

- `include_raw_text=false` by default.
- User and assistant text is stored as hash and character count, not raw prompt
  snippets.
- Sensitive identifiers and paths are hashed.
- Raw source material is referenced through `RawRef`.
- Reports and candidates are designed to avoid raw prompt text.

## Debug-Runbook Relationship

This project only exports `debug_runbook_seed` drafts after manual staging or
promotion. It does not register, sync, execute, or publish runbooks.

## Non-goals

- No LLM reviewer in v0.1.
- No dashboard.
- No automatic publishing.
- No direct `debug-runbook` runtime integration.
- No automatic skill, MCP, subagent, hook, or runbook changes.
- No cross-project automatic experience migration.
