from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from session_review.adapters.codex_jsonl import flatten_text, normalize_tool_name, parse_arguments
from session_review.models import AgentEventKind, RawRef, SensitivityLevel, SourceType, ToolFamily
from session_review.safety import SafetyPolicy, max_sensitivity, stable_hash, summarize_sensitive_text

from .models import (
    SubjectEpisode,
    SubjectEventSummary,
    SubjectReviewPack,
)


@dataclass
class SubjectCollectResult:
    episodes: list[SubjectEpisode] = field(default_factory=list)
    parser_warnings: list[str] = field(default_factory=list)
    redaction_warnings: list[str] = field(default_factory=list)


@dataclass
class _RawEvent:
    index: int
    line_no: int
    session_id: str
    kind: str
    role: str | None
    text: str
    tool_name: str | None
    tool_family: ToolFamily
    args: dict[str, Any]
    output: str
    raw_ref: RawRef
    safety_level: SensitivityLevel
    args_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    signal_ids: list[str] = field(default_factory=list)


@dataclass
class _Anchor:
    index: int
    signals: set[str]
    score: int


def collect_subject_episodes(
    paths: Iterable[Path],
    *,
    pack: SubjectReviewPack,
    safety: SafetyPolicy,
) -> SubjectCollectResult:
    result = SubjectCollectResult()
    for path in paths:
        parsed = _collect_file(path, pack=pack, safety=safety)
        result.episodes.extend(parsed.episodes)
        result.parser_warnings.extend(parsed.parser_warnings)
        result.redaction_warnings.extend(parsed.redaction_warnings)
    result.redaction_warnings.extend(safety.redaction_warnings)
    return result


def _collect_file(path: Path, *, pack: SubjectReviewPack, safety: SafetyPolicy) -> SubjectCollectResult:
    result = SubjectCollectResult()
    events, project_hint = _read_events(path, safety=safety, result=result)
    if not events:
        return result
    anchors = _find_anchors(events, pack)
    merged = _merge_anchors(anchors, pack.context_window.merge_distance_events)
    for anchor_group in merged:
        result.episodes.append(_episode_from_anchor_group(events, anchor_group, pack, project_hint))
    return result


def _read_events(
    path: Path,
    *,
    safety: SafetyPolicy,
    result: SubjectCollectResult,
) -> tuple[list[_RawEvent], dict[str, Any]]:
    session_id = stable_hash(("session_path", path.stem))
    cwd_hash: str | None = None
    events: list[_RawEvent] = []
    pending_tools: dict[str, tuple[str, ToolFamily]] = {}
    for line_no, record in _iter_json_records(path):
        if not isinstance(record, dict):
            result.parser_warnings.append(f"{_path_label(path)}:{line_no}: non-object JSON record")
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "session_meta":
            if payload.get("id"):
                session_id = stable_hash(("session_meta", payload.get("id")))
            if payload.get("cwd"):
                cwd_hash = stable_hash(str(payload.get("cwd")))
            continue
        payload_type = payload.get("type")
        role = payload.get("role")
        if payload_type == "message" and role in {"user", "assistant"}:
            text = flatten_text(payload.get("content") or payload.get("message"))
            raw_ref = _raw_ref(safety, path=path, line_no=line_no, source_id=(session_id, line_no, role))
            events.append(
                _RawEvent(
                    index=len(events),
                    line_no=line_no,
                    session_id=session_id,
                    kind=AgentEventKind.USER_MESSAGE if role == "user" else AgentEventKind.ASSISTANT_MESSAGE,
                    role=str(role),
                    text=text,
                    tool_name=None,
                    tool_family=ToolFamily.OTHER,
                    args={},
                    output="",
                    raw_ref=raw_ref,
                    safety_level=SensitivityLevel.S2,
                )
            )
            continue
        if payload_type == "function_call":
            tool_name = normalize_tool_name(payload.get("name"))
            tool_family = _subject_tool_family(tool_name)
            call_id = str(payload.get("call_id") or payload.get("id") or f"{path}:{line_no}")
            args = parse_arguments(payload.get("arguments"))
            args_summary, sensitivity = safety.redact_mapping(args)
            raw_ref = _raw_ref(
                safety,
                path=path,
                line_no=line_no,
                source_id=(session_id, line_no, call_id, "call"),
                sensitivity=sensitivity,
            )
            pending_tools[call_id] = (tool_name, tool_family)
            events.append(
                _RawEvent(
                    index=len(events),
                    line_no=line_no,
                    session_id=session_id,
                    kind=AgentEventKind.TOOL_CALL,
                    role=None,
                    text="",
                    tool_name=tool_name,
                    tool_family=tool_family,
                    args=args,
                    output="",
                    raw_ref=raw_ref,
                    safety_level=sensitivity,
                    args_summary=args_summary,
                )
            )
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
                source_id=(session_id, line_no, call_id, "result"),
                sensitivity=sensitivity,
            )
            events.append(
                _RawEvent(
                    index=len(events),
                    line_no=line_no,
                    session_id=session_id,
                    kind=AgentEventKind.TOOL_RESULT,
                    role=None,
                    text="",
                    tool_name=tool_name,
                    tool_family=tool_family,
                    args={},
                    output=output,
                    raw_ref=raw_ref,
                    safety_level=sensitivity,
                    output_summary=output_summary,
                )
            )
    project_hint = {"session_file_hash": stable_hash(str(path))}
    if cwd_hash:
        project_hint["cwd_hash"] = cwd_hash
    return events, project_hint


