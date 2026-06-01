from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from session_review.config import ReviewerPack
from session_review.models import (
    AgentEventKind,
    CaseOutcome,
    Confidence,
    FindingType,
    FindingVerdict,
    ReviewCase,
    ReviewFinding,
    ReviewPacket,
    TargetType,
    ToolFamily,
)
from session_review.safety import stable_hash


ReviewerId = str


def run_reviewers(
    *,
    cases: list[ReviewCase],
    packets: list[ReviewPacket],
    enabled: Iterable[str] | None = None,
    reviewer_pack: ReviewerPack | None = None,
    min_cases_for_proposal: int = 3,
) -> list[ReviewFinding]:
    """Run the deterministic v0 reviewer pack.

    v0 deliberately keeps reviewers conservative. They emit evidence-backed
    findings, not final workflow changes.
    """
    enabled_set = set(enabled or [])
    packet_by_case = {packet.case_id: packet for packet in packets}
    findings: list[ReviewFinding] = []

    if "mcp_efficacy" in enabled_set:
        findings.extend(_review_mcp_efficacy(cases))
    if "skill_utility" in enabled_set:
        findings.extend(_review_skill_utility(cases))
    if "subagent_value" in enabled_set:
        findings.extend(_review_subagent_value(cases))
    if "shell_fallback" in enabled_set:
        findings.extend(_review_shell_fallback(cases, packet_by_case))
    if "path_stability" in enabled_set:
        findings.extend(
            _review_path_stability(
                cases,
                reviewer_pack=reviewer_pack or ReviewerPack(),
                min_cases_for_proposal=min_cases_for_proposal,
            )
        )
    return findings


def _review_mcp_efficacy(cases: list[ReviewCase]) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for case in cases:
        if not _has_family(case, ToolFamily.MCP):
            continue
        if "mcp_empty_result" in case.recovery_path or "shell_fallback_after_mcp" in case.recovery_path:
            findings.append(
                _finding(
                    case=case,
                    reviewer_id="mcp_efficacy",
                    finding_type=FindingType.MCP_EFFICACY,
                    verdict=FindingVerdict.INEFFECTIVE,
                    confidence=Confidence.MEDIUM,
                    rationale=(
                        "MCP did not close the evidence path; the case needed an empty-result "
                        "recovery or a shell/source fallback."
                    ),
                    suggested_target=TargetType.MCP_TOOL,
                )
            )
        elif _is_resolved(case):
            findings.append(
                _finding(
                    case=case,
                    reviewer_id="mcp_efficacy",
                    finding_type=FindingType.MCP_EFFICACY,
                    verdict=FindingVerdict.HELPFUL,
                    confidence=Confidence.MEDIUM,
                    rationale="MCP participated in a resolved evidence path without recorded recovery.",
                    suggested_target=TargetType.NONE,
                )
            )
        else:
            findings.append(
                _finding(
                    case=case,
                    reviewer_id="mcp_efficacy",
                    finding_type=FindingType.MCP_EFFICACY,
                    verdict=FindingVerdict.INSUFFICIENT_EVIDENCE,
                    confidence=Confidence.LOW,
                    rationale="MCP was used, but the session does not prove whether it reduced uncertainty.",
                    suggested_target=TargetType.NONE,
                )
            )
    return findings


def _review_skill_utility(cases: list[ReviewCase]) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for case in cases:
        skill_positions = [
            index for index, event in enumerate(case.events) if _value(event.tool_family) == ToolFamily.SKILL
        ]
        if not skill_positions:
            continue
        last_skill_index = max(skill_positions)
        consumed_after_skill = any(
            event.tool_name and _value(event.tool_family) not in {ToolFamily.SKILL, ToolFamily.OTHER}
            for event in case.events[last_skill_index + 1 :]
        )
        if not consumed_after_skill and not _is_resolved(case):
            findings.append(
                _finding(
                    case=case,
                    reviewer_id="skill_utility",
                    finding_type=FindingType.SKILL_UTILITY,
                    verdict=FindingVerdict.INEFFECTIVE,
                    confidence=Confidence.MEDIUM,
                    rationale="A skill was triggered, but no later tool/evidence step consumed it.",
                    suggested_target=TargetType.SKILL,
                )
            )
        elif _is_resolved(case):
            findings.append(
                _finding(
                    case=case,
                    reviewer_id="skill_utility",
                    finding_type=FindingType.SKILL_UTILITY,
                    verdict=FindingVerdict.HELPFUL,
                    confidence=Confidence.LOW,
                    rationale="A skill appeared in a resolved path; v0 cannot prove causality.",
                    suggested_target=TargetType.NONE,
                )
            )
        else:
            findings.append(
                _finding(
                    case=case,
                    reviewer_id="skill_utility",
                    finding_type=FindingType.SKILL_UTILITY,
                    verdict=FindingVerdict.NOISY,
                    confidence=Confidence.LOW,
                    rationale="A skill was followed by more exploration, but the outcome stayed unclear.",
                    suggested_target=TargetType.SKILL,
                )
            )
    return findings


