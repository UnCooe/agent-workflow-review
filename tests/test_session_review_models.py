from __future__ import annotations

import json

from session_review.models import AgentEvent, AgentEventKind, ReviewCase, SensitivityLevel
from session_review.safety import SafetyPolicy


def test_session_review_models_serialize_without_raw_sensitive_locator() -> None:
    safety = SafetyPolicy(include_raw_text=False, hash_identifiers=True)
    raw_ref = safety.raw_ref(
        source_id=("session-1", 1),
        locator={"file": "sessions/session-1.jsonl", "line": 12},
        sensitivity=SensitivityLevel.S1,
    )
    event = AgentEvent(
        event_id="event-1",
        session_id="session-1",
        kind=AgentEventKind.USER_MESSAGE,
        text_summary="查 provider 是否符合预期",
        raw_ref=raw_ref,
        safety_level=SensitivityLevel.S2,
    )
    case = ReviewCase(
        case_id="case-1",
        session_id="session-1",
        case_group_key="tts_provider_verification:inspect_tts_request",
        user_goal_summary="查 provider 是否符合预期",
        expected_data=["provider", "evidence_ref"],
        events=[event],
        raw_refs=[raw_ref],
    )

    restored = ReviewCase.model_validate(case.model_dump(mode="json"))
    rendered_ref = json.dumps(restored.raw_refs[0].model_dump(mode="json"), ensure_ascii=False)

    assert restored.expected_data == ["provider", "evidence_ref"]
    assert "session-1.jsonl" not in rendered_ref
    assert "file_hash" in restored.raw_refs[0].locator


def test_safety_redacts_identifiers_text_secrets_and_paths() -> None:
    safety = SafetyPolicy(include_raw_text=False, hash_identifiers=True)

    redacted, level = safety.redact_mapping(
        {
            "request_id": "req-123",
            "user_id": "user-456",
            "text_snippet": "raw transcript should not leak",
            "token": "secret-token",
            "path": "workspace/project/private.py",
        }
    )

    rendered = json.dumps(redacted, ensure_ascii=False)
    assert level == SensitivityLevel.S3
    assert "req-123" not in rendered
    assert "user-456" not in rendered
    assert "raw transcript should not leak" not in rendered
    assert "secret-token" not in rendered
    assert "workspace/project/private.py" not in rendered
    assert redacted["token"] == "[redacted:S3]"