def _find_anchors(events: list[_RawEvent], pack: SubjectReviewPack) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    direct_indices: set[int] = set()
    for event in events:
        signals: set[str] = set()
        if _direct_usage_match(event, pack):
            signals.add("direct_usage")
            direct_indices.add(event.index)
        signals.update(_contextual_need_signals(event, pack))
        signals.update(_signal_pack_signals(event, pack))
        if any(signal.startswith("signal_pack:positive:") for signal in signals):
            if any(":tool:" in signal or ":command:" in signal for signal in signals):
                signals.add("direct_usage")
                direct_indices.add(event.index)
            else:
                signals.add("contextual_need:signal_pack")
        if signals:
            event.signal_ids = sorted(signals)
            anchors.append(_Anchor(index=event.index, signals=signals, score=_score_signals(signals)))
    anchors.extend(_fallback_anchors(events, pack, direct_indices))
    return sorted(anchors, key=lambda item: item.index)


def _direct_usage_match(event: _RawEvent, pack: SubjectReviewPack) -> bool:
    config = pack.collectors.direct_usage
    tool_name = event.tool_name or ""
    command_text = " ".join(str(value) for value in event.args.values())
    candidates = set(config.tool_names + config.mcp_names + config.skill_names + config.subagent_names)
    if tool_name and any(_contains(tool_name, item) for item in candidates):
        return True
    return any(_contains(command_text, item) for item in config.commands)


def _contextual_need_signals(event: _RawEvent, pack: SubjectReviewPack) -> set[str]:
    text = " ".join([event.text, event.output, " ".join(str(value) for value in event.args.values())])
    signals: set[str] = set()
    for item in pack.collectors.contextual_need.error_signals:
        if _contains(text, item):
            signals.add(f"contextual_need:error:{stable_hash(item, length=8)}")
    for item in pack.collectors.contextual_need.slow_signals:
        if _contains(text, item):
            signals.add(f"contextual_need:slow:{stable_hash(item, length=8)}")
    for item in pack.collectors.contextual_need.user_hint_signals:
        if _contains(text, item):
            signals.add(f"contextual_need:user_hint:{stable_hash(item, length=8)}")
    return signals


def _fallback_anchors(
    events: list[_RawEvent],
    pack: SubjectReviewPack,
    direct_indices: set[int],
) -> list[_Anchor]:
    config = pack.collectors.fallback
    if not config.inefficient_tools:
        return []
    anchors: list[_Anchor] = []
    matched_indices: list[int] = []
    for event in events:
        haystack = " ".join([event.tool_name or "", " ".join(str(value) for value in event.args.values())])
        if any(_contains(haystack, item) for item in config.inefficient_tools):
            matched_indices.append(event.index)
    if len(matched_indices) >= config.retry_threshold and not direct_indices:
        events[matched_indices[-1]].signal_ids = sorted(
            set(events[matched_indices[-1]].signal_ids)
            | {"fallback:repeated_inefficient_path", "missed_opportunity"}
        )
        anchors.append(
            _Anchor(
                index=matched_indices[-1],
                signals={"fallback:repeated_inefficient_path", "missed_opportunity"},
                score=70 if config.missing_direct_usage_penalty else 50,
            )
        )
    return anchors


