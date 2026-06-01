from __future__ import annotations

import json
from pathlib import Path

from session_review.adapters.codex_jsonl import parse_session_file
from session_review.config import ReviewerPack
from session_review.models import RouteMode
from session_review.safety import SafetyPolicy


def test_codex_jsonl_parser_splits_user_turns_and_records_recovery_path(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "session-1", "cwd": "workspace/project"}},
        {"payload": {"type": "message", "role": "user", "content": "帮我确认 role_id=6 走哪个 provider"}},
        {
            "payload": {
                "type": "function_call",
                "name": "inspect_request",
                "call_id": "mcp-1",
                "arguments": json.dumps({"request_id": "req-1", "text_snippet": "hello"}),
            }
        },
        {
            "payload": {
                "type": "function_call_output",
                "call_id": "mcp-1",
                "output": json.dumps({"total_hits": 0, "logs": []}),
            }
        },
        {
            "payload": {
                "type": "function_call",
                "name": "functions.exec_command",
                "call_id": "shell-1",
                "arguments": json.dumps({"cmd": "rg provider workspace/project"}),
            }
        },
        {"payload": {"type": "function_call_output", "call_id": "shell-1", "output": "provider=cartesia"}},
        {
            "payload": {
                "type": "function_call",
                "name": "get_trace",
                "call_id": "mcp-2",
                "arguments": json.dumps({"request_id": "req-1"}),
            }
        },
        {
            "payload": {
                "type": "function_call_output",
                "call_id": "mcp-2",
                "output": json.dumps({"total": 1, "provider": "cartesia"}),
            }
        },
        {"payload": {"type": "message", "role": "assistant", "content": "已确认 provider 是 cartesia。"}},
        {"payload": {"type": "message", "role": "user", "content": "另一个问题"}},
        {"payload": {"type": "message", "role": "assistant", "content": "需要更多信息。"}},
    ]
    session.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )
    pack = ReviewerPack.model_validate(
        {
            "tool_families": {
                "mcp": ["inspect_request", "get_trace"],
                "shell": ["exec_command"],
            },
            "task_families": {
                "tts_provider_verification": {
                    "trigger_keywords": ["provider", "role_id"],
                    "preferred_route": "mcp_first",
                    "expected_data": ["provider", "evidence_ref"],
                }
            },
        }
    )

    result = parse_session_file(session, safety=SafetyPolicy(), reviewer_pack=pack)

    assert len(result.cases) == 2
    first = result.cases[0]
    assert first.task_family == "tts_provider_verification"
    assert first.expected_data == ["provider", "evidence_ref"]
    assert first.expected_route == RouteMode.MCP_FIRST
    assert first.actual_route == RouteMode.MIXED
    assert first.tool_path == ["inspect_request", "exec_command", "get_trace"]
    assert "mcp_empty_result" in first.recovery_path
    assert "shell_fallback_after_mcp" in first.recovery_path
    assert "mcp_after_shell" in first.recovery_path
    assert all("file_hash" in ref.locator for ref in first.raw_refs)
