from __future__ import annotations

from collections import Counter, defaultdict

from session_review.models import CandidateStatus
from session_review.safety import stable_hash

from .models import (
    AttributionStatus,
    EvidenceBasis,
    ReviewCollision,
    SubjectCandidateTarget,
    SubjectEpisode,
    SubjectFinding,
    SubjectFindingType,
    SubjectImprovementCandidate,
    SubjectReviewPack,
)


def review_subject_episodes(
    episodes: list[SubjectEpisode],
    *,
    pack: SubjectReviewPack,
    collisions: list[ReviewCollision] | None = None,
) -> tuple[list[SubjectFinding], list[SubjectImprovementCandidate], str]:
    findings = _build_findings(episodes, pack, collisions=collisions or [])
    candidates = _build_candidates(findings, episodes, pack)
    report = render_subject_report(episodes, findings, candidates, pack, collisions=collisions or [])
    return findings, candidates, report


def render_subject_report(
    episodes: list[SubjectEpisode],
    findings: list[SubjectFinding],
    candidates: list[SubjectImprovementCandidate],
    pack: SubjectReviewPack,
    collisions: list[ReviewCollision] | None = None,
) -> str:
    collisions = collisions or []
    finding_counts = Counter(str(finding.type) for finding in findings)
    outcome_counts = Counter(episode.outcome_hint for episode in episodes)
    attribution_counts = Counter(
        str(episode.attribution.status)
        for episode in episodes
        if episode.attribution is not None
    )
    lines = [
        "# Subject Review Report",
        "",
        f"- subject: `{pack.subject.subject_id}`",
        f"- display_name: `{pack.subject.display_name}`",
        f"- episodes: {len(episodes)}",
        f"- findings: {len(findings)}",
        f"- candidates: {len(candidates)}",
        f"- collisions: {len(collisions)}",
        "",
        "## Usage",
        f"- Direct usage episodes: {_count_signal(episodes, 'direct_usage')}",
        f"- Missed opportunity episodes: {_count_signal(episodes, 'missed_opportunity')}",
        f"- Fallback episodes: {_count_prefix(episodes, 'fallback:')}",
        "",
        "## Effectiveness",
        f"- Outcomes: {dict(outcome_counts)}",
        f"- Attribution status: {dict(attribution_counts)}",
        "",
        "## Findings",
    ]
    if not findings:
        lines.append("- No findings.")
    for finding in findings:
        lines.append(
            f"- `{finding.type}` ({finding.confidence}, attribution={finding.attribution_status}, "
            f"basis={finding.evidence_basis}, review_only={str(finding.review_only).lower()}): "
            f"{finding.rationale}"
        )
        if finding.uncertainty:
            lines.append(f"  - uncertainty: {finding.uncertainty}")
    lines.extend(["", "## Collisions"])
    if not collisions:
        lines.append("- No collisions.")
    for collision in collisions:
        lines.append(
            f"- `{collision.collision_id}` status={collision.status} "
            f"resolution={collision.resolution} reasons={','.join(collision.reason_codes)}"
        )
    lines.extend(["", "## Candidates"])
    if not candidates:
        lines.append("- No candidates.")
    for candidate in candidates:
        lines.append(
            f"- `{candidate.candidate_id}` {candidate.target_type}: {candidate.problem_pattern}"
            f" attribution={candidate.attribution_status} review_only={str(candidate.review_only).lower()}"
        )
    lines.extend(
        [
            "",
            "## Promotion Boundary",
            "- Findings are review evidence, not automatic changes.",
            "- Do not auto-modify tools, skills, MCP schemas, subagents, or runbooks.",
            f"- Finding distribution: {dict(finding_counts)}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_subject_candidates_table(candidates: list[SubjectImprovementCandidate]) -> str:
    headers = ["candidate_id", "target_type", "maturity", "export_allowed", "score", "episodes", "problem_pattern"]
    rows = [
        [
            candidate.candidate_id,
            str(candidate.target_type),
            str(candidate.maturity),
            str(candidate.export_allowed).lower(),
            ",".join(f"{key}={value}" for key, value in sorted(candidate.score.items())),
            str(len(candidate.evidence_episode_ids)),
            candidate.problem_pattern,
        ]
        for candidate in candidates
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(headers[index])
        for index in range(len(headers))
    ]
    lines = ["  ".join(headers[index].ljust(widths[index]) for index in range(len(headers)))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(row[index].ljust(widths[index]) for index in range(len(headers))) for row in rows)
    return "\n".join(lines)


def _build_findings(
    episodes: list[SubjectEpisode],
    pack: SubjectReviewPack,
    *,
    collisions: list[ReviewCollision],
) -> list[SubjectFinding]:
    findings: list[SubjectFinding] = []
    actionable = [
        episode
        for episode in episodes
        if not episode.review_only
        and (
            episode.attribution is None
            or episode.attribution.status in {AttributionStatus.CONFIRMED, AttributionStatus.LIKELY}
        )
    ]
    uncertain = [episode for episode in episodes if episode not in actionable]
    direct = [episode for episode in actionable if "direct_usage" in episode.matched_signals]
    missed = [
        episode
        for episode in actionable
        if "missed_opportunity" in episode.matched_signals
        or any(signal.startswith("contextual_need:") for signal in episode.matched_signals)
        and "direct_usage" not in episode.matched_signals
    ]
    ineffective = [
        episode
        for episode in direct
        if episode.outcome_hint == "failed_or_retried"
        or any(signal.startswith("fallback:") for signal in episode.matched_signals)
    ]
    effective = [episode for episode in direct if episode.outcome_hint == "improved" and episode not in ineffective]

    if effective:
        findings.append(
            _finding(
                pack,
                SubjectFindingType.EFFECTIVE_USAGE,
                effective,
                "Subject usage was followed by an improved outcome signal.",
                None,
                "medium",
            )
        )
    if ineffective:
        findings.append(
            _finding(
                pack,
                SubjectFindingType.INEFFECTIVE_USAGE,
                ineffective,
                "Subject usage was followed by failure, retry, or fallback signals.",
                SubjectCandidateTarget.TOOL_INTERFACE,
                "medium",
            )
        )
        findings.append(
            _finding(
                pack,
                SubjectFindingType.INTERFACE_FRICTION,
                ineffective,
                "The subject interface or output may be hard for the agent to consume.",
                SubjectCandidateTarget.TOOL_INTERFACE,
                "low",
            )
        )
    if missed:
        findings.append(
            _finding(
                pack,
                SubjectFindingType.MISSED_OPPORTUNITY,
                missed,
                "The subject appeared needed but was not directly used in the episode.",
                SubjectCandidateTarget.SKILL,
                "medium",
            )
        )
    if len(missed) >= 2:
        findings.append(
            _finding(
                pack,
                SubjectFindingType.REPEATED_FAILURE_MODE,
                missed,
                "A missed-use pattern repeated across multiple episodes.",
                SubjectCandidateTarget.DOCS,
                "medium",
            )
        )
    if direct and any("skill" in signal for episode in direct for signal in episode.matched_signals):
        findings.append(
            _finding(
                pack,
                SubjectFindingType.SKILL_HELPFUL,
                direct,
                "Skill-related subject usage appeared in direct episodes.",
                None,
                "low",
            )
        )
    if not findings and episodes:
        findings.append(
            _finding(
                pack,
                SubjectFindingType.ACCIDENTAL_NOISE,
                episodes,
                "Episodes were collected, but deterministic review found no stable improvement signal.",
                None,
                "low",
            )
        )
    if uncertain:
        reason = "Episodes were kept review-only because attribution was ambiguous, rejected, or unknown."
        finding_type = (
            SubjectFindingType.FALSE_POSITIVE_SUBJECT_NEED
            if any(
                episode.attribution
                and episode.attribution.status == AttributionStatus.REJECTED
                for episode in uncertain
            )
            else SubjectFindingType.INSUFFICIENT_CONTEXT
        )
        findings.append(
            _finding(
                pack,
                finding_type,
                uncertain,
                reason,
                None,
                "low",
                review_only=True,
                uncertainty="Do not promote these observations without domain-specific manual review.",
                collision_ids=[item.collision_id for item in collisions],
            )
        )
    return findings


def _build_candidates(
    findings: list[SubjectFinding],
    episodes: list[SubjectEpisode],
    pack: SubjectReviewPack,
) -> list[SubjectImprovementCandidate]:
    grouped: dict[SubjectCandidateTarget, list[SubjectFinding]] = defaultdict(list)
    for finding in findings:
        if finding.target_type is not None and not finding.review_only:
            grouped[SubjectCandidateTarget(str(finding.target_type))].append(finding)
    candidates: list[SubjectImprovementCandidate] = []
    for target, target_findings in grouped.items():
        episode_ids = sorted({episode_id for finding in target_findings for episode_id in finding.episode_ids})
        problem, change, anti_patterns = _candidate_text(target, target_findings, pack)
        candidates.append(
            SubjectImprovementCandidate(
                candidate_id=stable_hash((pack.subject.subject_id, target, episode_ids)),
                subject_id=pack.subject.subject_id,
                target_type=target,
                problem_pattern=problem,
                evidence_episode_ids=episode_ids,
                recommended_change=change,
                anti_patterns=anti_patterns,
                maturity=CandidateStatus.OBSERVED,
                export_allowed=False,
                score={
                    "coverage": min(100, len(episode_ids) * 30),
                    "safety": 100,
                    "reuse": 80 if len(episodes) >= 2 else 50,
                },
                attribution_status=_candidate_attribution_status(target_findings),
                review_only=False,
            )
        )
    return candidates


def _candidate_text(
    target: SubjectCandidateTarget,
    findings: list[SubjectFinding],
    pack: SubjectReviewPack,
) -> tuple[str, str, list[str]]:
    subject = pack.subject.display_name
    if target == SubjectCandidateTarget.TOOL_INTERFACE:
        return (
            f"{subject} may create interface friction for agents.",
            "Review CLI/MCP output shape, result summaries, and failure messages for easier agent consumption.",
            ["Returning verbose output without a clear next action.", "Making the agent infer success from noisy logs."],
        )
    if target == SubjectCandidateTarget.SKILL:
        return (
            f"Agents may not recognize when to use {subject}.",
            "Update the skill trigger guidance and examples for repeated missed-use scenarios.",
            ["Relying on agent memory instead of explicit trigger examples."],
        )
    if target == SubjectCandidateTarget.DOCS:
        return (
            f"{subject} usage expectations are not explicit enough.",
            "Document when to use the subject, when not to use it, and expected evidence after use.",
            ["Promoting a single accidental path as a universal rule."],
        )
    if target == SubjectCandidateTarget.MCP_SCHEMA:
        return (
            f"{subject} MCP schema may not expose enough decision-ready evidence.",
            "Review schema fields and add concise outcome/status/evidence fields where needed.",
            ["Adding fields without proving they reduce uncertainty."],
        )
    return (
        f"{subject} workflow pattern may need clarification.",
        "Review repeated episodes and document the stable path only after manual validation.",
        ["Treating historical agent behavior as best practice without counterexamples."],
    )


def _finding(
    pack: SubjectReviewPack,
    finding_type: SubjectFindingType,
    episodes: list[SubjectEpisode],
    rationale: str,
    target: SubjectCandidateTarget | None,
    confidence: str,
    review_only: bool = False,
    uncertainty: str = "",
    collision_ids: list[str] | None = None,
) -> SubjectFinding:
    episode_ids = [episode.episode_id for episode in episodes]
    attribution_status = _episodes_attribution_status(episodes)
    evidence_basis = _episodes_evidence_basis(episodes)
    return SubjectFinding(
        finding_id=stable_hash((pack.subject.subject_id, finding_type, episode_ids)),
        subject_id=pack.subject.subject_id,
        type=finding_type,
        confidence=confidence,
        episode_ids=episode_ids,
        rationale=rationale,
        target_type=target,
        attribution_status=attribution_status,
        evidence_basis=evidence_basis,
        review_only=review_only,
        uncertainty=uncertainty,
        collision_ids=collision_ids or [],
    )


def _count_signal(episodes: list[SubjectEpisode], signal: str) -> int:
    return sum(1 for episode in episodes if signal in episode.matched_signals)


def _count_prefix(episodes: list[SubjectEpisode], prefix: str) -> int:
    return sum(1 for episode in episodes if any(signal.startswith(prefix) for signal in episode.matched_signals))


def _episodes_attribution_status(episodes: list[SubjectEpisode]) -> AttributionStatus:
    statuses = [
        episode.attribution.status
        for episode in episodes
        if episode.attribution is not None
    ]
    if not statuses:
        return AttributionStatus.UNKNOWN
    if any(status == AttributionStatus.REJECTED for status in statuses):
        return AttributionStatus.REJECTED
    if any(status == AttributionStatus.AMBIGUOUS for status in statuses):
        return AttributionStatus.AMBIGUOUS
    if all(status == AttributionStatus.CONFIRMED for status in statuses):
        return AttributionStatus.CONFIRMED
    if any(status == AttributionStatus.LIKELY for status in statuses):
        return AttributionStatus.LIKELY
    return AttributionStatus.UNKNOWN


def _episodes_evidence_basis(episodes: list[SubjectEpisode]) -> EvidenceBasis:
    bases = [
        episode.attribution.evidence_basis
        for episode in episodes
        if episode.attribution is not None
    ]
    if not bases:
        return EvidenceBasis.UNKNOWN
    unique = set(bases)
    if len(unique) > 1:
        return EvidenceBasis.MIXED
    return bases[0]


def _candidate_attribution_status(findings: list[SubjectFinding]) -> AttributionStatus:
    statuses = {finding.attribution_status for finding in findings}
    if AttributionStatus.CONFIRMED in statuses:
        return AttributionStatus.CONFIRMED
    if AttributionStatus.LIKELY in statuses:
        return AttributionStatus.LIKELY
    if AttributionStatus.AMBIGUOUS in statuses:
        return AttributionStatus.AMBIGUOUS
    if AttributionStatus.REJECTED in statuses:
        return AttributionStatus.REJECTED
    return AttributionStatus.UNKNOWN