def _merge_anchors(anchors: list[_Anchor], merge_distance: int) -> list[list[_Anchor]]:
    groups: list[list[_Anchor]] = []
    for anchor in anchors:
        if groups and anchor.index - groups[-1][-1].index <= merge_distance:
            groups[-1].append(anchor)
        else:
            groups.append([anchor])
    return groups


def _episode_from_anchor_group(
    events: list[_RawEvent],
    anchors: list[_Anchor],
    pack: SubjectReviewPack,
    project_hint: dict[str, Any],
) -> SubjectEpisode:
    first = min(anchor.index for anchor in anchors)
    last = max(anchor.index for anchor in anchors)
    before_start = max(0, first - pack.context_window.before_events)
    after_end = min(len(events), last + pack.context_window.after_events + 1)
    before = events[before_start:first]
    subject_events = events[first : last + 1]
    after = events[last + 1 : after_end]
    matched_signals = sorted(
        {signal for anchor in anchors for signal in anchor.signals}
        | {signal for event in before + subject_events + after for signal in event.signal_ids}
    )
    if "direct_usage" not in matched_signals and (
        any(signal.startswith("contextual_need:") for signal in matched_signals)
        or any(signal.startswith("fallback:") for signal in matched_signals)
    ):
        matched_signals.append("missed_opportunity")
        matched_signals = sorted(set(matched_signals))
    outcome_hint = _outcome_hint(events[first:after_end], matched_signals, pack)
    raw_refs = [event.raw_ref for event in before + subject_events + after]
    safety_level = max_sensitivity([event.safety_level for event in before + subject_events + after])
    episode_id = stable_hash((pack.subject.subject_id, events[first].session_id, first, last, matched_signals))
    return SubjectEpisode(
        episode_id=episode_id,
        subject_id=pack.subject.subject_id,
        session_id=events[first].session_id,
        project_hint=project_hint,
        matched_signals=matched_signals,
        relevance_score=min(100, sum(anchor.score for anchor in anchors)),
        before_context=[_summarize_event(event, event.signal_ids) for event in before],
        subject_events=[_summarize_event(event, event.signal_ids or matched_signals) for event in subject_events],
        after_context=[_summarize_event(event, event.signal_ids) for event in after],
        outcome_hint=outcome_hint,
        raw_refs=raw_refs,
        safety_summary={
            "max_sensitivity": safety_level,
            "redaction_status": "fail" if safety_level == SensitivityLevel.S3 else "pass",
        },
    )


def _summarize_event(event: _RawEvent, signal_ids: list[str]) -> SubjectEventSummary:
    text_summary = None
    if event.text:
        text_summary = summarize_sensitive_text(event.text, include_raw_text=False)
    return SubjectEventSummary(
        event_id=stable_hash((event.session_id, event.line_no, event.kind, event.tool_name)),
        kind=str(event.kind),
        tool_name=event.tool_name,
        tool_family=event.tool_family,
        args_summary=event.args_summary,
        output_summary=event.output_summary,
        text_summary=text_summary,
        signal_ids=signal_ids,
        raw_ref=event.raw_ref,
        safety_level=event.safety_level,
    )


def _outcome_hint(events: list[_RawEvent], matched_signals: list[str], pack: SubjectReviewPack) -> str:
    text = " ".join([event.text for event in events] + [event.output for event in events])
    if any(_contains(text, item) for item in pack.objective.success_signals):
        return "improved"
    if any(_contains(text, item) for item in pack.objective.failure_signals):
        return "failed_or_retried"
    if any(signal.startswith("fallback:") or signal == "missed_opportunity" for signal in matched_signals):
        return "missed_opportunity"
    return "unknown"


