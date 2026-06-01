from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class SensitivityLevel(StrEnum):
    S0 = "S0"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


class AccessPolicy(StrEnum):
    LOCAL_ONLY = "local_only"
    RESTRICTED = "restricted"
    SHAREABLE_REDACTED = "shareable_redacted"


class SourceType(StrEnum):
    CODEX_JSONL = "codex_jsonl"
    OBSERVABILITY_EVENT = "observability_event"
    PACKET = "packet"
    REPORT = "report"


class AgentEventKind(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUBAGENT_SPAWN = "subagent_spawn"
    SUBAGENT_RESULT = "subagent_result"
    SUBAGENT_CLOSE = "subagent_close"


class ToolFamily(StrEnum):
    MCP = "mcp"
    SKILL = "skill"
    SHELL = "shell"
    SUBAGENT = "subagent"
    BROWSER = "browser"
    OTHER = "other"


class RouteMode(StrEnum):
    MCP_FIRST = "mcp_first"
    SOURCE_FIRST = "source_first"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class CaseOutcome(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class FindingType(StrEnum):
    MCP_EFFICACY = "mcp_efficacy"
    SKILL_UTILITY = "skill_utility"
    SUBAGENT_VALUE = "subagent_value"
    SHELL_FALLBACK = "shell_fallback"
    PATH_STABILITY = "path_stability"


class FindingVerdict(StrEnum):
    HELPFUL = "helpful"
    NOISY = "noisy"
    INEFFECTIVE = "ineffective"
    HARMFUL = "harmful"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TargetType(StrEnum):
    MCP_TOOL = "mcp_tool"
    SKILL = "skill"
    SUBAGENT_PATTERN = "subagent_pattern"
    DEBUG_RUNBOOK_SEED = "debug_runbook_seed"
    NONE = "none"


class CandidateStatus(StrEnum):
    OBSERVED = "observed"
    PROPOSAL = "proposal"
    REVIEWED = "reviewed"
    STAGED = "staged"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"


class DecisionAction(StrEnum):
    PROMOTE = "promote"
    DEFER = "defer"
    REJECT = "reject"
    DEPRECATE = "deprecate"


class RawRef(StrictModel):
    source_type: SourceType = SourceType.CODEX_JSONL
    source_id_hash: str
    locator: dict[str, Any] = Field(default_factory=dict)
    sensitivity: SensitivityLevel = SensitivityLevel.S0
    ttl_days: int = 30
    access_policy: AccessPolicy = AccessPolicy.LOCAL_ONLY


class AgentEvent(StrictModel):
    event_id: str
    session_id: str
    turn_index: int = 0
    ts: datetime | None = None
    cwd_hash: str | None = None
    kind: AgentEventKind
    tool_name: str | None = None
    tool_family: ToolFamily = ToolFamily.OTHER
    args_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    text_summary: str | None = None
    raw_ref: RawRef
    safety_level: SensitivityLevel = SensitivityLevel.S0


class ReviewCase(StrictModel):
    case_id: str
    session_id: str
    case_group_key: str
    user_goal_summary: str
    task_family: str = "unknown"
    expected_data: list[str] = Field(default_factory=list)
    expected_route: RouteMode = RouteMode.UNKNOWN
    actual_route: RouteMode = RouteMode.UNKNOWN
    events: list[AgentEvent] = Field(default_factory=list)
    tool_path: list[str] = Field(default_factory=list)
    recovery_path: list[str] = Field(default_factory=list)
    outcome: CaseOutcome = CaseOutcome.UNKNOWN
    raw_refs: list[RawRef] = Field(default_factory=list)


class ReviewPacket(StrictModel):
    packet_id: str
    case_id: str
    review_question: str = "Did the agent workflow reduce uncertainty and build evidence?"
    task_family: str = "unknown"
    expected_data: list[str] = Field(default_factory=list)
    observed_path: list[str] = Field(default_factory=list)
    route_quality: dict[str, Any] = Field(default_factory=dict)
    evidence_quality: dict[str, Any] = Field(default_factory=dict)
    efficiency: dict[str, Any] = Field(default_factory=dict)
    feedback_signals: list[str] = Field(default_factory=list)
    safety_summary: dict[str, Any] = Field(default_factory=dict)


class ReviewFinding(StrictModel):
    finding_id: str
    case_id: str
    reviewer_id: str
    type: FindingType
    verdict: FindingVerdict
    confidence: Confidence = Confidence.LOW
    evidence_refs: list[RawRef] = Field(default_factory=list)
    rationale: str
    suggested_target: TargetType = TargetType.NONE


class ImprovementCandidate(StrictModel):
    candidate_id: str
    candidate_key: str
    target_type: TargetType
    problem_pattern: str
    trigger_signals: list[str] = Field(default_factory=list)
    proposed_change: str
    recommended_path: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)
    fallback_rules: list[str] = Field(default_factory=list)
    supporting_findings: list[str] = Field(default_factory=list)
    maturity: CandidateStatus = CandidateStatus.OBSERVED
    score: dict[str, int] = Field(default_factory=dict)
    export_allowed: bool = False


class PromotionDecision(StrictModel):
    decision_id: str
    candidate_id: str
    status: CandidateStatus
    decision: DecisionAction
    reviewer: str
    reason: str
    required_actions: list[str] = Field(default_factory=list)
    export_allowed: bool = False
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReviewReport(StrictModel):
    report_id: str
    window: dict[str, Any] = Field(default_factory=dict)
    profile: str
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    top_findings: list[ReviewFinding] = Field(default_factory=list)
    candidates: list[ImprovementCandidate] = Field(default_factory=list)
    safety: dict[str, Any] = Field(default_factory=dict)
    debug_artifact_ref: RawRef


class DebugArtifact(StrictModel):
    artifact_id: str
    packets: list[ReviewPacket] = Field(default_factory=list)
    cases: list[ReviewCase] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    candidate_inputs: list[dict[str, Any]] = Field(default_factory=list)
    parser_warnings: list[str] = Field(default_factory=list)
    redaction_warnings: list[str] = Field(default_factory=list)
