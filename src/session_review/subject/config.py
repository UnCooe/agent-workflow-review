from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .models import ReviewObjective, ReviewSubject, SubjectCollectorsConfig, SubjectReviewPack


def subject_dir(target: str | Path, subject_id: str) -> Path:
    return Path(target).expanduser() / ".session-review" / "subjects" / subject_id


def load_subject_pack(path: str | Path) -> SubjectReviewPack:
    base = Path(path).expanduser()
    subject = load_subject(base / "subject.toml")
    objective = load_objective(base / "objective.toml")
    collector_data = _load_toml(base / "subject.toml").get("collectors", {})
    objective_data = _load_toml(base / "objective.toml")
    if "collectors" in objective_data:
        collector_data = _deep_merge(collector_data, objective_data["collectors"])
    context_window = _deep_merge(
        _load_toml(base / "subject.toml").get("context_window", {}),
        objective_data.get("context_window", {}),
    )
    return SubjectReviewPack.model_validate(
        {
            "subject": subject.model_dump(mode="json"),
            "objective": objective.model_dump(mode="json"),
            "collectors": collector_data,
            "context_window": context_window,
        }
    )


def load_subject(path: str | Path) -> ReviewSubject:
    data = _load_toml(path)
    return ReviewSubject.model_validate(data.get("subject", data))


def load_objective(path: str | Path) -> ReviewObjective:
    data = _load_toml(path)
    return ReviewObjective.model_validate(data.get("objective", data))


def dump_default_subject(subject_id: str) -> str:
    return f"""[subject]
subject_id = "{subject_id}"
subject_type = "cli_workflow"
display_name = "{subject_id}"
description = "Cross-project subject review target."
aliases = ["{subject_id}"]
owners = []
safety_tags = []

[context_window]
before_events = 3
after_events = 5
merge_distance_events = 3

[collectors.direct_usage]
tool_names = []
commands = []
skill_names = []
mcp_names = []
subagent_names = []
"""


def dump_default_objective() -> str:
    return """[objective]
objective_id = "default"
questions = [
  "When was this subject used?",
  "When should it have been used but was not?",
  "Did the subject improve the workflow?",
  "Should the tool, skill, MCP schema, or docs change?"
]
expected_use_cases = []
missed_use_signals = []
success_signals = ["success", "connected", "resolved", "confirmed"]
failure_signals = ["timeout", "failed", "error", "retry"]
candidate_targets = ["tool_interface", "skill", "docs", "workflow_pattern"]

[collectors.contextual_need]
error_signals = ["timeout", "connection reset", "connection refused", "failed", "error"]
slow_signals = ["slow", "latency", "timeout"]
user_hint_signals = []

[collectors.fallback]
inefficient_tools = []
retry_threshold = 2
missing_direct_usage_penalty = true
"""


def _load_toml(path: str | Path) -> dict[str, Any]:
    with Path(path).expanduser().open("rb") as handle:
        return tomllib.load(handle)


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    output = dict(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(output.get(key), dict):
            output[key] = _deep_merge(output[key], value)
        else:
            output[key] = value
    return output
