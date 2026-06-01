from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Iterable

from session_review.config import ReviewProfile
from session_review.models import (
    CandidateStatus,
    DecisionAction,
    FindingType,
    FindingVerdict,
    ImprovementCandidate,
    PromotionDecision,
    ReviewCase,
    ReviewFinding,
    SensitivityLevel,
    TargetType,
)
from session_review.safety import stable_hash


def build_candidates(
    *,
    cases: list[ReviewCase],
    findings: list[ReviewFinding],
    profile: ReviewProfile,
    decisions: Iterable[PromotionDecision] | None = None,
) -> list[ImprovementCandidate]:
    case_by_id = {case.case_id: case for case in cases}
    grouped: dict[str, list[ReviewFinding]] = defaultdict(list)
    for finding in findings:
        target = _target_type(finding.suggested_target)
        if target == TargetType.NONE:
            continue
        grouped[_candidate_key(finding, case_by_id)].append(finding)

    latest_decisions = latest_decision_by_candidate(decisions or [])
    candidates: list[ImprovementCandidate] = []
    for key, group in sorted(grouped.items()):
        candidate = _candidate_from_group(key, group, case_by_id, profile)
        decision = latest_decisions.get(candidate.candidate_id)
        if decision:
            candidate = _apply_decision_to_candidate(candidate, decision)
        candidates.append(candidate)
    return candidates


def create_decision(
    *,
    candidates: list[ImprovementCandidate],
    candidate_id: str,
    status: CandidateStatus | str,
    reason: str,
    reviewer: str = "manual",
    required_actions: list[str] | None = None,
) -> tuple[list[ImprovementCandidate], PromotionDecision]:
    target_status = CandidateStatus(str(status))
    if target_status in {CandidateStatus.OBSERVED, CandidateStatus.PROPOSAL}:
        action = DecisionAction.DEFER
    elif target_status in {CandidateStatus.REVIEWED, CandidateStatus.STAGED, CandidateStatus.PROMOTED}:
        action = DecisionAction.PROMOTE if target_status in {CandidateStatus.STAGED, CandidateStatus.PROMOTED} else DecisionAction.DEFER
    else:
        action = DecisionAction.DEPRECATE

    matched = False
    updated: list[ImprovementCandidate] = []
    for candidate in candidates:
        if candidate.candidate_id != candidate_id:
            updated.append(candidate)
            continue
        matched = True
        decision = PromotionDecision(
            decision_id=stable_hash(
                (
                    "decision",
                    candidate_id,
                    target_status,
                    reason,
                    datetime.now(timezone.utc).isoformat(),
                )
            ),
            candidate_id=candidate_id,
            status=target_status,
            decision=action,
            reviewer=reviewer,
            reason=reason,
            required_actions=required_actions or [],
            export_allowed=target_status in {CandidateStatus.STAGED, CandidateStatus.PROMOTED},
        )
        updated.append(_apply_decision_to_candidate(candidate, decision))
    if not matched:
        raise ValueError(f"Unknown candidate: {candidate_id}")
    return updated, decision


def can_export(candidate: ImprovementCandidate, decisions: Iterable[PromotionDecision]) -> bool:
    latest = latest_decision_by_candidate(decisions).get(candidate.candidate_id)
    if not latest:
        return False
    return (
        _status(latest.status) in {CandidateStatus.STAGED, CandidateStatus.PROMOTED}
        and latest.export_allowed
        and candidate.export_allowed
    )


def latest_decision_by_candidate(
    decisions: Iterable[PromotionDecision],
) -> dict[str, PromotionDecision]:
    latest: dict[str, PromotionDecision] = {}
    for decision in decisions:
        current = latest.get(decision.candidate_id)
        if current is None or decision.decided_at >= current.decided_at:
            latest[decision.candidate_id] = decision
    return latest


def render_candidates_markdown(candidates: list[ImprovementCandidate]) -> str:
    lines = [
        "# Improvement Candidates",
        "",
        f"- candidates: {len(candidates)}",
        "",
    ]
    if not candidates:
        lines.append("No candidates.")
        return "\n".join(lines) + "\n"
    for candidate in candidates:
        lines.extend(
            [
                f"## {candidate.candidate_id}",
                "",
                f"- target: `{candidate.target_type}`",
                f"- maturity: `{candidate.maturity}`",
                f"- export_allowed: `{candidate.export_allowed}`",
                f"- support: {len(candidate.supporting_findings)} findings",
                f"- problem: {candidate.problem_pattern}",
                f"- proposed_change: {candidate.proposed_change}",
                f"- recommended_path: {' -> '.join(candidate.recommended_path) or 'unknown'}",
                "",
            ]
        )
    return "\n".join(lines)


