from __future__ import annotations

import json
from pathlib import Path

from session_review.cli import run
from session_review.transcripts.aggregate import FORBIDDEN_AGGREGATE_KEYS


def test_transcript_prepare_generates_facts_aggregate_and_manifest(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    _write_session(session)
    output_run = tmp_path / "run"

    assert (
        run(
            [
                "transcript",
                "prepare",
                "--session",
                str(session),
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_run),
                "--timezone",
                "Asia/Shanghai",
            ]
        )
        == 0
    )

    manifest = json.loads((output_run / "manifest.json").read_text(encoding="utf-8"))
    task_input = json.loads((output_run / "agent" / "task-input.json").read_text(encoding="utf-8"))
    aggregate = json.loads((output_run / "aggregate" / "usage-summary.json").read_text(encoding="utf-8"))
    mcp_subject = json.loads((output_run / "subjects" / "mcp" / "usage-summary.json").read_text(encoding="utf-8"))
    turn_map = json.loads((output_run / "aggregate" / "turn-map.json").read_text(encoding="utf-8"))
    cards = sorted((output_run / "facts" / "turns" / "cards").glob("*.md"))
    full = sorted((output_run / "facts" / "turns" / "full").glob("*.md"))
    facts = sorted((output_run / "facts" / "turns" / "json").glob("*.json"))
    session_indexes = sorted((output_run / "facts" / "sessions").glob("*.json"))

    assert manifest["schema_version"] == "session_review.run_manifest.v1"
    assert manifest["safety"]["aggregate_contains_raw_text"] is False
    assert task_input["raw_session_access"] == "disabled_by_default"
    assert aggregate["counts"]["turns_total"] == 2
    assert aggregate["counts"]["turns_with_mcp"] == 2
    assert mcp_subject["subject_id"] == "mcp"
    assert cards and full and facts and session_indexes
    assert turn_map["turn_count"] == 2

    rendered_facts = "\n".join(path.read_text(encoding="utf-8") for path in facts)
    rendered_cards = "\n".join(path.read_text(encoding="utf-8") for path in cards)
    assert "Confirm provider routing for alpha" in rendered_facts
    assert "confirmed provider routing with evidence" in rendered_facts
    assert "Confirm provider routing for alpha" in rendered_cards
    rendered_aggregate = json.dumps(aggregate, ensure_ascii=False).lower()
    assert "Confirm provider routing for alpha".lower() not in rendered_aggregate
    assert not (FORBIDDEN_AGGREGATE_KEYS & _json_keys(aggregate))

    for ref in turn_map["turns"]:
        paths = ref["paths"]
        hashes = ref["hashes"]
        assert (output_run / paths["card_md"]).exists()
        assert (output_run / paths["full_md"]).exists()
        assert (output_run / paths["json"]).exists()
        assert set(hashes) == {"fact_json", "card_md", "full_md"}


