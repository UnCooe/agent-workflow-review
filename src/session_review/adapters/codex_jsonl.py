from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from session_review.config import ReviewerPack, TaskFamilyConfig
from session_review.models import (
    AgentEvent,
    AgentEventKind,
    CaseOutcome,
    RawRef,
    ReviewCase,
    RouteMode,
    SensitivityLevel,
    SourceType,
    ToolFamily,
)
from session_review.safety import SafetyPolicy, max_sensitivity, stable_hash, summarize_text


@dataclass
class ParseResult:
    cases: list[ReviewCase] = field(default_factory=list)
    parser_warnings: list[str] = field(default_factory=list)
    redaction_warnings: list[str] = field(default_factory=list)


def parse_session_files(
    paths: Iterable[Path],
    *,
    safety: SafetyPolicy,
    reviewer_pack: ReviewerPack,
) -> ParseResult:
    result = ParseResult()
    for path in paths:
        parsed = parse_session_file(path, safety=safety, reviewer_pack=reviewer_pack)
        result.cases.extend(parsed.cases)
        result.parser_warnings.extend(parsed.parser_warnings)
        result.redaction_warnings.extend(parsed.redaction_warnings)
    result.redaction_warnings.extend(safety.redaction_warnings)
    return result


def parse_session_file(
    path: Path,
    *,
    safety: SafetyPolicy,
    reviewer_pack: ReviewerPack,
) -> ParseResult:
    result = ParseResult()
    session_id = path.stem
    cwd_hash: str | None = None
    turn_index = -1
    current: _CaseBuilder | None = None
    pending_tools: dict[str, tuple[str, ToolFamily]] = {}

    for line_no, record in iter_json_records(path):
        if not isinstance(record, dict):
            result.parser_warnings.append(f"{path}:{line_no}: non-object JSON record")
            continue
        timestamp = _parse_timestamp(record.get("timestamp") or record.get("created_at"))
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "session_meta":
            session_id = str(payload.get("id") or session_id)
            if payload.get("cwd"):
                cwd_hash = stable_hash(payload.get("cwd"))
            continue

        payload_type = payload.get("type")
        role = payload.get("role")

        if payload_type == "message" and role == "user":
            if current and current.events:
                result.cases.append(current.build())
            turn_index += 1
            text = flatten_text(payload.get("content") or payload.get("message"))
            task_family, family_config = classify_task_family(text, reviewer_pack)
            raw_ref = _raw_ref(
                safety,
                path=path,
                line_no=line_no,
                turn_index=turn_index,
                source_id=(session_id, line_no, "user"),
                sensitivity=SensitivityLevel.S2,
            )
            event = AgentEvent(
                event_id=_event_id(session_id, line_no, "user"),
                session_id=session_id,
                turn_index=turn_index,
                ts=timestamp,
                cwd_hash=cwd_hash,
                kind=AgentEventKind.USER_MESSAGE,
                tool_family=ToolFamily.OTHER,
                text_summary=summarize_text(text),
                raw_ref=raw_ref,
                safety_level=SensitivityLevel.S2,
            )
            current = _CaseBuilder(
                session_id=session_id,
                turn_index=turn_index,
                user_goal_summary=summarize_text(text),
                task_family=task_family,
                expected_route=_route_mode(family_config.preferred_route),
                expected_data=list(family_config.expected_data),
            )
            current.add_event(event)
            continue

        if current is None:
            continue

        if payload_type == "message" and role == "assistant":
            text = flatten_text(payload.get("content") or payload.get("message"))
            event = _message_event(
                safety=safety,
                path=path,
                line_no=line_no,
                session_id=session_id,
                turn_index=turn_index,
                timestamp=timestamp,
                cwd_hash=cwd_hash,
                role="assistant",
                text=text,
            )
            current.add_event(event)
            if _looks_resolved(text):
                current.outcome = CaseOutcome.RESOLVED
            elif _looks_unresolved(text):
                current.outcome = CaseOutcome.UNRESOLVED
            continue

        if payload_type == "function_call":
            tool_name = normalize_tool_name(payload.get("name"))
            tool_family = classify_tool_family(tool_name, reviewer_pack)
            call_id = str(payload.get("call_id") or payload.get("id") or f"{path}:{line_no}")
            args = parse_arguments(payload.get("arguments"))
            args_summary, sensitivity = safety.redact_mapping(args)
            raw_ref = _raw_ref(
                safety,
                path=path,
                line_no=line_no,
                turn_index=turn_index,
                source_id=(session_id, line_no, call_id, "call"),
                sensitivity=sensitivity,
            )
            kind = AgentEventKind.TOOL_CALL
            if tool_family == ToolFamily.SUBAGENT and tool_name == "spawn_agent":
                kind = AgentEventKind.SUBAGENT_SPAWN
            event = AgentEvent(
                event_id=_event_id(session_id, line_no, "call"),
                session_id=session_id,
                turn_index=turn_index,
                ts=timestamp,
                cwd_hash=cwd_hash,
                kind=kind,
                tool_name=tool_name,
                tool_family=tool_family,
                args_summary=args_summary,
                raw_ref=raw_ref,
                safety_level=sensitivity,
            )
            pending_tools[call_id] = (tool_name, tool_family)
            current.add_tool_event(event)
            continue

        if payload_type == "function_call_output":
            call_id = str(payload.get("call_id") or "")
            tool_name, tool_family = pending_tools.get(call_id, ("unknown", ToolFamily.OTHER))
            output = flatten_text(payload.get("output") or payload.get("result") or payload.get("content"))
            output_summary, sensitivity = safety.summarize_output(output)
            raw_ref = _raw_ref(
                safety,
                path=path,
                line_no=line_no,
                turn_index=turn_index,
                source_id=(session_id, line_no, call_id, "result"),
                sensitivity=sensitivity,
            )
            kind = AgentEventKind.TOOL_RESULT
            if tool_family == ToolFamily.SUBAGENT:
                kind = AgentEventKind.SUBAGENT_RESULT
            event = AgentEvent(
                event_id=_event_id(session_id, line_no, "result"),
                session_id=session_id,
                turn_index=turn_index,
                ts=timestamp,
                cwd_hash=cwd_hash,
                kind=kind,
                tool_name=tool_name,
                tool_family=tool_family,
                output_summary=output_summary,
                raw_ref=raw_ref,
                safety_level=sensitivity,
            )
            current.add_event(event)
            if _output_empty(output_summary, output):
                current.add_recovery("mcp_empty_result" if tool_family == ToolFamily.MCP else "empty_result")
            continue

    if current and current.events:
        result.cases.append(current.build())
    result.redaction_warnings.extend(safety.redaction_warnings)
    return result