def _review_subagent_value(cases: list[ReviewCase]) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for case in cases:
        result_positions = [
            index
            for index, event in enumerate(case.events)
            if event.kind == AgentEventKind.SUBAGENT_RESULT or _value(event.tool_family) == ToolFamily.SUBAGENT
        ]
        if not result_positions:
            continue
        last_result = max(result_positions)
        followup_tool_calls = [
            event
            for event in case.events[last_result + 1 :]
            if event.tool_name and _value(event.tool_family) not in {ToolFamily.SUBAGENT, ToolFamily.OTHER}
        ]
        if len(followup_tool_calls) >= 2:
            verdict = FindingVerdict.NOISY
            rationale = "Subagent output did not end the exploration; the main thread repeated evidence work."
            target = TargetType.SUBAGENT_PATTERN
            confidence = Confidence.MEDIUM
        elif _is_resolved(case) and not followup_tool_calls:
            verdict = FindingVerdict.HELPFUL
            rationale = "Subagent output appears to have closed the case without repeated exploration."
            target = TargetType.NONE
            confidence = Confidence.LOW
        elif not _is_resolved(case):
            verdict = FindingVerdict.INSUFFICIENT_EVIDENCE
            rationale = "Subagent was used, but v0 cannot prove a complexity reduction."
            target = TargetType.SUBAGENT_PATTERN
            confidence = Confidence.LOW
        else:
            verdict = FindingVerdict.NOISY
            rationale = "Subagent was followed by extra main-thread work; value needs manual review."
            target = TargetType.SUBAGENT_PATTERN
            confidence = Confidence.LOW
        findings.append(
            _finding(
                case=case,
                reviewer_id="subagent_value",
                finding_type=FindingType.SUBAGENT_VALUE,
                verdict=verdict,
                confidence=confidence,
                rationale=rationale,
                suggested_target=target,
            )
        )
    return findings


def _review_shell_fallback(
    cases: list[ReviewCase],
    packet_by_case: dict[str, ReviewPacket],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for case in cases:
        if "shell_fallback_after_mcp" not in case.recovery_path:
            continue
        packet = packet_by_case.get(case.case_id)
        wrong_track_rounds = int((packet.route_quality if packet else {}).get("wrong_track_rounds") or 0)
        confidence = Confidence.HIGH if wrong_track_rounds or "mcp_empty_result" in case.recovery_path else Confidence.MEDIUM
        findings.append(
            _finding(
                case=case,
                reviewer_id="shell_fallback",
                finding_type=FindingType.SHELL_FALLBACK,
                verdict=FindingVerdict.HELPFUL if _is_resolved(case) else FindingVerdict.NOISY,
                confidence=confidence,
                rationale=(
                    "Shell/source fallback was needed after MCP. This is a candidate signal for "
                    "either improving MCP coverage or recommending source-first for this task."
                ),
                suggested_target=TargetType.MCP_TOOL,
            )
        )
    return findings


def _review_path_stability(
    cases: list[ReviewCase],
    *,
    reviewer_pack: ReviewerPack,
    min_cases_for_proposal: int,
) -> list[ReviewFinding]:
    config = reviewer_pack.path_stability()
    groups: dict[str, list[ReviewCase]] = defaultdict(list)
    for case in cases:
        if not case.tool_path:
            continue
        key_parts: list[str] = []
        for field in config.group_by:
            if field == "task_family":
                key_parts.append(case.task_family)
            elif field in {"recommended_path", "tool_path"}:
                key_parts.append("->".join(case.tool_path))
            else:
                key_parts.append(str(getattr(case, field, "unknown")))
        groups["|".join(key_parts)].append(case)

    findings: list[ReviewFinding] = []
    by_task_family = Counter(case.task_family for case in cases if case.tool_path)
    for key, grouped_cases in groups.items():
        if len(grouped_cases) < min_cases_for_proposal:
            continue
        task_family = grouped_cases[0].task_family
        denominator = max(1, by_task_family[task_family])
        consistency = len(grouped_cases) / denominator
        if consistency < config.min_consistency_ratio:
            continue
        evidence_refs = [case.raw_refs[0] for case in grouped_cases[:5] if case.raw_refs]
        findings.append(
            ReviewFinding(
                finding_id=stable_hash(("path_stability", key, [case.case_id for case in grouped_cases])),
                case_id=grouped_cases[0].case_id,
                reviewer_id="path_stability",
                type=FindingType.PATH_STABILITY,
                verdict=FindingVerdict.HELPFUL,
                confidence=Confidence.HIGH if len(grouped_cases) >= min_cases_for_proposal * 2 else Confidence.MEDIUM,
                evidence_refs=evidence_refs,
                rationale=(
                    f"Stable path observed for {len(grouped_cases)} {task_family} cases "
                    f"(consistency={consistency:.2f}): {' -> '.join(grouped_cases[0].tool_path)}."
                ),
                suggested_target=TargetType.DEBUG_RUNBOOK_SEED,
            )
        )
    return findings


def _finding(
    *,
    case: ReviewCase,
    reviewer_id: ReviewerId,
    finding_type: FindingType,
    verdict: FindingVerdict,
    confidence: Confidence,
    rationale: str,
    suggested_target: TargetType,
) -> ReviewFinding:
    return ReviewFinding(
        finding_id=stable_hash((reviewer_id, case.case_id, finding_type, verdict, rationale)),
        case_id=case.case_id,
        reviewer_id=reviewer_id,
        type=finding_type,
        verdict=verdict,
        confidence=confidence,
        evidence_refs=case.raw_refs[:5],
        rationale=rationale,
        suggested_target=suggested_target,
    )


def _has_family(case: ReviewCase, family: ToolFamily) -> bool:
    return any(_value(event.tool_family) == family for event in case.events if event.tool_name)


def _is_resolved(case: ReviewCase) -> bool:
    return _value(case.outcome) == CaseOutcome.RESOLVED


def _value(item: object) -> object:
    return getattr(item, "value", item)