def test_transcript_prepare_is_idempotent_for_same_input(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    _write_session(session)
    first = tmp_path / "first"
    second = tmp_path / "second"
    args = [
        "transcript",
        "prepare",
        "--session",
        str(session),
        "--reviewer-pack",
        "examples/session-review/reviewer-pack.toml",
    ]

    assert run([*args, "--output-run", str(first)]) == 0
    assert run([*args, "--output-run", str(second)]) == 0

    first_map = json.loads((first / "aggregate" / "turn-map.json").read_text(encoding="utf-8"))
    second_map = json.loads((second / "aggregate" / "turn-map.json").read_text(encoding="utf-8"))
    assert [item["turn_id"] for item in first_map["turns"]] == [
        item["turn_id"] for item in second_map["turns"]
    ]
    assert [item["hashes"]["fact_json"] for item in first_map["turns"]] == [
        item["hashes"]["fact_json"] for item in second_map["turns"]
    ]


def test_transcript_date_bj_alias_and_collect_then_aggregate(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    _write_session(session)
    output_run = tmp_path / "run"

    assert (
        run(
            [
                "transcript",
                "collect",
                "--session",
                str(session),
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_run),
                "--date-bj",
                "2026-07-01",
            ]
        )
        == 0
    )
    assert (
        run(
            [
                "transcript",
                "aggregate",
                "--output-run",
                str(output_run),
                "--date-bj",
                "2026-07-01",
            ]
        )
        == 0
    )
    aggregate = json.loads((output_run / "aggregate" / "usage-summary.json").read_text(encoding="utf-8"))
    assert aggregate["window"]["timezone"] == "Asia/Shanghai"
    assert aggregate["window"]["mode"] == "natural_day"


def test_transcript_window_discovery_modes(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    session_dir = codex_home / "sessions" / "2026" / "07" / "01"
    session_dir.mkdir(parents=True)
    session = session_dir / "session.jsonl"
    _write_session(session)

    output_glob = tmp_path / "glob-run"
    assert (
        run(
            [
                "transcript",
                "prepare",
                "--session-glob",
                str(session_dir / "*.jsonl"),
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_glob),
            ]
        )
        == 0
    )
    assert json.loads((output_glob / "aggregate" / "usage-summary.json").read_text(encoding="utf-8"))[
        "counts"
    ]["turns_total"] == 2

    output_hours = tmp_path / "hours-run"
    assert (
        run(
            [
                "transcript",
                "prepare",
                "--codex-home",
                str(codex_home),
                "--since-hours",
                "1",
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_hours),
            ]
        )
        == 0
    )
    hours_manifest = json.loads((output_hours / "manifest.json").read_text(encoding="utf-8"))
    assert hours_manifest["window"]["mode"] == "since_hours"
    assert json.loads((output_hours / "aggregate" / "usage-summary.json").read_text(encoding="utf-8"))[
        "counts"
    ]["turns_total"] == 2

    output_days = tmp_path / "days-run"
    assert (
        run(
            [
                "transcript",
                "prepare",
                "--codex-home",
                str(codex_home),
                "--since-days",
                "1",
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_days),
            ]
        )
        == 0
    )
    days_manifest = json.loads((output_days / "manifest.json").read_text(encoding="utf-8"))
    assert days_manifest["window"]["mode"] == "since_days"
    assert json.loads((output_days / "aggregate" / "usage-summary.json").read_text(encoding="utf-8"))[
        "counts"
    ]["turns_total"] == 2


def test_transcript_date_timezone_window_metadata(tmp_path: Path) -> None:
    session = tmp_path / "session.jsonl"
    _write_session(session)
    output_run = tmp_path / "date-run"

    assert (
        run(
            [
                "transcript",
                "prepare",
                "--session",
                str(session),
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_run),
                "--date",
                "2026-07-01",
                "--timezone",
                "Asia/Shanghai",
            ]
        )
        == 0
    )
    manifest = json.loads((output_run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["window"]["mode"] == "natural_day"
    assert manifest["window"]["timezone"] == "Asia/Shanghai"
    assert manifest["window"]["start"] == "2026-07-01T00:00:00+08:00"
    assert manifest["window"]["end"] == "2026-07-02T00:00:00+08:00"


def test_transcript_collects_event_msg_mcp_end_and_task_complete(tmp_path: Path) -> None:
    session = tmp_path / "event-msg-session.jsonl"
    _write_event_msg_session(session)
    output_run = tmp_path / "event-msg-run"

    assert (
        run(
            [
                "transcript",
                "prepare",
                "--session",
                str(session),
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_run),
                "--timezone",
                "Asia/Shanghai",
            ]
        )
        == 0
    )

    facts = sorted((output_run / "facts" / "turns" / "json").glob("*.json"))
    fact_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in facts]
    fact = next(item for item in fact_payloads if item["mcp_call_sequence"])
    call = fact["mcp_call_sequence"][0]

    assert fact["user"]["text"] == "查 message_id=msg-1 为什么 provider 不对"
    assert fact["next_user"]["text"] == "不是这个，再看昨天的"
    assert fact["final_answer"]["text"] == "最终：这次 provider 是 cartesia，日志里 vendor_output 存在。"
    assert call["result_source"] == "mcp_tool_call_end"
    assert call["result_summary"]["providers"] == ["cartesia"]
    assert call["result_summary"]["total_returned"] == 1
    assert call["evidence_role"] == "used_in_final"
    assert call["evidence_role_confidence"] == "medium"
    assert call["evidence_role_method"] == "exact_value_match"
    assert {
        "match_type": "exact_value_match",
        "signal": "cartesia",
        "field": "provider",
        "value": "cartesia",
    } in call["evidence_matches"]
    assert any(item.get("signal") == "cartesia" for item in fact["adopted_evidence"])


