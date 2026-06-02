from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = "session-review"
    root: str | None = None
    codex_home: str = "~/.codex"
    output_dir: str = ".codex-local/session-review"


class SafetyConfig(BaseModel):
    include_raw_text: bool = False
    hash_identifiers: bool = True
    max_export_sensitivity: str = "S1"
    default_ttl_days: int = 30


class ReviewerConfig(BaseModel):
    enabled: list[str] = Field(
        default_factory=lambda: [
            "mcp_efficacy",
            "skill_utility",
            "subagent_value",
            "shell_fallback",
            "path_stability",
        ]
    )


class ThresholdConfig(BaseModel):
    min_cases_for_proposal: int = 3
    min_cases_for_staged: int = 6
    max_secret_leak_count: int = 0


class PromotionConfig(BaseModel):
    targets: list[str] = Field(
        default_factory=lambda: ["mcp_tool", "skill", "subagent_pattern", "debug_runbook_seed"]
    )


class ReviewProfile(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    reviewers: ReviewerConfig = Field(default_factory=ReviewerConfig)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    promotion: PromotionConfig = Field(default_factory=PromotionConfig)


class TaskFamilyConfig(BaseModel):
    trigger_keywords: list[str] = Field(default_factory=list)
    preferred_route: str = "unknown"
    expected_data: list[str] = Field(default_factory=list)


class PathStabilityReviewerConfig(BaseModel):
    group_by: list[str] = Field(default_factory=lambda: ["task_family", "recommended_path"])
    min_consistency_ratio: float = 0.7


class PackConfig(BaseModel):
    id: str = "default"
    version: str = "0.1.0"


class ReviewerPack(BaseModel):
    pack: PackConfig = Field(default_factory=PackConfig)
    tool_families: dict[str, list[str]] = Field(default_factory=dict)
    task_families: dict[str, TaskFamilyConfig] = Field(default_factory=dict)
    reviewers: dict[str, Any] = Field(default_factory=dict)

    def tools_for_family(self, family: str) -> set[str]:
        return {str(item) for item in self.tool_families.get(family, [])}

    def path_stability(self) -> PathStabilityReviewerConfig:
        raw = (self.reviewers or {}).get("path_stability") or {}
        return PathStabilityReviewerConfig.model_validate(raw)


def load_review_profile(path: str | Path | None) -> ReviewProfile:
    if not path:
        return ReviewProfile()
    data = _load_toml(path)
    return ReviewProfile.model_validate(data)


def load_reviewer_pack(path: str | Path | None) -> ReviewerPack:
    if not path:
        return ReviewerPack()
    data = _load_toml(path)
    return ReviewerPack.model_validate(data)


def _load_toml(path: str | Path) -> dict[str, Any]:
    with Path(path).expanduser().open("rb") as handle:
        return tomllib.load(handle)
