from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from session_review.adapters.codex_jsonl import (
    classify_tool_family,
    flatten_text,
    iter_json_records,
    normalize_tool_name,
    parse_arguments,
)
from session_review.config import ReviewerPack
from session_review.models import SensitivityLevel, SourceType
from session_review.safety import SafetyPolicy, stable_hash, summarize_text
from session_review.transcripts.paths import TranscriptRunPaths, make_turn_ref
from session_review.transcripts.render import render_turn_card, render_turn_full
from session_review.transcripts.schemas import (
    McpBurden,
    SessionIndex,
    TextBlock,
    TimelineItem,
    ToolCallFact,
    TurnFact,
)


LOW_LEVEL_MCP_TOOLS = {"search_elk_logs", "search_aiserver_logs", "get_log_context"}
ERROR_KEYS = {"error", "exception", "traceback"}


@dataclass
class RawEvent:
    line_no: int
    timestamp: str
    record_type: str
    payload_type: str
    role: str
    payload: dict[str, Any]
    raw_ref: dict[str, Any]


@dataclass
class Turn:
    session_id: str
    session_id_hash: str
    source_file: Path
    source_file_hash: str
    turn_index: int
    trigger: RawEvent
    events: list[RawEvent] = field(default_factory=list)
    reaction: RawEvent | None = None


@dataclass
class CollectResult:
    facts: list[TurnFact]
    session_indexes: list[SessionIndex]


def collect_transcript_facts(
    session_files: Iterable[Path],
    *,
    reviewer_pack: ReviewerPack,
    safety: SafetyPolicy | None = None,
) -> list[TurnFact]:
    facts: list[TurnFact] = []
    policy = safety or SafetyPolicy(include_raw_text=True, hash_identifiers=False)
    for path in session_files:
        for turn in extract_turns_from_file(path, safety=policy):
            facts.append(turn_to_fact(turn, reviewer_pack=reviewer_pack))
    return facts


