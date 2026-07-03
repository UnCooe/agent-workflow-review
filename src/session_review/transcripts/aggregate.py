from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from session_review.safety import stable_hash
from session_review.transcripts.paths import TranscriptRunPaths, make_turn_ref
from session_review.transcripts.schemas import (
    AgentTaskInput,
    BucketMetric,
    RunManifest,
    SubjectAggregate,
    TurnFact,
    TurnRef,
    UsageAggregate,
)


FORBIDDEN_AGGREGATE_KEYS = {
    "priority",
    "p1",
    "p2",
    "recommendation",
    "recommended_first_reads",
    "suggested",
    "suggested_target",
    "proposed_change",
    "why_it_matters",
    "worth_inspecting",
    "agent_task",
    "verdict",
    "confidence",
    "rationale",
}


def load_turn_facts(run_paths: TranscriptRunPaths) -> list[TurnFact]:
    facts: list[TurnFact] = []
    for path in sorted(run_paths.facts_turn_json.glob("*.json")):
        facts.append(TurnFact.model_validate(json.loads(path.read_text(encoding="utf-8"))))
    return facts


def write_aggregate_artifacts(
    *,
    run_paths: TranscriptRunPaths,
    run_id: str,
    window: dict[str, Any],
) -> None:
    facts = load_turn_facts(run_paths)
    refs = build_turn_refs(run_paths, facts)
    generic = build_usage_aggregate(run_id=run_id, window=window, facts=facts, refs=refs)
    mcp = build_subject_mcp_aggregate(run_id=run_id, window=window, facts=facts, refs=refs)
    turn_map = {
        "schema_version": "session_review.turn_map.v1",
        "run_id": run_id,
        "turn_count": len(refs),
        "turns": [ref.model_dump(mode="json") for ref in refs],
        "by_bucket": {
            bucket.bucket_id: [ref.turn_id for ref in bucket.turn_refs]
            for bucket in generic.buckets
        },
    }
    write_json(run_paths.aggregate / "usage-summary.json", generic.model_dump(mode="json"))
    write_json(run_paths.aggregate / "bucket-metrics.json", bucket_payload(run_id, generic.buckets))
    write_json(run_paths.aggregate / "turn-map.json", turn_map)
    write_json(run_paths.subject_mcp / "usage-summary.json", mcp.model_dump(mode="json"))
    write_json(run_paths.subject_mcp / "bucket-metrics.json", bucket_payload(run_id, mcp.buckets))
    assert_no_forbidden_keys(run_paths.aggregate / "usage-summary.json")
    assert_no_forbidden_keys(run_paths.aggregate / "bucket-metrics.json")
    assert_no_forbidden_keys(run_paths.aggregate / "turn-map.json")
    assert_no_forbidden_keys(run_paths.subject_mcp / "usage-summary.json")
    assert_no_forbidden_keys(run_paths.subject_mcp / "bucket-metrics.json")


def write_manifest_and_task_input(
    *,
    run_paths: TranscriptRunPaths,
    run_id: str,
    window: dict[str, Any],
) -> None:
    manifest = RunManifest(
        run_id=run_id,
        window=window,
        artifacts={
            "aggregate": "aggregate/usage-summary.json",
            "mcp_subject": "subjects/mcp/usage-summary.json",
            "turn_map": "aggregate/turn-map.json",
            "task_input": "agent/task-input.json",
        },
        safety={
            "facts_access_policy": "local_only",
            "aggregate_contains_raw_text": False,
        },
    )
    task_input = AgentTaskInput(
        run_id=run_id,
        entry_artifacts={
            "manifest": "manifest.json",
            "aggregate": "aggregate/usage-summary.json",
            "mcp_subject": "subjects/mcp/usage-summary.json",
            "turn_map": "aggregate/turn-map.json",
        },
    )
    write_json(run_paths.root / "manifest.json", manifest.model_dump(mode="json"))
    write_json(run_paths.agent / "task-input.json", task_input.model_dump(mode="json"))


def build_turn_refs(run_paths: TranscriptRunPaths, facts: list[TurnFact]) -> list[TurnRef]:
    refs: list[TurnRef] = []
    for fact in sorted(facts, key=lambda item: item.turn_id):
        card_path, full_path, json_path = run_paths.turn_paths(fact.turn_id)
        refs.append(
            make_turn_ref(
                run_paths=run_paths,
                turn_id=fact.turn_id,
                session_id_hash=fact.session_id_hash,
                fact_json=json_path.read_text(encoding="utf-8"),
                card_md=card_path.read_text(encoding="utf-8"),
                full_md=full_path.read_text(encoding="utf-8"),
                bucket_flags=list(fact.mcp_burden.issue_flags),
            )
        )
    return refs


def build_usage_aggregate(
    *,
    run_id: str,
    window: dict[str, Any],
    facts: list[TurnFact],
    refs: list[TurnRef],
) -> UsageAggregate:
    return UsageAggregate(
        run_id=run_id,
        window=window,
        counts=build_counts(facts),
        distributions=build_distributions(facts),
        buckets=build_buckets(facts, refs),
    )