def iter_recent_session_files(codex_home: Path, *, since_days: int | None = None) -> Iterator[Path]:
    sessions = codex_home.expanduser() / "sessions"
    if not sessions.exists():
        return
    cutoff: float | None = None
    if since_days is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400
    for path in sorted(sessions.glob("**/*.jsonl")):
        if cutoff is None or path.stat().st_mtime >= cutoff:
            yield path


class _CaseBuilder:
    def __init__(
        self,
        *,
        session_id: str,
        turn_index: int,
        user_goal_summary: str,
        task_family: str,
        expected_route: RouteMode,
        expected_data: list[str],
    ) -> None:
        self.session_id = session_id
        self.turn_index = turn_index
        self.user_goal_summary = user_goal_summary
        self.task_family = task_family
        self.expected_route = expected_route
        self.expected_data = expected_data
        self.events: list[AgentEvent] = []
        self.tool_path: list[str] = []
        self.recovery_path: list[str] = []
        self.outcome = CaseOutcome.UNKNOWN

    def add_event(self, event: AgentEvent) -> None:
        self.events.append(event)

    def add_tool_event(self, event: AgentEvent) -> None:
        previous_families = [item.tool_family for item in self.events if item.tool_name]
        if event.tool_name:
            self.tool_path.append(event.tool_name)
        if event.tool_family == ToolFamily.SHELL and ToolFamily.MCP in previous_families:
            self.add_recovery("shell_fallback_after_mcp")
        if event.tool_family == ToolFamily.MCP and ToolFamily.SHELL in previous_families:
            self.add_recovery("mcp_after_shell")
        self.add_event(event)

    def add_recovery(self, item: str) -> None:
        if item not in self.recovery_path:
            self.recovery_path.append(item)

    def build(self) -> ReviewCase:
        families = [event.tool_family for event in self.events if event.tool_name]
        actual_route = RouteMode.UNKNOWN
        if families:
            if families[0] == ToolFamily.MCP:
                actual_route = RouteMode.MCP_FIRST
            elif families[0] == ToolFamily.SHELL:
                actual_route = RouteMode.SOURCE_FIRST
            if ToolFamily.MCP in families and ToolFamily.SHELL in families:
                actual_route = RouteMode.MIXED
        raw_refs = [event.raw_ref for event in self.events]
        case_key = f"{self.task_family}:{'->'.join(self.tool_path[:4]) or 'no-tool'}"
        return ReviewCase(
            case_id=stable_hash((self.session_id, self.turn_index, self.user_goal_summary)),
            session_id=self.session_id,
            case_group_key=case_key,
            user_goal_summary=self.user_goal_summary,
            task_family=self.task_family,
            expected_data=self.expected_data,
            expected_route=self.expected_route,
            actual_route=actual_route,
            events=self.events,
            tool_path=self.tool_path,
            recovery_path=self.recovery_path,
            outcome=self.outcome,
            raw_refs=raw_refs,
        )