def write_transcript_facts(
    facts: list[TurnFact],
    *,
    session_files: Iterable[Path],
    run_paths: TranscriptRunPaths,
) -> CollectResult:
    run_paths.ensure()
    by_session: dict[str, list[TurnFact]] = {}
    for fact in facts:
        by_session.setdefault(fact.session_id_hash, []).append(fact)

    refs_by_turn: dict[str, Any] = {}
    for fact in facts:
        fact_json_text = fact.model_dump_json(indent=2)
        card_text = render_turn_card(fact)
        full_text = render_turn_full(fact)
        card_path, full_path, json_path = run_paths.turn_paths(fact.turn_id)
        json_path.write_text(fact_json_text + "\n", encoding="utf-8")
        card_path.write_text(card_text, encoding="utf-8")
        full_path.write_text(full_text, encoding="utf-8")
        refs_by_turn[fact.turn_id] = make_turn_ref(
            run_paths=run_paths,
            turn_id=fact.turn_id,
            session_id_hash=fact.session_id_hash,
            fact_json=fact_json_text + "\n",
            card_md=card_text,
            full_md=full_text,
            bucket_flags=list(fact.mcp_burden.issue_flags),
        )

    source_by_session = _source_by_session(session_files, facts)
    indexes: list[SessionIndex] = []
    for session_id_hash, items in sorted(by_session.items()):
        source_file = source_by_session.get(session_id_hash, "")
        source_file_hash = items[0].source_file_hash if items else stable_hash(source_file)
        index = SessionIndex(
            session_id_hash=session_id_hash,
            source_file_hash=source_file_hash,
            source_file=source_file,
            turn_count=len(items),
            turns=[refs_by_turn[item.turn_id] for item in sorted(items, key=lambda item: item.turn_index)],
        )
        indexes.append(index)
        (run_paths.facts_sessions / f"session_{session_id_hash}.index.json").write_text(
            index.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    return CollectResult(facts=facts, session_indexes=indexes)


def extract_turns_from_file(path: Path, *, safety: SafetyPolicy) -> list[Turn]:
    source_file_hash = stable_hash(str(path.expanduser().resolve()))
    events = list(iter_events(path, source_file_hash=source_file_hash, safety=safety))
    if not events:
        return []
    session_id = path.stem
    for event in events:
        if event.payload_type == "session_meta":
            session_id = str(event.payload.get("id") or session_id)
            break
    session_id_hash = stable_hash(("session_meta", session_id))
    prefer_event_user = any(event.payload_type == "user_message" for event in events)
    current: Turn | None = None
    turns: list[Turn] = []
    turn_index = -1
    for event in events:
        if event.payload_type == "session_meta":
            continue
        if is_user_turn_boundary(event, prefer_event_user=prefer_event_user):
            if current is not None:
                current.reaction = event
                turns.append(current)
            turn_index += 1
            current = Turn(
                session_id=session_id,
                session_id_hash=session_id_hash,
                source_file=path,
                source_file_hash=source_file_hash,
                turn_index=turn_index,
                trigger=event,
            )
            continue
        if current is not None:
            current.events.append(event)
    if current is not None:
        turns.append(current)
    return turns


def iter_events(path: Path, *, source_file_hash: str, safety: SafetyPolicy) -> Iterator[RawEvent]:
    for line_no, record in iter_json_records(path):
        if not isinstance(record, dict):
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        record_type = str(record.get("type") or "")
        if record_type == "session_meta":
            payload_type = "session_meta"
            role = ""
            payload = payload if payload else record
        elif record_type == "turn_context":
            payload_type = "turn_context"
            role = ""
        else:
            payload_type = str(payload.get("type") or "")
            role = str(payload.get("role") or "")
        raw_ref = safety.raw_ref(
            source_type=SourceType.CODEX_JSONL,
            source_id=(source_file_hash, line_no, payload_type),
            locator={"file": str(path), "line": line_no},
            sensitivity=SensitivityLevel.S2,
        )
        yield RawEvent(
            line_no=line_no,
            timestamp=str(
                record.get("timestamp")
                or payload.get("started_at")
                or payload.get("completed_at")
                or ""
            ),
            record_type=record_type,
            payload_type=payload_type,
            role=role,
            payload=payload,
            raw_ref=raw_ref.model_dump(mode="json"),
        )


def is_user_turn_boundary(event: RawEvent, *, prefer_event_user: bool) -> bool:
    if prefer_event_user:
        return event.payload_type == "user_message"
    return event.payload_type == "message" and event.role == "user"


def turn_to_fact(turn: Turn, *, reviewer_pack: ReviewerPack) -> TurnFact:
    output_events = collect_output_events(turn.events)
    final_answer = final_answer_block(turn.events)
    tool_calls = collect_tool_calls(
        turn.events,
        output_events,
        reviewer_pack=reviewer_pack,
        final_answer_text=final_answer.text,
    )
    timeline = build_timeline(turn.events, tool_calls, final_answer_text=final_answer.text)
    mcp_sequence = [call for call in tool_calls if call.tool_family == "mcp"]
    adopted = build_adopted_evidence(mcp_sequence)
    burden = build_mcp_burden(mcp_sequence)
    raw_refs = [turn.trigger.raw_ref]
    raw_refs.extend(item.raw_ref for item in timeline)
    raw_refs.extend(call.raw_ref for call in tool_calls)
    raw_refs.extend(call.result_raw_ref for call in tool_calls if call.result_raw_ref)
    if final_answer.raw_ref:
        raw_refs.append(final_answer.raw_ref)
    if turn.reaction is not None:
        raw_refs.append(turn.reaction.raw_ref)
    timestamps = [item for item in [turn.trigger.timestamp, *(event.timestamp for event in turn.events)] if item]
    if turn.reaction and turn.reaction.timestamp:
        timestamps.append(turn.reaction.timestamp)
    turn_id = stable_hash(
        (
            str(turn.source_file.expanduser().resolve()),
            turn.trigger.line_no,
            turn.trigger.timestamp,
            turn.turn_index,
        )
    )
    next_user = text_block(turn.reaction) if turn.reaction else TextBlock()
    next_reaction = classify_next_user_reaction(next_user.text)
    return TurnFact(
        turn_id=f"turn_{turn_id}",
        session_id_hash=turn.session_id_hash,
        source_file_hash=turn.source_file_hash,
        turn_index=turn.turn_index,
        time_range={
            "start": min(timestamps) if timestamps else turn.trigger.timestamp,
            "end": max(timestamps) if timestamps else turn.trigger.timestamp,
        },
        user=text_block(turn.trigger),
        timeline=timeline,
        tool_calls=tool_calls,
        mcp_call_sequence=mcp_sequence,
        mcp_burden=burden,
        adopted_evidence=adopted,
        final_answer=final_answer,
        next_user=next_user,
        raw_refs=dedupe_refs(raw_refs),
        derived={
            "event_counts": {
                "assistant_messages": sum(1 for item in timeline if item.kind == "assistant_message"),
                "tool_calls": len(tool_calls),
                "mcp_tool_calls": len(mcp_sequence),
                "shell_tool_calls": sum(1 for item in tool_calls if item.tool_family == "shell"),
            },
            "next_user_reaction": next_reaction,
            "tool_families": dict(Counter(item.tool_family for item in tool_calls)),
            "tools": dict(Counter(item.tool_name for item in tool_calls)),
            "shell_fallback_after_mcp": has_shell_fallback_after_mcp(tool_calls),
        },
    )


def collect_output_events(events: list[RawEvent]) -> dict[str, dict[str, Any]]:
    outputs: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.payload_type == "mcp_tool_call_end":
            call_id = str(event.payload.get("call_id") or "")
            if call_id:
                outputs[stable_hash(call_id)] = {
                    "source": "mcp_tool_call_end",
                    "raw_ref": event.raw_ref,
                    "summary": summarize_tool_result(event.payload.get("result")),
                }
        elif event.payload_type == "function_call_output":
            call_id = str(event.payload.get("call_id") or "")
            if call_id:
                outputs.setdefault(
                    stable_hash(call_id),
                    {
                        "source": "function_call_output",
                        "raw_ref": event.raw_ref,
                        "summary": summarize_tool_result(
                            event.payload.get("output")
                            or event.payload.get("result")
                            or event.payload.get("content")
                        ),
                    },
                )
    return outputs


def collect_tool_calls(
    events: list[RawEvent],
    outputs: dict[str, dict[str, Any]],
    *,
    reviewer_pack: ReviewerPack,
    final_answer_text: str,
) -> list[ToolCallFact]:
    calls: list[ToolCallFact] = []
    for event in events:
        if event.payload_type != "function_call":
            continue
        call_id = str(event.payload.get("call_id") or event.payload.get("id") or f"line-{event.line_no}")
        tool_name = normalize_tool_name(event.payload.get("name"))
        tool_family = classify_tool_family(tool_name, reviewer_pack)
        output = outputs.get(stable_hash(call_id))
        summary = output["summary"] if output else {"status": "missing_output", "shape": "missing"}
        args_summary = summarize_args(parse_arguments(event.payload.get("arguments")))
        matches = match_call_to_final_answer(tool_family.value, args_summary, summary, final_answer_text)
        role, confidence, method = classify_evidence_role(tool_family.value, summary, matches)
        calls.append(
            ToolCallFact(
                index=len(calls) + 1,
                tool_name=tool_name,
                tool_family=tool_family.value,
                args_summary=args_summary,
                result_summary=summary,
                result_source=output["source"] if output else None,
                evidence_role=role,
                evidence_role_confidence=confidence,
                evidence_role_method=method,
                evidence_matches=matches,
                raw_ref=event.raw_ref,
                result_raw_ref=output["raw_ref"] if output else None,
            )
        )
    return calls


def build_timeline(
    events: list[RawEvent],
    tool_calls: list[ToolCallFact],
    *,
    final_answer_text: str,
) -> list[TimelineItem]:
    timeline: list[TimelineItem] = []
    final_marker = comparable_text(final_answer_text)
    call_index = 0
    for event in events:
        if is_assistant_message_event(event):
            text = event_text(event)
            if comparable_text(text) == final_marker:
                continue
            if text.strip():
                timeline.append(
                    TimelineItem(
                        kind="assistant_message",
                        timestamp=event.timestamp,
                        text=text,
                        raw_ref=event.raw_ref,
                    )
                )
        elif event.payload_type == "function_call":
            call = tool_calls[call_index] if call_index < len(tool_calls) else None
            call_index += 1
            timeline.append(
                TimelineItem(
                    kind="tool_call",
                    timestamp=event.timestamp,
                    tool_name=call.tool_name if call else normalize_tool_name(event.payload.get("name")),
                    tool_family=call.tool_family if call else "other",
                    raw_ref=event.raw_ref,
                )
            )
    return timeline


def final_answer_block(events: list[RawEvent]) -> TextBlock:
    task_complete = next((event for event in reversed(events) if event.payload_type == "task_complete"), None)
    if task_complete is not None:
        text = str(task_complete.payload.get("last_agent_message") or "")
        if text:
            return TextBlock(text=text, char_count=len(text), raw_ref=task_complete.raw_ref)
    for event in reversed(events):
        if is_assistant_message_event(event):
            text = event_text(event)
            return TextBlock(text=text, char_count=len(text), raw_ref=event.raw_ref)
    return TextBlock()


def build_mcp_burden(mcp_calls: list[ToolCallFact]) -> McpBurden:
    counts = Counter(call.tool_name for call in mcp_calls)
    repeated = {tool: count for tool, count in counts.items() if count >= 3}
    failed = sum(1 for call in mcp_calls if call.evidence_role == "failed_or_detour")
    used = sum(1 for call in mcp_calls if call.evidence_role in {"used_in_final", "likely_used"})
    low_level = sum(1 for call in mcp_calls if call.tool_name in LOW_LEVEL_MCP_TOOLS)
    flags: list[str] = []
    if len(mcp_calls) > 2:
        flags.append("mcp_calls_exceed_two")
    if repeated:
        flags.append("repeated_same_tool")
    if failed:
        flags.append("mcp_errors_or_detours")
    if low_level and low_level >= max(2, len(mcp_calls) // 2):
        flags.append("low_level_search_dominates")
    if mcp_calls and not used:
        flags.append("no_detected_adopted_evidence")
    return McpBurden(
        total_mcp_calls=len(mcp_calls),
        used_in_final_calls=used,
        failed_or_detour_calls=failed,
        low_level_search_calls=low_level,
        repeated_calls_by_tool=repeated,
        issue_flags=flags,
    )


def build_adopted_evidence(mcp_calls: list[ToolCallFact]) -> list[dict[str, Any]]:
    adopted: list[dict[str, Any]] = []
    for call in mcp_calls:
        for match in call.evidence_matches:
            adopted.append(
                {
                    "tool_name": call.tool_name,
                    "tool_call_index": call.index,
                    "evidence_role": call.evidence_role,
                    "evidence_role_confidence": call.evidence_role_confidence,
                    "evidence_role_method": call.evidence_role_method,
                    "match_type": match.get("match_type"),
                    "signal": match.get("signal"),
                    "field": match.get("field"),
                    "value": match.get("value"),
                }
            )
    if not adopted and mcp_calls:
        adopted.append(
            {
                "heuristic_match": "none",
                "note": "No summarized value/status match was detected; this is not proof that MCP output was unused.",
            }
        )
    return adopted


def classify_evidence_role(
    tool_family: str,
    summary: dict[str, Any],
    evidence_matches: list[dict[str, Any]],
) -> tuple[str, str, str]:
    if summary.get("status") == "missing_output":
        return "unknown", "low", "missing_output"
    if result_has_error(summary):
        return "failed_or_detour", "high", "result_error"
    if tool_family != "mcp":
        return "unknown", "low", "not_mcp"
    if evidence_matches:
        method = str(evidence_matches[0].get("match_type") or "heuristic")
        return "used_in_final", "medium", method
    if payload_has_signal(summary):
        return "exploratory", "low", "heuristic"
    return "not_detected_in_final", "low", "heuristic"


def summarize_tool_result(value: object) -> dict[str, Any]:
    payload = normalize_result_payload(value)
    summary: dict[str, Any] = {
        "status": result_status(value, payload),
        "raw_text_omitted": True,
    }
    if isinstance(payload, dict):
        summary["shape"] = "json_object"
        summary["top_level_keys"] = sorted(str(key) for key in payload)[:12]
        counts = observed_counts(payload)
        providers = observed_providers(payload)
        stage_status = observed_stage_status(payload)
        if counts:
            summary["counts"] = counts
        if providers:
            summary["providers"] = providers
        if stage_status:
            summary["stage_status"] = stage_status
        for item in counts:
            field = item.get("field")
            value = item.get("value")
            if field in {"total", "total_hits", "returned", "total_returned", "candidate_count"} and isinstance(value, int):
                summary[str(field)] = value
        for key in ("logs", "samples", "candidates", "timeline"):
            if isinstance(payload.get(key), list):
                summary[f"{key}_count"] = len(payload[key])
        if any(key in payload for key in ERROR_KEYS):
            summary["status"] = "error"
        return summary
    if isinstance(payload, list):
        summary["shape"] = "json_array"
        summary["item_count"] = len(payload)
        return summary
    text = "" if payload is None else str(payload)
    if not text:
        summary["status"] = "empty"
        summary["shape"] = "empty"
        return summary
    summary["shape"] = "text"
    summary["text_char_count"] = len(text)
    return summary


def normalize_result_payload(value: object) -> Any:
    if isinstance(value, dict) and "Ok" in value:
        ok = value.get("Ok")
        if isinstance(ok, dict):
            content = ok.get("content")
            if isinstance(content, list):
                text = "\n".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
                return parse_json_maybe(text)
        return ok
    if isinstance(value, dict) and "Err" in value:
        return value.get("Err")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return parse_json_maybe(value)
    return value


def parse_json_maybe(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return text


def result_has_error(summary: dict[str, Any]) -> bool:
    if summary.get("status") == "error":
        return True
    keys = set(summary.get("top_level_keys") or [])
    return bool(keys & ERROR_KEYS)


def result_status(original: object, payload: object) -> str:
    if isinstance(original, dict):
        if "Err" in original:
            return "error"
        if "Ok" in original:
            return "ok"
    if isinstance(payload, dict) and any(key in payload for key in ERROR_KEYS):
        return "error"
    return "ok"


def observed_counts(payload: object) -> list[dict[str, Any]]:
    counts: list[dict[str, Any]] = []
    for path, value in walk_json_scalars(payload):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        field = ".".join(path)
        if path and (
            str(path[-1]).lower() in {"count", "total", "total_hits", "returned", "total_returned", "candidate_count"}
            or str(path[-1]).lower().endswith("_count")
        ):
            counts.append({"field": field, "value": value})
    return counts[:24]


def observed_providers(payload: object) -> list[str]:
    providers: list[str] = []
    for path, value in walk_json_scalars(payload):
        if not isinstance(value, str):
            continue
        if path and "provider" in str(path[-1]).lower() and value.strip():
            providers.append(value.strip())
    return sorted(set(providers))[:12]


def observed_stage_status(payload: object) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for path, value in walk_json_scalars(payload):
        field = ".".join(path)
        if "stage_status" in field and isinstance(value, str | int | float | bool):
            statuses.append({"field": field, "value": value})
    return statuses[:24]


def walk_json_scalars(value: object, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], object]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_json_scalars(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_json_scalars(item, (*path, str(index)))
    else:
        yield path, value


def payload_has_signal(summary: dict[str, Any]) -> bool:
    return bool(
        summary.get("counts")
        or summary.get("providers")
        or summary.get("stage_status")
        or summary.get("total") is not None
        or summary.get("total_returned") is not None
    )


def match_call_to_final_answer(
    tool_family: str,
    args_summary: dict[str, Any],
    summary: dict[str, Any],
    final_answer_text: str,
) -> list[dict[str, Any]]:
    if tool_family != "mcp" or not final_answer_text:
        return []
    final_norm = normalize_for_match(final_answer_text)
    matches: list[dict[str, Any]] = []
    for provider in summary.get("providers") or []:
        provider_text = str(provider).strip()
        if provider_text and provider_text.lower() in final_norm:
            matches.append(
                {
                    "match_type": "exact_value_match",
                    "signal": provider_text,
                    "field": "provider",
                    "value": provider_text,
                }
            )
    context_terms = context_terms_for_args(args_summary)
    for count in summary.get("counts") or []:
        value = count.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        value_text = str(int(value)) if isinstance(value, int) or float(value).is_integer() else str(value)
        if value_near_context(final_norm, value_text, context_terms):
            matches.append(
                {
                    "match_type": "exact_value_match",
                    "signal": value_text,
                    "field": count.get("field"),
                    "value": value,
                }
            )
        elif value == 0 and negative_zero_match(final_norm, context_terms):
            matches.append(
                {
                    "match_type": "negative_match",
                    "signal": "zero_result",
                    "field": count.get("field"),
                    "value": value,
                }
            )
    for item in summary.get("stage_status") or []:
        field = str(item.get("field") or "")
        value = str(item.get("value") or "")
        field_tail = field.rsplit(".", 1)[-1].lower()
        if field_tail and field_tail in final_norm:
            matches.append(
                {
                    "match_type": "source_claim_match",
                    "signal": field_tail,
                    "field": field,
                    "value": item.get("value"),
                }
            )
        if value and value.lower() in final_norm:
            matches.append(
                {
                    "match_type": "exact_value_match",
                    "signal": value,
                    "field": field,
                    "value": item.get("value"),
                }
            )
    return dedupe_dicts(matches)


def context_terms_for_args(args_summary: dict[str, Any]) -> list[str]:
    args_text = normalize_for_match(" ".join(str(value) for value in args_summary.values()))
    candidates = {
        "chunk": ["chunk", "流式发送"],
        "frame": ["frame", "数字人", "驱动帧"],
        "ws.send": ["ws.send", "客户端", "下发客户端"],
        "provider": ["provider", "厂商"],
        "vendor_output": ["vendor_output"],
        "wait_msg": ["wait_msg"],
        "audio_size": ["audio_size"],
        "task_continued": ["task_continued"],
        "task_finished": ["task_finished"],
    }
    terms: list[str] = []
    for marker, aliases in candidates.items():
        if marker in args_text:
            terms.extend(aliases)
    return sorted(set(terms))


def value_near_context(final_norm: str, value_text: str, context_terms: list[str], *, window: int = 80) -> bool:
    if not context_terms:
        return False
    pattern = rf"(?<!\d){re.escape(value_text)}(?!\d)"
    for match in re.finditer(pattern, final_norm):
        start = max(0, match.start() - window)
        end = min(len(final_norm), match.end() + window)
        neighborhood = final_norm[start:end]
        if any(term in neighborhood for term in context_terms):
            return True
    return False


def negative_zero_match(final_norm: str, context_terms: list[str]) -> bool:
    negative_terms = ("没有", "没查到", "查不到", "为 0", "-> 0", "=0", " 0 ")
    return any(term in final_norm for term in negative_terms) and value_near_context(
        final_norm,
        "0",
        context_terms,
        window=100,
    )


def normalize_for_match(text: str) -> str:
    return " ".join(str(text).lower().split())


def dedupe_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def summarize_args(arguments: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in sorted(arguments.items()):
        if isinstance(value, (int, float, bool)) or value is None:
            result[key] = value
        elif isinstance(value, str):
            result[key] = summarize_text(value, max_chars=360)
        else:
            result[key] = {"kind": type(value).__name__, "hash": stable_hash(value)}
    return result


def text_block(event: RawEvent | None) -> TextBlock:
    if event is None:
        return TextBlock()
    text = event_text(event)
    return TextBlock(text=text, char_count=len(text), raw_ref=event.raw_ref)


def event_text(event: RawEvent) -> str:
    if event.payload_type in {"user_message", "agent_message"}:
        return str(event.payload.get("message") or "")
    return flatten_text(event.payload.get("content") or event.payload.get("message"))


def is_assistant_message_event(event: RawEvent) -> bool:
    return (event.payload_type == "message" and event.role == "assistant") or event.payload_type == "agent_message"


def comparable_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def classify_next_user_reaction(text: str) -> str:
    lowered = text.lower()
    if not lowered:
        return "none"
    if any(token in lowered for token in ("不对", "不是", "错", "重新", "弄错")):
        return "correction_candidate"
    if any(token in lowered for token in ("再查", "继续", "补充", "再看", "换个")):
        return "retry_candidate"
    if any(token in lowered for token in ("ok", "可以", "按这个", "继续推进")):
        return "proceed_candidate"
    return "new_task_candidate"


def has_shell_fallback_after_mcp(tool_calls: list[ToolCallFact]) -> bool:
    seen_mcp = False
    for call in tool_calls:
        if call.tool_family == "mcp":
            seen_mcp = True
        if call.tool_family == "shell" and seen_mcp:
            return True
    return False


def dedupe_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ref in refs:
        marker = json.dumps(ref, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(ref)
    return unique


def _source_by_session(session_files: Iterable[Path], facts: list[TurnFact]) -> dict[str, str]:
    source_by_hash = {fact.source_file_hash: "" for fact in facts}
    for path in session_files:
        source_by_hash[stable_hash(str(path.expanduser().resolve()))] = str(path)
    return {
        fact.session_id_hash: source_by_hash.get(fact.source_file_hash, "")
        for fact in facts
    }
