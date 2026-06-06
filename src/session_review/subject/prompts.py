from __future__ import annotations

from pathlib import Path


def scaffold_prompt(subject_id: str, *, tool_roots: list[str], skill_paths: list[str], mcp_schemas: list[str]) -> str:
    return f"""# Subject Review Scaffold Prompt

You are preparing a subject review pack for `{subject_id}`.

Read the relevant tool project, README, CLI help, skill instructions, and MCP schemas.
Generate `subject.toml`, `objective.toml`, and a proposed `signal-pack.toml` for this subject.

Inputs to inspect:
- tool roots: {tool_roots or ["<none provided>"]}
- skill paths: {skill_paths or ["<none provided>"]}
- MCP schemas: {mcp_schemas or ["<none provided>"]}

Focus on:
- direct usage signals: tool names, commands, skill names, MCP names, subagent names;
- contextual need signals: errors, latency, timeout, retries, user reminders;
- fallback signals: inefficient tools or repeated manual paths used when this subject should help;
- success/failure signals that can be detected from session traces.
- domain anchors: evidence that proves an episode belongs to this subject;
- negative signals: contexts that look similar but should be excluded;
- ambiguous terms that require a domain anchor before attribution.

Signal pack rules:
- Do not create a global keyword list.
- Keep signals subject-specific and auditable.
- Include positive signals, negative signals, and domain anchors.
- Treat generated signal packs as proposals until a human reviews them.
- Use `status = "proposed"` for generated drafts; switch to `reviewed` or
  `active` only after manual approval.
- Prefer abstention for ambiguous cross-domain terms such as ssh, proxy, timeout, or git.

Safety:
- Do not include secrets or raw private logs in the generated config.
- Do not modify tools, skills, MCP servers, subagents, hooks, or runbooks.
- Produce review configuration only.
"""


def automation_prompt(subject_id: str) -> str:
    return f"""# Codex Automation Prompt

Run a daily subject review for `{subject_id}`.

Steps:
1. Scan the last 24 hours of Codex sessions.
2. Use the subject-local `signal-pack.toml` only when it is marked `reviewed` or `active`.
3. Run `session-review subject collect {subject_id} --all-projects --since-hours 24`.
4. Run `session-review subject discriminate {subject_id}`.
5. Run `session-review subject review {subject_id} --with-collision`.
6. Summarize the updated report, attribution hints, collisions, and candidates.

Constraints:
- Do not automatically modify tools, skills, MCP schemas, subagents, hooks, or runbooks.
- Do not auto-promote candidates.
- Do not treat attribution hints as root-cause proof.
- Keep ambiguous or collided episodes review-only.
- Keep outputs in `.session-review/subjects/{subject_id}/output`.
- Preserve safety defaults and avoid raw sensitive text.
"""


def write_prompts(
    subject_dir: Path,
    subject_id: str,
    *,
    tool_roots: list[str],
    skill_paths: list[str],
    mcp_schemas: list[str],
) -> None:
    subject_dir.mkdir(parents=True, exist_ok=True)
    (subject_dir / "scaffold-prompt.md").write_text(
        scaffold_prompt(
            subject_id,
            tool_roots=tool_roots,
            skill_paths=skill_paths,
            mcp_schemas=mcp_schemas,
        ),
        encoding="utf-8",
    )
    (subject_dir / "codex-automation-prompt.md").write_text(
        automation_prompt(subject_id),
        encoding="utf-8",
    )
