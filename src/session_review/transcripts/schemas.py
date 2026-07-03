from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from session_review.models import StrictModel


class ArtifactHashes(StrictModel):
    fact_json: str
    card_md: str
    full_md: str


class TurnPaths(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        use_enum_values=True,
    )

    card_md: str
    full_md: str
    json_path: str = Field(alias="json", serialization_alias="json")


class TurnRef(StrictModel):
    turn_id: str
    paths: TurnPaths
    hashes: ArtifactHashes
    session_id_hash: str
    bucket_flags: list[str] = Field(default_factory=list)


class TextBlock(StrictModel):
    text: str = ""
    char_count: int = 0
    raw_ref: dict[str, Any] | None = None


class ToolCallFact(StrictModel):
    index: int
    tool_name: str
    tool_family: str
    args_summary: dict[str, Any] = Field(default_factory=dict)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    result_source: str | None = None
    evidence_role: str = "unknown"
    evidence_role_confidence: str = "low"
    evidence_role_method: str = "unknown"
    evidence_matches: list[dict[str, Any]] = Field(default_factory=list)
    raw_ref: dict[str, Any]
    result_raw_ref: dict[str, Any] | None = None


class TimelineItem(StrictModel):
    kind: str
    timestamp: str = ""
    text: str = ""
    tool_name: str | None = None
    tool_family: str | None = None
    raw_ref: dict[str, Any]


class McpBurden(StrictModel):
    total_mcp_calls: int = 0
    used_in_final_calls: int = 0
    failed_or_detour_calls: int = 0
    low_level_search_calls: int = 0
    repeated_calls_by_tool: dict[str, int] = Field(default_factory=dict)
    issue_flags: list[str] = Field(default_factory=list)


class TurnFact(StrictModel):
    schema_version: str = "session_review.turn_fact.v1"
    turn_id: str
    session_id_hash: str
    source_file_hash: str
    turn_index: int
    access_policy: str = "local_only"
    time_range: dict[str, str] = Field(default_factory=dict)
    user: TextBlock
    timeline: list[TimelineItem] = Field(default_factory=list)
    tool_calls: list[ToolCallFact] = Field(default_factory=list)
    mcp_call_sequence: list[ToolCallFact] = Field(default_factory=list)
    mcp_burden: McpBurden = Field(default_factory=McpBurden)
    adopted_evidence: list[dict[str, Any]] = Field(default_factory=list)
    final_answer: TextBlock = Field(default_factory=TextBlock)
    next_user: TextBlock = Field(default_factory=TextBlock)
    raw_refs: list[dict[str, Any]] = Field(default_factory=list)
    derived: dict[str, Any] = Field(default_factory=dict)


class BucketMetric(StrictModel):
    bucket_id: str
    definition: str
    count: int
    distribution: dict[str, Any] = Field(default_factory=dict)
    turn_refs: list[TurnRef] = Field(default_factory=list)


class UsageAggregate(StrictModel):
    schema_version: str = "session_review.usage_aggregate.v1"
    run_id: str
    window: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    distributions: dict[str, Any] = Field(default_factory=dict)
    buckets: list[BucketMetric] = Field(default_factory=list)


class SubjectAggregate(StrictModel):
    schema_version: str = "session_review.subject_usage_aggregate.v1"
    run_id: str
    subject_id: str
    window: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    distributions: dict[str, Any] = Field(default_factory=dict)
    buckets: list[BucketMetric] = Field(default_factory=list)


class RunManifest(StrictModel):
    schema_version: str = "session_review.run_manifest.v1"
    run_id: str
    window: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)


class AgentTaskInput(StrictModel):
    schema_version: str = "session_review.agent_task_input.v1"
    run_id: str
    required_skill: str = "mcp-session-review"
    entry_artifacts: dict[str, str] = Field(default_factory=dict)
    facts_root: str = "facts/turns"
    raw_session_access: str = "disabled_by_default"


class SessionIndex(StrictModel):
    schema_version: str = "session_review.session_fact_index.v1"
    session_id_hash: str
    source_file_hash: str
    source_file: str
    turn_count: int
    turns: list[TurnRef] = Field(default_factory=list)
