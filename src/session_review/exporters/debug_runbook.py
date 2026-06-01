from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml

from session_review.models import ImprovementCandidate, PromotionDecision, TargetType


def build_debug_runbook_seed(
    *,
    candidate: ImprovementCandidate,
    decision: PromotionDecision,
) -> dict[str, Any]:
    if str(candidate.target_type) != TargetType.DEBUG_RUNBOOK_SEED.value:
        # MCP/skill/subagent candidates can still be reviewed by humans, but v0 only
        # exports debug-runbook seeds.
        raise ValueError("debug_runbook_seed export only supports debug_runbook_seed candidates")
    return {
        "schema_version": "debug_runbook_seed.v0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate": {
            "id": candidate.candidate_id,
            "key": candidate.candidate_key,
            "maturity": str(candidate.maturity),
            "supporting_findings": candidate.supporting_findings,
        },
        "manual_decision": {
            "id": decision.decision_id,
            "status": str(decision.status),
            "reviewer": decision.reviewer,
            "reason": decision.reason,
        },
        "runbook_seed": {
            "title": candidate.problem_pattern.strip("`"),
            "trigger_signals": candidate.trigger_signals,
            "recommended_path": candidate.recommended_path,
            "anti_patterns": candidate.anti_patterns,
            "fallback_rules": candidate.fallback_rules,
            "open_review_items": [
                "Validate with future sessions before promotion.",
                "Confirm business-specific boundaries and counterexamples.",
                "Ensure no raw sensitive evidence is copied into the runbook.",
            ],
        },
    }


def render_debug_runbook_seed(
    *,
    candidate: ImprovementCandidate,
    decision: PromotionDecision,
) -> str:
    return yaml.safe_dump(
        build_debug_runbook_seed(candidate=candidate, decision=decision),
        allow_unicode=True,
        sort_keys=False,
    )
