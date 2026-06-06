from __future__ import annotations

from collections import Counter, defaultdict

from session_review.models import CandidateStatus
from session_review.safety import stable_hash

from .models import (
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
) -> tuple[list[SubjectFinding], list[SubjectImprovementCandidate], str]:
    findings = _build_findings(episodes, pack)
    candidates = _build_candidates(findings, episodes, pack)
    report = render_subject_report(episodes, findings, candidates, pack)
    return findings, candidates, report


def render_subject_report(
    episodes: list[SubjectEpisode],
    findings: list[SubjectFinding],
    candidates: list[SubjectImprovementCandidate],
    pack: SubjectReviewPack,
) -> str:
    finding_counts = Counter(str(finding.type) for finding in findings)
    outcome_counts = Counter(episode.outcome_hint for episode in episodes)
    lines = [
        "# Subject Review Report",
        "",
        f"- subject: `{pack.subject.subject_id}`",
        f"- display_name: `{pack.subject.display_name}`",
        f"- episodes: {len(episodes)}",
        f"- findings: {len(findings)}",
        f"- candidates: {len(candidates)}",
        "",
        "## Usage",
        f"- Direct usage episodes: {_count_signal(episodes, 'direct_usage')}",
        f"- Missed opportunity episodes: {_count_signal(episodes, 'missed_opportunity')}",
        f"- Fallback episodes: {_count_prefix(episodes, 'fallback:')}",
        "",
        "## Effectiveness",
        f"- Outcomes: {dict(outcome_counts)}",
        "",
        "## Findings",
    ]
    if not findings:
        lines.append("- No findings.")
    for finding in findings:
        lines.append(f"- `{finding.type}` ({finding.confidence}): {finding.rationale}")
    lines.extend(["", "## Candidates"])
    if not candidates:
        lines.append("- No candidates.")
    for candidate in candidates:
        lines.append(
            f"- `{candidate.candidate_id}` {candidate.target_type}: {candidate.problem_pattern}"
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


def _build_findings(episodes: list[SubjectEpisode], pack: SubjectReviewPack) -> list[SubjectFinding]:
    findings: list[SubjectFinding] = []
    direct = [episode for episode in episodes if "direct_usage" in episode.matched_signals]
    missed = [
        episode
        for episode in episodes
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
    return findings


def _build_candidates(
    findings: list[SubjectFinding],
    episodes: list[SubjectEpisode],
    pack: SubjectReviewPack,
) -> list[SubjectImprovementCandidate]:
    grouped: dict[SubjectCandidateTarget, list[SubjectFinding]] = defaultdict(list)
    for finding in findings:
        if finding.target_type is not None:
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
) -> SubjectFinding:
    episode_ids = [episode.episode_id for episode in episodes]
    return SubjectFinding(
        finding_id=stable_hash((pack.subject.subject_id, finding_type, episode_ids)),
        subject_id=pack.subject.subject_id,
        type=finding_type,
        confidence=confidence,
        episode_ids=episode_ids,
        rationale=rationale,
        target_type=target,
    )


def _count_signal(episodes: list[SubjectEpisode], signal: str) -> int:
    return sum(1 for episode in episodes if signal in episode.matched_signals)


def _count_prefix(episodes: list[SubjectEpisode], prefix: str) -> int:
    return sum(1 for episode in episodes if any(signal.startswith(prefix) for signal in episode.matched_signals))