def build_subject_mcp_aggregate(
    *,
    run_id: str,
    window: dict[str, Any],
    facts: list[TurnFact],
    refs: list[TurnRef],
) -> SubjectAggregate:
    mcp_facts = [fact for fact in facts if fact.mcp_burden.total_mcp_calls > 0]
    mcp_ids = {fact.turn_id for fact in mcp_facts}
    return SubjectAggregate(
        run_id=run_id,
        subject_id="mcp",
        window=window,
        counts=build_counts(mcp_facts),
        distributions=build_distributions(mcp_facts),
        buckets=build_buckets(mcp_facts, [ref for ref in refs if ref.turn_id in mcp_ids]),
    )


def build_counts(facts: list[TurnFact]) -> dict[str, int]:
    return {
        "sessions_scanned": len({fact.session_id_hash for fact in facts}),
        "turns_total": len(facts),
        "turns_with_mcp": sum(1 for fact in facts if fact.mcp_burden.total_mcp_calls > 0),
        "mcp_calls_total": sum(fact.mcp_burden.total_mcp_calls for fact in facts),
        "tool_calls_total": sum(len(fact.tool_calls) for fact in facts),
        "turns_with_shell_fallback_after_mcp": sum(
            1 for fact in facts if fact.derived.get("shell_fallback_after_mcp") is True
        ),
    }


def build_distributions(facts: list[TurnFact]) -> dict[str, Any]:
    tool_calls = Counter(call.tool_name for fact in facts for call in fact.tool_calls)
    tool_families = Counter(call.tool_family for fact in facts for call in fact.tool_calls)
    evidence_roles = Counter(call.evidence_role for fact in facts for call in fact.mcp_call_sequence)
    evidence_confidence = Counter(
        call.evidence_role_confidence for fact in facts for call in fact.mcp_call_sequence
    )
    next_user = Counter(str(fact.derived.get("next_user_reaction") or "none") for fact in facts)
    return {
        "tool_calls": dict(tool_calls),
        "tool_families": dict(tool_families),
        "mcp_evidence_role": dict(evidence_roles),
        "mcp_evidence_role_confidence": dict(evidence_confidence),
        "next_user_reaction": dict(next_user),
    }


def build_buckets(facts: list[TurnFact], refs: list[TurnRef]) -> list[BucketMetric]:
    by_id = {ref.turn_id: ref for ref in refs}
    return [
        bucket(
            "repeated_same_tool",
            "same tool called >= 3 times in one turn",
            [fact for fact in facts if fact.mcp_burden.repeated_calls_by_tool],
            by_id,
            distribution_key="repeated_tools",
        ),
        bucket(
            "mcp_errors_or_detours",
            "one or more MCP calls had failed_or_detour evidence role",
            [fact for fact in facts if fact.mcp_burden.failed_or_detour_calls > 0],
            by_id,
        ),
        bucket(
            "mcp_calls_exceed_two",
            "turns where MCP call count is greater than two",
            [fact for fact in facts if fact.mcp_burden.total_mcp_calls > 2],
            by_id,
        ),
        bucket(
            "no_detected_adopted_evidence",
            "MCP was called but no summarized MCP evidence was detected in the final answer",
            [
                fact
                for fact in facts
                if fact.mcp_burden.total_mcp_calls > 0
                and fact.mcp_burden.used_in_final_calls == 0
            ],
            by_id,
        ),
        bucket(
            "shell_fallback_after_mcp",
            "a shell tool was called after an MCP tool in the same turn",
            [fact for fact in facts if fact.derived.get("shell_fallback_after_mcp") is True],
            by_id,
        ),
        bucket(
            "low_level_search_dominates",
            "low-level search tools account for a large share of MCP calls",
            [fact for fact in facts if "low_level_search_dominates" in fact.mcp_burden.issue_flags],
            by_id,
        ),
        bucket(
            "next_user_correction_candidate",
            "next user message has correction-candidate language",
            [
                fact
                for fact in facts
                if fact.derived.get("next_user_reaction") == "correction_candidate"
            ],
            by_id,
        ),
    ]


def bucket(
    bucket_id: str,
    definition: str,
    facts: list[TurnFact],
    by_id: dict[str, TurnRef],
    *,
    distribution_key: str | None = None,
) -> BucketMetric:
    distribution: dict[str, Any] = {}
    if distribution_key == "repeated_tools":
        counter = Counter(
            tool
            for fact in facts
            for tool in fact.mcp_burden.repeated_calls_by_tool
        )
        distribution["tools"] = dict(counter)
    return BucketMetric(
        bucket_id=bucket_id,
        definition=definition,
        count=len(facts),
        distribution=distribution,
        turn_refs=[by_id[fact.turn_id] for fact in facts if fact.turn_id in by_id],
    )


def bucket_payload(run_id: str, buckets: list[BucketMetric]) -> dict[str, Any]:
    return {
        "schema_version": "session_review.bucket_metrics.v1",
        "run_id": run_id,
        "buckets": [bucket.model_dump(mode="json") for bucket in buckets],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def assert_no_forbidden_keys(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    found = find_forbidden_keys(payload)
    if found:
        raise ValueError(f"{path} contains forbidden aggregate keys: {sorted(found)}")


def find_forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_AGGREGATE_KEYS:
                found.add(str(key))
            found.update(find_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(find_forbidden_keys(item))
    return found
