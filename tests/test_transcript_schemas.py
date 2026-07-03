from __future__ import annotations

from session_review.transcripts.schemas import (
    RunManifest,
    TextBlock,
    ToolCallFact,
    TurnFact,
    TurnRef,
    UsageAggregate,
)


def test_transcript_schema_snapshot_minimal() -> None:
    raw_ref = {"source_type": "codex_jsonl", "source_id_hash": "abc", "locator": {"line": 1}}
    fact = TurnFact(
        turn_id="turn_abc",
        session_id_hash="session",
        source_file_hash="source",
        turn_index=0,
        user=TextBlock(text="hello", char_count=5, raw_ref=raw_ref),
        tool_calls=[
            ToolCallFact(
                index=1,
                tool_name="inspect_request",
                tool_family="mcp",
                evidence_role="not_detected_in_final",
                evidence_role_confidence="low",
                evidence_role_method="heuristic",
                raw_ref=raw_ref,
            )
        ],
    )
    payload = fact.model_dump(mode="json")

    assert payload["schema_version"] == "session_review.turn_fact.v1"
    assert payload["access_policy"] == "local_only"
    assert payload["tool_calls"][0]["evidence_role_method"] == "heuristic"


def test_manifest_and_aggregate_schema_snapshot_minimal() -> None:
    manifest = RunManifest(
        run_id="run",
        artifacts={"aggregate": "aggregate/usage-summary.json"},
        safety={"facts_access_policy": "local_only", "aggregate_contains_raw_text": False},
    )
    aggregate = UsageAggregate(
        run_id="run",
        counts={"turns_total": 0},
        distributions={"tool_calls": {}},
        buckets=[],
    )
    ref_payload = {
        "turn_id": "turn_x",
        "paths": {
            "card_md": "facts/turns/cards/turn_x.md",
            "full_md": "facts/turns/full/turn_x.md",
            "json": "facts/turns/json/turn_x.json",
        },
        "hashes": {"fact_json": "a", "card_md": "b", "full_md": "c"},
        "session_id_hash": "session",
        "bucket_flags": [],
    }
    ref = TurnRef.model_validate(ref_payload)

    assert manifest.schema_version == "session_review.run_manifest.v1"
    assert manifest.safety["aggregate_contains_raw_text"] is False
    assert aggregate.schema_version == "session_review.usage_aggregate.v1"
    assert ref.paths.card_md.endswith("turn_x.md")
    assert ref.paths.json_path.endswith("turn_x.json")
