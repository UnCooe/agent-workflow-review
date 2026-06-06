from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml

from .models import SubjectImprovementCandidate


def render_subject_candidate_export(candidate: SubjectImprovementCandidate) -> str:
    payload: dict[str, Any] = {
        "schema_version": "subject_candidate_review_bundle.v0",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "subject_id": candidate.subject_id,
        "candidate": candidate.model_dump(mode="json"),
        "promotion_boundary": {
            "export_is_review_bundle_only": True,
            "auto_modify_tool_skill_mcp_subagent_or_runbook": False,
            "auto_promote": False,
        },
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
