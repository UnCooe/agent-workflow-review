from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from session_review.models import CandidateStatus, RawRef, SensitivityLevel, ToolFamily


class StrictSubjectModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class SubjectType(StrEnum):
    TOOL = "tool"
    MCP = "mcp"
    SKILL = "skill"
    SUBAGENT_PATTERN = "subagent_pattern"
    CLI_WORKFLOW = "cli_workflow"
    OTHER = "other"


class SubjectCandidateTarget(StrEnum):
    TOOL_INTERFACE = "tool_interface"
    SKILL = "skill"
    MCP_SCHEMA = "mcp_schema"
    DOCS = "docs"
    WORKFLOW_PATTERN = "workflow_pattern"


class SubjectFindingType(StrEnum):
    EFFECTIVE_USAGE = "effective_usage"
    INEFFECTIVE_USAGE = "ineffective_usage"
    MISSED_OPPORTUNITY = "missed_opportunity"
    SKILL_HELPFUL = "skill_helpful"
    SKILL_NOT_HELPFUL = "skill_not_helpful"
    INTERFACE_FRICTION = "interface_friction"
    REPEATED_FAILURE_MODE = "repeated_failure_mode"
    ACCIDENTAL_NOISE = "accidental_noise"


class ReviewSubject(StrictSubjectModel):
    subject_id: str
    subject_type: SubjectType = SubjectType.OTHER
    display_name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    owners: list[str] = Field(default_factory=list)
    safety_tags: list[str] = Field(default_factory=list)


class ReviewObjective(StrictSubjectModel):
    objective_id: str = "default"
    questions: list[str] = Field(default_factory=list)
    expected_use_cases: list[str] = Field(default_factory=list)
    missed_use_signals: list[str] = Field(default_factory=list)
    success_signals: list[str] = Field(default_factory=list)
    failure_signals: list[str] = Field(default_factory=list)
    candidate_targets: list[SubjectCandidateTarget] = Field(
        default_factory=lambda: [
            SubjectCandidateTarget.TOOL_INTERFACE,
            SubjectCandidateTarget.SKILL,
            SubjectCandidateTarget.DOCS,
        ]
    )


class DirectUsageCollectorConfig(StrictSubjectModel):
    tool_names: list[str] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    mcp_names: list[str] = Field(default_factory=list)
    subagent_names: list[str] = Field(default_factory=list)


class ContextualNeedCollectorConfig(StrictSubjectModel):
    error_signals: list[str] = Field(default_factory=list)
    slow_signals: list[str] = Field(default_factory=list)
    user_hint_signals: list[str] = Field(default_factory=list)


class FallbackCollectorConfig(StrictSubjectModel):
    inefficient_tools: list[str] = Field(default_factory=list)
    retry_threshold: int = 2
    missing_direct_usage_penalty: bool = True


class SubjectCollectorsConfig(StrictSubjectModel):
    direct_usage: DirectUsageCollectorConfig = Field(default_factory=DirectUsageCollectorConfig)
    contextual_need: ContextualNeedCollectorConfig = Field(default_factory=ContextualNeedCollectorConfig)
    fallback: FallbackCollectorConfig = Field(default_factory=FallbackCollectorConfig)


class ContextWindowConfig(StrictSubjectModel):
    before_events: int = 3
    after_events: int = 5
    merge_distance_events: int = 3


class SubjectReviewPack(StrictSubjectModel):
    subject: ReviewSubject
    objective: ReviewObjective = Field(default_factory=ReviewObjective)
    collectors: SubjectCollectorsConfig = Field(default_factory=SubjectCollectorsConfig)
    context_window: ContextWindowConfig = Field(default_factory=ContextWindowConfig)


class SubjectEventSummary(StrictSubjectModel):
    event_id: str
    kind: str
    tool_name: str | None = None
    tool_family: ToolFamily = ToolFamily.OTHER
    args_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    text_summary: dict[str, Any] | None = None
    signal_ids: list[str] = Field(default_factory=list)
    raw_ref: RawRef
    safety_level: SensitivityLevel = SensitivityLevel.S0


class SubjectEpisode(StrictSubjectModel):
    episode_id: str
    subject_id: str
    session_id: str
    project_hint: dict[str, Any] = Field(default_factory=dict)
    matched_signals: list[str] = Field(default_factory=list)
    relevance_score: int = 0
    before_context: list[SubjectEventSummary] = Field(default_factory=list)
    subject_events: list[SubjectEventSummary] = Field(default_factory=list)
    after_context: list[SubjectEventSummary] = Field(default_factory=list)
    outcome_hint: str = "unknown"
    raw_refs: list[RawRef] = Field(default_factory=list)
    safety_summary: dict[str, Any] = Field(default_factory=dict)


class SubjectFinding(StrictSubjectModel):
    finding_id: str
    subject_id: str
    type: SubjectFindingType
    confidence: str = "low"
    episode_ids: list[str] = Field(default_factory=list)
    rationale: str
    target_type: SubjectCandidateTarget | None = None


class SubjectImprovementCandidate(StrictSubjectModel):
    candidate_id: str
    subject_id: str
    target_type: SubjectCandidateTarget
    problem_pattern: str
    evidence_episode_ids: list[str] = Field(default_factory=list)
    recommended_change: str
    anti_patterns: list[str] = Field(default_factory=list)
    maturity: CandidateStatus = CandidateStatus.OBSERVED
    export_allowed: bool = False
    score: dict[str, int] = Field(default_factory=dict)
