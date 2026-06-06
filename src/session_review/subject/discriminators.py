from __future__ import annotations

from collections import Counter

from session_review.safety import stable_hash

from .models import (
    AttributionHint,
    AttributionStatus,
    CollisionStatus,
    EvidenceBasis,
    ReviewCollision,
    ReviewerClaim,
    SubjectEpisode,
    SubjectReviewPack,
)


def discriminate_subject_episodes(
    episodes: list[SubjectEpisode],
    *,
    pack: SubjectReviewPack,
) -> tuple[list[SubjectEpisode], list[AttributionHint], list[ReviewCollision], str]:
    hints = [_attribution_hint(episode, pack) for episode in episodes]
    hint_by_episode = {hint.episode_id: hint for hint in hints}
    discriminated = [
        episode.model_copy(
            update={
                "attribution": hint_by_episode[episode.episode_id],
                "review_only": hint_by_episode[episode.episode_id].review_only,
            },
            deep=True,
        )
        for episode in episodes
    ]
    collisions = _build_collisions(discriminated, hints, pack)
    return discriminated, hints, collisions, render_discrimination_report(discriminated, hints, collisions, pack)


def render_discrimination_report(
    episodes: list[SubjectEpisode],
    hints: list[AttributionHint],
    collisions: list[ReviewCollision],
    pack: SubjectReviewPack,
) -> str:
    status_counts = Counter(str(hint.status) for hint in hints)
    basis_counts = Counter(str(hint.evidence_basis) for hint in hints)
    cross_project = len({episode.project_hint.get("cwd_hash") for episode in episodes if episode.project_hint}) > 1
    lines = [
        "# Subject Discrimination Report",
        "",
        f"- subject: `{pack.subject.subject_id}`",
        f"- signal_pack: `{pack.signal_pack.pack.id if pack.signal_pack else '<none>'}`",
        f"- episodes: {len(episodes)}",
        f"- cross_project: {str(cross_project).lower()}",
        f"- attribution_status: {dict(status_counts)}",
        f"- evidence_basis: {dict(basis_counts)}",
        f"- collisions: {len(collisions)}",
        "",
        "## Attribution Hints",
    ]
    if not hints:
        lines.append("- No attribution hints.")
    for hint in hints:
        lines.append(
            f"- `{hint.episode_id}` status={hint.status} basis={hint.evidence_basis} "
            f"review_only={str(hint.review_only).lower()} reasons={','.join(hint.reason_codes)}"
        )
    lines.extend(["", "## Collision Notes"])
    if not collisions:
        lines.append("- No collisions.")
    for collision in collisions:
        lines.append(
            f"- `{collision.collision_id}` status={collision.status} "
            f"episodes={len(collision.episode_ids)} resolution={collision.resolution} "
            f"reasons={','.join(collision.reason_codes)}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "- Attribution hints are not root-cause proof.",
            "- Ambiguous, rejected, or competing episodes stay review-only.",
            "- Do not auto-promote candidates from collided or uncertain evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def _attribution_hint(episode: SubjectEpisode, pack: SubjectReviewPack) -> AttributionHint:
    signals = _episode_signals(episode)
    domain_anchor_ids = sorted(signal for signal in signals if signal.startswith("signal_pack:domain_anchor:"))
    negative_ids = sorted(signal for signal in signals if signal.startswith("signal_pack:negative:"))
    ambiguous_ids = sorted(signal for signal in signals if signal.startswith("signal_pack:ambiguous:"))
    basis = _evidence_basis(signals)
    reason_codes: list[str] = []
    status = AttributionStatus.UNKNOWN
    confidence = "low"

    if pack.signal_pack is None:
        reason_codes.append("no_signal_pack")
        if "direct_usage" in signals:
            status = AttributionStatus.LIKELY
            confidence = "low"
        elif "missed_opportunity" in signals:
            status = AttributionStatus.AMBIGUOUS
        return _hint(
            episode,
            status=status,
            basis=basis,
            confidence=confidence,
            reason_codes=reason_codes,
            domain_anchor_ids=domain_anchor_ids,
            negative_signal_ids=negative_ids,
            ambiguous_signal_ids=ambiguous_ids,
        )

    required_all = [
        f"signal_pack:domain_anchor:{stable_hash(item, length=8)}"
        for item in pack.signal_pack.domain_anchors.required_all
    ]
    missing_required = [item for item in required_all if item not in domain_anchor_ids]
    if negative_ids:
        reason_codes.append("negative_signal_matched")
    if ambiguous_ids:
        reason_codes.append("ambiguous_term_matched")
    if missing_required:
        reason_codes.append("missing_required_domain_anchor")
    if pack.signal_pack.domain_anchors.required_any and not domain_anchor_ids:
        reason_codes.append("missing_any_domain_anchor")

    if negative_ids and domain_anchor_ids:
        status = AttributionStatus.AMBIGUOUS
        reason_codes.append("competing_domain_signals")
    elif negative_ids and not domain_anchor_ids:
        status = AttributionStatus.REJECTED
        reason_codes.append("rejected_by_negative_without_anchor")
    elif missing_required:
        status = AttributionStatus.AMBIGUOUS
    elif ambiguous_ids and pack.signal_pack.ambiguous_terms.require_domain_anchor and not domain_anchor_ids:
        status = AttributionStatus.AMBIGUOUS
        reason_codes.append("ambiguous_requires_domain_anchor")
    elif domain_anchor_ids and "direct_usage" in signals:
        status = AttributionStatus.CONFIRMED
        confidence = "medium"
        reason_codes.append("direct_usage_with_domain_anchor")
    elif domain_anchor_ids:
        status = AttributionStatus.LIKELY
        confidence = "medium"
        reason_codes.append("domain_anchor_matched")
    elif "direct_usage" in signals and not negative_ids:
        status = AttributionStatus.LIKELY
        reason_codes.append("direct_usage_without_domain_anchor")
    else:
        status = AttributionStatus.AMBIGUOUS
        reason_codes.append("insufficient_domain_evidence")

    return _hint(
        episode,
        status=status,
        basis=basis,
        confidence=confidence,
        reason_codes=sorted(set(reason_codes)),
        domain_anchor_ids=domain_anchor_ids,
        negative_signal_ids=negative_ids,
        ambiguous_signal_ids=ambiguous_ids,
    )


def _hint(
    episode: SubjectEpisode,
    *,
    status: AttributionStatus,
    basis: EvidenceBasis,
    confidence: str,
    reason_codes: list[str],
    domain_anchor_ids: list[str],
    negative_signal_ids: list[str],
    ambiguous_signal_ids: list[str],
) -> AttributionHint:
    return AttributionHint(
        episode_id=episode.episode_id,
        subject_id=episode.subject_id,
        status=status,
        evidence_basis=basis,
        confidence=confidence,
        reason_codes=reason_codes,
        signal_ids=sorted(_episode_signals(episode)),
        domain_anchor_ids=domain_anchor_ids,
        negative_signal_ids=negative_signal_ids,
        ambiguous_signal_ids=ambiguous_signal_ids,
        review_only=status
        in {AttributionStatus.AMBIGUOUS, AttributionStatus.REJECTED, AttributionStatus.UNKNOWN},
    )


def _build_collisions(
    episodes: list[SubjectEpisode],
    hints: list[AttributionHint],
    pack: SubjectReviewPack,
) -> list[ReviewCollision]:
    collisions: list[ReviewCollision] = []
    for episode, hint in zip(episodes, hints, strict=True):
        claims = [
            ReviewerClaim(
                reviewer_id="collector",
                claim_type="subject_relevance",
                episode_ids=[episode.episode_id],
                attribution_status=AttributionStatus.LIKELY
                if episode.matched_signals
                else AttributionStatus.UNKNOWN,
                evidence_basis=_evidence_basis(set(episode.matched_signals)),
                confidence="low",
                rationale="Collector recalled the episode from configured deterministic signals.",
                uncertainty="Collector recall is not domain attribution.",
                competing_explanations=[],
            ),
            ReviewerClaim(
                reviewer_id="domain_discriminator",
                claim_type="domain_attribution",
                episode_ids=[episode.episode_id],
                attribution_status=hint.status,
                evidence_basis=hint.evidence_basis,
                confidence=hint.confidence,
                rationale="Domain discriminator evaluated anchors, negative signals, and ambiguous terms.",
                uncertainty="No raw text is persisted; ambiguous cases require manual review.",
                competing_explanations=_competing_explanations(hint),
            ),
        ]
        if hint.status in {AttributionStatus.AMBIGUOUS, AttributionStatus.REJECTED, AttributionStatus.UNKNOWN}:
            collisions.append(
                ReviewCollision(
                    collision_id=stable_hash((pack.subject.subject_id, episode.episode_id, hint.status)),
                    subject_id=pack.subject.subject_id,
                    episode_ids=[episode.episode_id],
                    status=(
                        CollisionStatus.COMPETING_DOMAIN
                        if hint.status == AttributionStatus.REJECTED
                        else CollisionStatus.INSUFFICIENT_CONTEXT
                    ),
                    claims=claims,
                    resolution="review_only",
                    reason_codes=hint.reason_codes,
                )
            )
    return collisions


def _episode_signals(episode: SubjectEpisode) -> set[str]:
    signals = set(episode.matched_signals)
    for event in episode.before_context + episode.subject_events + episode.after_context:
        signals.update(event.signal_ids)
    return signals


def _evidence_basis(signals: set[str]) -> EvidenceBasis:
    has_direct = "direct_usage" in signals
    has_contextual = any(signal.startswith("contextual_need:") for signal in signals)
    has_fallback = any(signal.startswith("fallback:") for signal in signals)
    count = sum([has_direct, has_contextual, has_fallback])
    if count > 1:
        return EvidenceBasis.MIXED
    if has_direct:
        return EvidenceBasis.DIRECT_USAGE
    if has_contextual:
        return EvidenceBasis.CONTEXTUAL_NEED
    if has_fallback:
        return EvidenceBasis.FALLBACK
    return EvidenceBasis.UNKNOWN


def _competing_explanations(hint: AttributionHint) -> list[str]:
    explanations: list[str] = []
    if hint.negative_signal_ids:
        explanations.append("Configured negative signals indicate a different domain may explain this episode.")
    if hint.ambiguous_signal_ids and not hint.domain_anchor_ids:
        explanations.append("Only ambiguous terms matched without a configured domain anchor.")
    if "missing_any_domain_anchor" in hint.reason_codes:
        explanations.append("The episode lacks the configured domain anchor required for attribution.")
    return explanations
