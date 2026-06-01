from __future__ import annotations

from session_review.candidates import build_candidates, can_export, create_decision
from session_review.config import ReviewProfile, ReviewerPack
from session_review.exporters.debug_runbook import render_debug_runbook_seed
from session_review.models import (
    AgentEvent,
    AgentEventKind,
    CandidateStatus,
    CaseOutcome,
    RawRef,
    ReviewCase,
    TargetType,
    ToolFamily,
)
from session_review.packets import packets_from_cases
from session_review.reviewers import run_reviewers


def test_reviewers_detect_shell_fallback_skill_noise_and_subagent_noise() -> None:
    cases = [
        _case(
            "mcp-shell",
            tool_families=[ToolFamily.MCP, ToolFamily.SHELL],
            tool_names=["inspect_request", "exec_command"],
            recovery_path=["mcp_empty_result", "shell_fallback_after_mcp"],
            outcome=CaseOutcome.RESOLVED,
        ),
        _case(
            "skill-unused",
            tool_families=[ToolFamily.SKILL],
            tool_names=["service-log-triage"],
            outcome=CaseOutcome.UNKNOWN,
        ),
        _case(
            "subagent-repeat",
            tool_families=[ToolFamily.SUBAGENT, ToolFamily.SHELL, ToolFamily.MCP],
            tool_names=["spawn_agent", "exec_command", "inspect_request"],
            subagent_result=True,
            outcome=CaseOutcome.RESOLVED,
        ),
    ]

    findings = run_reviewers(
        cases=cases,
        packets=packets_from_cases(cases),
        enabled=["mcp_efficacy", "skill_utility", "subagent_value", "shell_fallback"],
    )

    by_type = {(finding.type, finding.verdict) for finding in findings}
    assert ("shell_fallback", "helpful") in by_type
    assert ("mcp_efficacy", "ineffective") in by_type
    assert ("skill_utility", "ineffective") in by_type
    assert ("subagent_value", "noisy") in by_type


def test_candidate_lifecycle_requires_manual_staging_before_export() -> None:
    stable_cases = [
        _case(
            f"stable-{index}",
            tool_families=[ToolFamily.MCP],
            tool_names=["inspect_request"],
            outcome=CaseOutcome.RESOLVED,
        )
        for index in range(3)
    ]
    pack = ReviewerPack.model_validate(
        {"reviewers": {"path_stability": {"group_by": ["task_family", "recommended_path"]}}}
    )
    findings = run_reviewers(
        cases=stable_cases,
        packets=packets_from_cases(stable_cases),
        enabled=["path_stability"],
        reviewer_pack=pack,
        min_cases_for_proposal=3,
    )
    profile = ReviewProfile()
    candidates = build_candidates(cases=stable_cases, findings=findings, profile=profile)

    assert findings
    candidate = candidates[0]
    assert candidate.target_type == TargetType.DEBUG_RUNBOOK_SEED
    assert candidate.maturity == CandidateStatus.PROPOSAL
    assert candidate.export_allowed is False

    candidates, reviewed = create_decision(
        candidates=candidates,
        candidate_id=candidate.candidate_id,
        status=CandidateStatus.REVIEWED,
        reason="真实问题，但还缺少 owner/eval 计划。",
    )
    assert not can_export(candidates[0], [reviewed])

    candidates, staged = create_decision(
        candidates=candidates,
        candidate_id=candidate.candidate_id,
        status=CandidateStatus.STAGED,
        reason="已有 owner 和后续 session eval 计划。",
    )
    assert can_export(candidates[0], [reviewed, staged])
    seed = render_debug_runbook_seed(candidate=candidates[0], decision=staged)
    assert "schema_version: debug_runbook_seed.v0" in seed
    assert "inspect_request" in seed


def _case(
    case_id: str,
    *,
    tool_families: list[ToolFamily],
    tool_names: list[str],
    outcome: CaseOutcome,
    recovery_path: list[str] | None = None,
    subagent_result: bool = False,
) -> ReviewCase:
    raw_ref = RawRef(source_id_hash=f"ref-{case_id}")
    events = [
        AgentEvent(
            event_id=f"{case_id}-user",
            session_id="session",
            kind=AgentEventKind.USER_MESSAGE,
            raw_ref=raw_ref,
        )
    ]
    for index, (family, name) in enumerate(zip(tool_families, tool_names, strict=True)):
        kind = AgentEventKind.TOOL_CALL
        if family == ToolFamily.SUBAGENT:
            kind = AgentEventKind.SUBAGENT_SPAWN
        events.append(
            AgentEvent(
                event_id=f"{case_id}-tool-{index}",
                session_id="session",
                kind=kind,
                tool_name=name,
                tool_family=family,
                raw_ref=raw_ref,
            )
        )
        if subagent_result and family == ToolFamily.SUBAGENT:
            events.append(
                AgentEvent(
                    event_id=f"{case_id}-subagent-result",
                    session_id="session",
                    kind=AgentEventKind.SUBAGENT_RESULT,
                    tool_name=name,
                    tool_family=family,
                    raw_ref=raw_ref,
                )
            )
    return ReviewCase(
        case_id=case_id,
        session_id="session",
        case_group_key="tts_provider_verification:inspect_request",
        user_goal_summary="确认 provider",
        task_family="tts_provider_verification",
        expected_data=["provider", "evidence_ref"],
        events=events,
        tool_path=tool_names,
        recovery_path=recovery_path or [],
        outcome=outcome,
        raw_refs=[raw_ref],
    )
