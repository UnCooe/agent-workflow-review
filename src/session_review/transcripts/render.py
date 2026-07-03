from __future__ import annotations

import json
from collections import Counter

from session_review.transcripts.schemas import TextBlock, ToolCallFact, TurnFact


def render_turn_card(fact: TurnFact) -> str:
    lines = [
        f"# Turn `{fact.turn_id}`",
        "",
        f"- time: `{fact.time_range.get('start', '')}` -> `{fact.time_range.get('end', '')}`",
        f"- tools: {_tool_summary(fact.tool_calls)}",
        (
            "- mcp burden: "
            f"total={fact.mcp_burden.total_mcp_calls}, "
            f"used={fact.mcp_burden.used_in_final_calls}, "
            f"failed_or_detour={fact.mcp_burden.failed_or_detour_calls}, "
            f"flags={','.join(fact.mcp_burden.issue_flags) or 'none'}"
        ),
        "",
        "## User",
        "",
        _quote(fact.user),
        "",
        "## MCP Call Sequence",
        "",
    ]
    if fact.mcp_call_sequence:
        for call in fact.mcp_call_sequence:
            lines.append(_tool_line(call))
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Final Answer",
            "",
            _quote(fact.final_answer),
            "",
            "## Next User",
            "",
            _quote(fact.next_user),
            "",
            "## Artifact Notes",
            "",
            "- This card is local-only transcript evidence.",
            "- Tool outputs are summarized; raw logs are not copied here.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_turn_full(fact: TurnFact) -> str:
    lines = [
        f"# Turn `{fact.turn_id}`",
        "",
        f"- time: `{fact.time_range.get('start', '')}` -> `{fact.time_range.get('end', '')}`",
        f"- raw refs: {len(fact.raw_refs)}",
        "",
        "## User",
        "",
        _quote(fact.user),
        "",
        "## Agent Process",
        "",
    ]
    process_items = [item for item in fact.timeline if item.kind in {"assistant_message", "tool_call"}]
    if process_items:
        for item in process_items:
            if item.kind == "assistant_message":
                lines.append(f"- assistant: {item.text}")
            else:
                lines.append(f"- tool `{item.tool_name}` ({item.tool_family})")
    else:
        lines.append("- none")
    lines.extend(["", "## Tool Calls", ""])
    if fact.tool_calls:
        for call in fact.tool_calls:
            lines.append(_tool_line(call, include_args=True))
    else:
        lines.append("- none")
    lines.extend(["", "## MCP Call Sequence", ""])
    if fact.mcp_call_sequence:
        for call in fact.mcp_call_sequence:
            lines.append(_tool_line(call, include_args=True))
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Adopted Evidence",
            "",
            _json_block(fact.adopted_evidence or []),
            "",
            "## Final Answer",
            "",
            _quote(fact.final_answer),
            "",
            "## Next User",
            "",
            _quote(fact.next_user),
            "",
            "## Derived Facts",
            "",
            _json_block(fact.derived),
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _tool_summary(calls: list[ToolCallFact]) -> str:
    counts = Counter(call.tool_name for call in calls)
    return ", ".join(f"`{tool}` x{count}" for tool, count in sorted(counts.items())) or "none"


def _tool_line(call: ToolCallFact, *, include_args: bool = False) -> str:
    summary = call.result_summary
    status = summary.get("status") or "unknown"
    shape = summary.get("shape") or "unknown"
    keys = ",".join(str(key) for key in summary.get("top_level_keys", [])[:5])
    label = "MCP" if call.tool_family == "mcp" else call.tool_family.upper()
    base = (
        f"- {label} #{call.index} `{call.tool_name}` "
        f"[{call.evidence_role}; {call.evidence_role_confidence}; {call.evidence_role_method}]: "
        f"result={call.result_source or 'none'} status={status} shape={shape}"
    )
    if keys:
        base += f" keys={keys}"
    if include_args:
        base += f" args={json.dumps(call.args_summary, ensure_ascii=False, sort_keys=True)}"
    return base


def _quote(block: TextBlock) -> str:
    if not block.text:
        return "> "
    return "\n".join(f"> {line}" if line else ">" for line in block.text.splitlines())


def _json_block(value: object) -> str:
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n```"