def _signal_pack_signals(event: _RawEvent, pack: SubjectReviewPack) -> set[str]:
    signal_pack = pack.signal_pack
    if signal_pack is None:
        return set()
    text = " ".join([event.text, event.output, " ".join(str(value) for value in event.args.values())])
    signals: set[str] = set()
    for item in signal_pack.positive_signals.tool_names:
        if _contains(event.tool_name, item):
            signals.add(_signal_id("positive:tool", item))
    for item in signal_pack.positive_signals.commands:
        if _contains(text, item):
            signals.add(_signal_id("positive:command", item))
    for item in signal_pack.positive_signals.skill_names:
        if _contains(text, item) or _contains(event.tool_name, item):
            signals.add(_signal_id("positive:skill", item))
    for item in signal_pack.positive_signals.mcp_names:
        if _contains(text, item) or _contains(event.tool_name, item):
            signals.add(_signal_id("positive:mcp", item))
    for item in signal_pack.positive_signals.subagent_names:
        if _contains(text, item) or _contains(event.tool_name, item):
            signals.add(_signal_id("positive:subagent", item))
    for item in signal_pack.positive_signals.text:
        if _contains(text, item):
            signals.add(_signal_id("positive:text", item))
    for item in signal_pack.positive_signals.error_signals:
        if _contains(text, item):
            signals.add(_signal_id("positive:error", item))
    for item in signal_pack.positive_signals.user_hint_signals:
        if _contains(text, item):
            signals.add(_signal_id("positive:user_hint", item))
    for item in signal_pack.domain_anchors.required_any + signal_pack.domain_anchors.required_all:
        if _contains(text, item):
            signals.add(_signal_id("domain_anchor", item))
    for item in signal_pack.negative_signals.exclude_contexts:
        if _contains(text, item):
            signals.add(_signal_id("negative:exclude_context", item))
    for item in signal_pack.negative_signals.commands:
        if _contains(text, item):
            signals.add(_signal_id("negative:command", item))
    for item in signal_pack.negative_signals.text:
        if _contains(text, item):
            signals.add(_signal_id("negative:text", item))
    for item in signal_pack.ambiguous_terms.terms:
        if _contains(text, item):
            signals.add(_signal_id("ambiguous", item))
    return signals


def _subject_tool_family(tool_name: str) -> ToolFamily:
    if tool_name in {"spawn_agent", "wait_agent", "close_agent"}:
        return ToolFamily.SUBAGENT
    if "skill" in tool_name:
        return ToolFamily.SKILL
    if any(token in tool_name for token in ("exec_command", "bash", "zsh", "shell")):
        return ToolFamily.SHELL
    return ToolFamily.OTHER


def _raw_ref(
    safety: SafetyPolicy,
    *,
    path: Path,
    line_no: int,
    source_id: object,
    sensitivity: SensitivityLevel = SensitivityLevel.S2,
) -> RawRef:
    return safety.raw_ref(
        source_type=SourceType.CODEX_JSONL,
        source_id=source_id,
        locator={"file": str(path), "line": line_no},
        sensitivity=sensitivity,
    )


def _iter_json_records(path: Path):
    try:
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError:
                yield line_no, None
    except OSError:
        yield 0, None


def _contains(haystack: object, needle: object) -> bool:
    needle_text = str(needle or "").strip().lower()
    if not needle_text:
        return False
    return needle_text in str(haystack or "").lower()


def _score_signals(signals: set[str]) -> int:
    score = 0
    if "direct_usage" in signals:
        score += 80
    if any(signal.startswith("contextual_need:error") for signal in signals):
        score += 55
    if any(signal.startswith("contextual_need:slow") for signal in signals):
        score += 45
    if any(signal.startswith("contextual_need:user_hint") for signal in signals):
        score += 60
    if any(signal.startswith("signal_pack:positive:") for signal in signals):
        score += 50
    if any(signal.startswith("signal_pack:domain_anchor:") for signal in signals):
        score += 20
    return min(100, score)


def _path_label(path: Path) -> str:
    return f"session_file:{stable_hash(str(path))}"


def _signal_id(kind: str, value: object) -> str:
    return f"signal_pack:{kind}:{stable_hash(value, length=8)}"