def _candidate_from_group(
    key: str,
    findings: list[ReviewFinding],
    case_by_id: dict[str, ReviewCase],
    profile: ReviewProfile,
) -> ImprovementCandidate:
    first = findings[0]
    target = _target_type(first.suggested_target)
    finding_type = _finding_type(first.type)
    support_case_ids = {finding.case_id for finding in findings}
    concrete_cases = [case_by_id[case_id] for case_id in support_case_ids if case_id in case_by_id]
    path_counter = Counter("->".join(case.tool_path) for case in concrete_cases if case.tool_path)
    recommended_path = path_counter.most_common(1)[0][0].split("->") if path_counter else []
    task_family = concrete_cases[0].task_family if concrete_cases else "aggregate"
    secret_leaks = sum(
        1
        for finding in findings
        for ref in finding.evidence_refs
        if _sensitivity(ref.sensitivity) == SensitivityLevel.S3
    )
    if any(_finding_type(finding.type) == FindingType.PATH_STABILITY for finding in findings):
        support_count = max(len(finding.evidence_refs) for finding in findings)
    else:
        support_count = len(support_case_ids)
    maturity = (
        CandidateStatus.PROPOSAL
        if support_count >= profile.thresholds.min_cases_for_proposal
        and secret_leaks <= profile.thresholds.max_secret_leak_count
        else CandidateStatus.OBSERVED
    )
    problem_pattern, proposed_change = _problem_and_change(target, finding_type, task_family)
    trigger_signals = sorted({_finding_type(finding.type).value for finding in findings})
    if any(_verdict(finding.verdict) == FindingVerdict.INEFFECTIVE for finding in findings):
        trigger_signals.append("ineffective_workflow")
    anti_patterns = _anti_patterns(findings, concrete_cases)
    fallback_rules = _fallback_rules(findings)
    candidate_id = stable_hash(("candidate", key, [finding.finding_id for finding in findings]))
    return ImprovementCandidate(
        candidate_id=candidate_id,
        candidate_key=key,
        target_type=target,
        problem_pattern=problem_pattern,
        trigger_signals=trigger_signals,
        proposed_change=proposed_change,
        recommended_path=recommended_path,
        anti_patterns=anti_patterns,
        fallback_rules=fallback_rules,
        supporting_findings=[finding.finding_id for finding in findings],
        maturity=maturity,
        score={
            "coverage": min(100, support_count * 20),
            "consistency": _consistency_score(path_counter, support_count),
            "safety": 100 if secret_leaks == 0 else 0,
            "reuse": 80 if target == TargetType.DEBUG_RUNBOOK_SEED else 60,
        },
        export_allowed=False,
    )


def _candidate_key(finding: ReviewFinding, case_by_id: dict[str, ReviewCase]) -> str:
    target = _target_type(finding.suggested_target).value
    finding_type = _finding_type(finding.type).value
    case = case_by_id.get(finding.case_id)
    if case:
        return f"{target}:{finding_type}:{case.case_group_key}"
    return f"{target}:{finding_type}:{stable_hash(finding.rationale)}"


def _apply_decision_to_candidate(
    candidate: ImprovementCandidate,
    decision: PromotionDecision,
) -> ImprovementCandidate:
    updated = candidate.model_copy(deep=True)
    updated.maturity = decision.status
    updated.export_allowed = decision.export_allowed
    return updated


def _problem_and_change(
    target: TargetType,
    finding_type: FindingType,
    task_family: str,
) -> tuple[str, str]:
    if target == TargetType.MCP_TOOL:
        return (
            f"MCP did not reliably close evidence for `{task_family}`.",
            "Review whether the MCP schema, query path, or documented fallback should change.",
        )
    if target == TargetType.SKILL:
        return (
            f"Skill usage was not clearly consumed for `{task_family}`.",
            "Review whether the skill trigger, context contract, or examples should be tightened.",
        )
    if target == TargetType.SUBAGENT_PATTERN:
        return (
            f"Subagent delegation did not clearly reduce complexity for `{task_family}`.",
            "Review delegation boundaries and acceptance criteria before reusing this pattern.",
        )
    if finding_type == FindingType.PATH_STABILITY:
        return (
            f"Stable debugging path observed for `{task_family}`.",
            "Stage a debug_runbook_seed only after manual review of evidence and failure boundaries.",
        )
    return (
        f"Reusable workflow candidate observed for `{task_family}`.",
        "Review evidence, boundaries, and counterexamples before promotion.",
    )


def _anti_patterns(findings: list[ReviewFinding], cases: list[ReviewCase]) -> list[str]:
    items: set[str] = set()
    for case in cases:
        if "mcp_empty_result" in case.recovery_path:
            items.add("Trusting empty MCP results without alternate evidence.")
        if "shell_fallback_after_mcp" in case.recovery_path:
            items.add("Treating MCP-first as mandatory when source/shell evidence is faster.")
    if any(_verdict(finding.verdict) == FindingVerdict.NOISY for finding in findings):
        items.add("Adding tooling steps after the main uncertainty was already answerable.")
    return sorted(items)


def _fallback_rules(findings: list[ReviewFinding]) -> list[str]:
    rules: set[str] = set()
    if any(_finding_type(finding.type) == FindingType.SHELL_FALLBACK for finding in findings):
        rules.add("Prefer direct source/shell inspection when MCP returns empty or cannot expose needed fields.")
    if any(_finding_type(finding.type) == FindingType.MCP_EFFICACY for finding in findings):
        rules.add("Do not optimize for MCP usage rate; require evidence that MCP reduced uncertainty.")
    return sorted(rules)


def _consistency_score(path_counter: Counter[str], support_count: int) -> int:
    if support_count <= 0 or not path_counter:
        return 0
    return int(path_counter.most_common(1)[0][1] / support_count * 100)


def _target_type(value: object) -> TargetType:
    return value if isinstance(value, TargetType) else TargetType(str(value))


def _finding_type(value: object) -> FindingType:
    return value if isinstance(value, FindingType) else FindingType(str(value))


def _verdict(value: object) -> FindingVerdict:
    return value if isinstance(value, FindingVerdict) else FindingVerdict(str(value))


def _status(value: object) -> CandidateStatus:
    return value if isinstance(value, CandidateStatus) else CandidateStatus(str(value))


def _sensitivity(value: object) -> SensitivityLevel:
    return value if isinstance(value, SensitivityLevel) else SensitivityLevel(str(value))
