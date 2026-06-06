from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .models import (
    ReviewObjective,
    ReviewSubject,
    SignalPackStatus,
    SubjectCollectorsConfig,
    SubjectReviewPack,
    SubjectSignalPack,
)


def subject_dir(target: str | Path, subject_id: str) -> Path:
    return Path(target).expanduser() / ".session-review" / "subjects" / subject_id


def load_subject_pack(
    path: str | Path,
    *,
    signal_pack_path: str | Path | None = None,
    include_proposed_signal_pack: bool = False,
) -> SubjectReviewPack:
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
            "signal_pack": _load_runtime_signal_pack(
                base,
                signal_pack_path=signal_pack_path,
                include_proposed=include_proposed_signal_pack,
            ),
        }
    )


def load_subject(path: str | Path) -> ReviewSubject:
    data = _load_toml(path)
    return ReviewSubject.model_validate(data.get("subject", data))


def load_objective(path: str | Path) -> ReviewObjective:
    data = _load_toml(path)
    return ReviewObjective.model_validate(data.get("objective", data))


def load_signal_pack(path: str | Path) -> SubjectSignalPack:
    data = _load_toml(path)
    return SubjectSignalPack.model_validate(data)


def _load_runtime_signal_pack(
    base: Path,
    *,
    signal_pack_path: str | Path | None,
    include_proposed: bool,
) -> dict[str, Any] | None:
    path = Path(signal_pack_path).expanduser() if signal_pack_path else base / "signal-pack.toml"
    if not path.exists():
        return None
    signal_pack = load_signal_pack(path)
    status = SignalPackStatus(str(signal_pack.pack.status))
    if status in {SignalPackStatus.REVIEWED, SignalPackStatus.ACTIVE} or include_proposed:
        return signal_pack.model_dump(mode="json")
    return None


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


def dump_default_signal_pack(subject_id: str) -> str:
    return f"""[pack]
id = "{subject_id}-signals"
version = "0.2.0"
generated_by = "manual"
status = "proposed"
source_refs = []

[positive_signals]
tool_names = []
commands = []
skill_names = []
mcp_names = []
subagent_names = []
text = []
error_signals = []
user_hint_signals = []

[domain_anchors]
required_any = []
required_all = []

[negative_signals]
exclude_contexts = []
commands = []
text = []

[ambiguous_terms]
terms = []
require_domain_anchor = true
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