def test_transcript_fixture_integration_command(tmp_path: Path) -> None:
    output_run = tmp_path / "fixture-run"

    assert (
        run(
            [
                "transcript",
                "prepare",
                "--session",
                "examples/session-review/sessions/redacted-session.jsonl",
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--output-run",
                str(output_run),
                "--timezone",
                "Asia/Shanghai",
            ]
        )
        == 0
    )

    expected = [
        "manifest.json",
        "aggregate/usage-summary.json",
        "aggregate/bucket-metrics.json",
        "aggregate/turn-map.json",
        "subjects/mcp/usage-summary.json",
        "subjects/mcp/bucket-metrics.json",
        "agent/task-input.json",
    ]
    for item in expected:
        assert (output_run / item).exists()
    assert list((output_run / "facts" / "turns" / "cards").glob("*.md"))
    assert list((output_run / "facts" / "turns" / "full").glob("*.md"))
    assert list((output_run / "facts" / "turns" / "json").glob("*.json"))
    assert list((output_run / "facts" / "sessions").glob("*.json"))


def _write_session(path: Path) -> None:
    records = [
        {"timestamp": "2026-07-01T00:00:00Z", "type": "session_meta", "payload": {"id": "session-1"}},
        {
            "timestamp": "2026-07-01T00:00:01Z",
            "payload": {"type": "message", "role": "user", "content": "Confirm provider routing for alpha"},
        },
        {
            "timestamp": "2026-07-01T00:00:02Z",
            "payload": {
                "type": "function_call",
                "name": "inspect_request",
                "call_id": "mcp-1",
                "arguments": json.dumps({"request_id": "alpha"}),
            },
        },
        {
            "timestamp": "2026-07-01T00:00:03Z",
            "payload": {
                "type": "function_call_output",
                "call_id": "mcp-1",
                "output": json.dumps({"total": 1, "provider": "example-provider"}),
            },
        },
        {
            "timestamp": "2026-07-01T00:00:04Z",
            "payload": {"type": "message", "role": "assistant", "content": "confirmed provider routing with evidence"},
        },
        {
            "timestamp": "2026-07-01T00:00:05Z",
            "payload": {"type": "message", "role": "user", "content": "不是这个，重新看 beta"},
        },
        {
            "timestamp": "2026-07-01T00:00:06Z",
            "payload": {
                "type": "function_call",
                "name": "inspect_request",
                "call_id": "mcp-2",
                "arguments": json.dumps({"request_id": "beta"}),
            },
        },
        {
            "timestamp": "2026-07-01T00:00:07Z",
            "payload": {"type": "function_call_output", "call_id": "mcp-2", "output": json.dumps({"error": "timeout"})},
        },
        {
            "timestamp": "2026-07-01T00:00:08Z",
            "payload": {"type": "message", "role": "assistant", "content": "无法确认 beta。"},
        },
    ]
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")


def _write_event_msg_session(path: Path) -> None:
    records = [
        {"timestamp": "2026-06-01T00:00:00Z", "type": "session_meta", "payload": {"id": "session-turn-1"}},
        {
            "timestamp": "2026-06-01T00:00:01Z",
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-a", "started_at": "2026-06-01T00:00:01Z"},
        },
        {
            "timestamp": "2026-06-01T00:00:02Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "查 message_id=msg-1 为什么 provider 不对"},
        },
        {
            "timestamp": "2026-06-01T00:00:03Z",
            "type": "event_msg",
            "payload": {"type": "agent_message", "message": "我先按 message_id 查 TTS 日志。"},
        },
        {
            "timestamp": "2026-06-01T00:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "inspect_request",
                "arguments": json.dumps({"message_id": "msg-1", "env": "prod"}, ensure_ascii=False),
                "call_id": "call-1",
            },
        },
        {
            "timestamp": "2026-06-01T00:00:05Z",
            "type": "event_msg",
            "payload": {
                "type": "mcp_tool_call_end",
                "call_id": "call-1",
                "result": {
                    "Ok": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "stage_status": {"vendor_output": {"status": "found"}},
                                        "provider_summary": {"provider": "cartesia"},
                                        "total_returned": 1,
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ]
                    }
                },
            },
        },
        {
            "timestamp": "2026-06-01T00:00:06Z",
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "call-1", "output": "string fallback should not win"},
        },
        {
            "timestamp": "2026-06-01T00:00:07Z",
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "last_agent_message": "最终：这次 provider 是 cartesia，日志里 vendor_output 存在。",
            },
        },
        {
            "timestamp": "2026-06-01T00:00:08Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "不是这个，再看昨天的"},
        },
    ]
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")


def _json_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key).lower())
            keys.update(_json_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_json_keys(item))
    return keys