def iter_json_records(path: Path) -> Iterator[tuple[int, object | None]]:
    decoder = json.JSONDecoder()
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        yield 0, None
        return
    cursor = 0
    line_no = 1
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            if text[cursor] == "\n":
                line_no += 1
            cursor += 1
        if cursor >= len(text):
            break
        record_line = line_no
        try:
            record, end = decoder.raw_decode(text, cursor)
            yield record_line, record
            line_no += text[cursor:end].count("\n")
            cursor = end
        except json.JSONDecodeError:
            yield record_line, None
            next_newline = text.find("\n", cursor)
            if next_newline == -1:
                break
            line_no += text[cursor : next_newline + 1].count("\n")
            cursor = next_newline + 1


def classify_task_family(text: str, reviewer_pack: ReviewerPack) -> tuple[str, TaskFamilyConfig]:
    lowered = text.lower()
    for name, config in reviewer_pack.task_families.items():
        if any(keyword.lower() in lowered for keyword in config.trigger_keywords):
            return name, config
    return "unknown", TaskFamilyConfig()


def classify_tool_family(tool_name: str, reviewer_pack: ReviewerPack) -> ToolFamily:
    if tool_name in reviewer_pack.tools_for_family("mcp"):
        return ToolFamily.MCP
    if tool_name in reviewer_pack.tools_for_family("skill") or "skill" in tool_name:
        return ToolFamily.SKILL
    if tool_name in reviewer_pack.tools_for_family("subagent") or tool_name in {
        "spawn_agent",
        "wait_agent",
        "close_agent",
    }:
        return ToolFamily.SUBAGENT
    if tool_name in reviewer_pack.tools_for_family("shell") or any(
        token in tool_name for token in ("exec_command", "bash", "zsh", "shell")
    ):
        return ToolFamily.SHELL
    if "browser" in tool_name:
        return ToolFamily.BROWSER
    return ToolFamily.OTHER


def normalize_tool_name(value: object) -> str:
    name = str(value or "")
    return name.rsplit(".", 1)[-1] if name else ""


def parse_arguments(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw_arguments": value}
    return parsed if isinstance(parsed, dict) else {"arguments": parsed}


def flatten_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, dict):
                chunks.append(str(item.get("text") or item.get("content") or ""))
            else:
                chunks.append(str(item))
        return "\n".join(chunk for chunk in chunks if chunk)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    return "" if value is None else str(value)


def _raw_ref(
    safety: SafetyPolicy,
    *,
    path: Path,
    line_no: int,
    turn_index: int,
    source_id: object,
    sensitivity: SensitivityLevel,
) -> RawRef:
    return safety.raw_ref(
        source_type=SourceType.CODEX_JSONL,
        source_id=source_id,
        locator={"file": str(path), "line": line_no, "turn_index": turn_index},
        sensitivity=sensitivity,
    )


def _message_event(
    *,
    safety: SafetyPolicy,
    path: Path,
    line_no: int,
    session_id: str,
    turn_index: int,
    timestamp: datetime | None,
    cwd_hash: str | None,
    role: str,
    text: str,
) -> AgentEvent:
    raw_ref = _raw_ref(
        safety,
        path=path,
        line_no=line_no,
        turn_index=turn_index,
        source_id=(session_id, line_no, role),
        sensitivity=SensitivityLevel.S2,
    )
    return AgentEvent(
        event_id=_event_id(session_id, line_no, role),
        session_id=session_id,
        turn_index=turn_index,
        ts=timestamp,
        cwd_hash=cwd_hash,
        kind=AgentEventKind.ASSISTANT_MESSAGE,
        tool_family=ToolFamily.OTHER,
        text_summary=summarize_text(text),
        raw_ref=raw_ref,
        safety_level=SensitivityLevel.S2,
    )


def _event_id(session_id: str, line_no: int, suffix: str) -> str:
    return stable_hash((session_id, line_no, suffix))


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _route_mode(value: object) -> RouteMode:
    try:
        return RouteMode(str(value))
    except ValueError:
        return RouteMode.UNKNOWN


def _output_empty(summary: dict[str, Any], text: str) -> bool:
    if any(summary.get(key) for key in ("total", "total_hits", "returned", "total_returned", "candidate_count")):
        return False
    lowered = text.lower()
    return any(token in lowered for token in ("no results", "empty", "[]", "total_hits\":0", "total_returned\":0"))


def _looks_resolved(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("已确认", "定位到", "符合预期", "root cause", "confirmed"))


def _looks_unresolved(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("查不到", "没查到", "不能下结论", "能力不够", "无法确认"))


def case_max_sensitivity(case: ReviewCase) -> SensitivityLevel:
    return max_sensitivity([event.safety_level for event in case.events])
