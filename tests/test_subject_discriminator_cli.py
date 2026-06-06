from __future__ import annotations

import json
import shutil
from pathlib import Path

from session_review.cli import run


def test_subject_init_generates_signal_pack(tmp_path: Path) -> None:
    target = tmp_path / "project"

    assert run(["subject", "init", "server-ssh", "--target", str(target)]) == 0
    base = target / ".session-review" / "subjects" / "server-ssh"

    signal_pack = (base / "signal-pack.toml").read_text(encoding="utf-8")
    scaffold_prompt = (base / "scaffold-prompt.md").read_text(encoding="utf-8")

    assert "[positive_signals]" in signal_pack
    assert "[negative_signals]" in signal_pack
    assert "domain anchors" in scaffold_prompt
    assert "Do not create a global keyword list" in scaffold_prompt


def test_signal_pack_validation_warns_for_ambiguous_unanchored_pack(
    tmp_path: Path,
    capsys,
) -> None:
    target = tmp_path / "project"
    assert run(["subject", "init", "ambiguous", "--target", str(target)]) == 0
    base = target / ".session-review" / "subjects" / "ambiguous"
    (base / "signal-pack.toml").write_text(
        """[pack]
id = "ambiguous-signals"

[positive_signals]
text = ["ssh"]

[ambiguous_terms]
terms = ["ssh"]
require_domain_anchor = true
""",
        encoding="utf-8",
    )

    assert run(["subject", "validate-signals", "ambiguous", "--target", str(target)]) == 0
    payload = json.loads(capsys.readouterr().out)
    rendered = json.dumps(payload, ensure_ascii=False)

    assert "ambiguous_terms_without_required_any_domain_anchor" in payload["warnings"]
    assert payload["signal_pack_ref"]["status"] == "proposed"
    assert payload["signal_counts"]["positive"] == 1
    assert "ssh" not in rendered


def test_proposed_signal_pack_does_not_affect_collection_by_default(tmp_path: Path) -> None:
    target = tmp_path / "project"
    assert run(["subject", "init", "proposed-only", "--target", str(target)]) == 0
    base = target / ".session-review" / "subjects" / "proposed-only"
    sessions = base / "sessions"
    sessions.mkdir()
    (base / "subject.toml").write_text(
        """[subject]
subject_id = "proposed-only"
subject_type = "cli_workflow"
display_name = "proposed-only"

[collectors.direct_usage]
tool_names = []
commands = []
skill_names = []
mcp_names = []
subagent_names = []
""",
        encoding="utf-8",
    )
    (base / "objective.toml").write_text(
        """[objective]
objective_id = "default"
success_signals = []
failure_signals = []

[collectors.contextual_need]
error_signals = []
slow_signals = []
user_hint_signals = []

[collectors.fallback]
inefficient_tools = []
retry_threshold = 2
missing_direct_usage_penalty = true
""",
        encoding="utf-8",
    )
    (base / "signal-pack.toml").write_text(
        """[pack]
id = "proposed-only-signals"
status = "proposed"

[positive_signals]
commands = ["unique-proposed-command"]
""",
        encoding="utf-8",
    )
    (sessions / "session.jsonl").write_text(
        '\n'.join(
            [
                '{"type":"session_meta","payload":{"id":"proposed-only","cwd":"workspace/proposed"}}',
                '{"payload":{"type":"function_call","name":"functions.exec_command","call_id":"call-1","arguments":"{\\"cmd\\":\\"unique-proposed-command\\"}"}}',
                '{"payload":{"type":"function_call_output","call_id":"call-1","output":"success"}}',
            ]
        ),
        encoding="utf-8",
    )

    assert (
        run(
            [
                "subject",
                "collect",
                "proposed-only",
                "--target",
                str(target),
                "--session-glob",
                str(sessions / "*.jsonl"),
            ]
        )
        == 0
    )
    payload = json.loads((base / "output" / "subject-episodes.json").read_text())
    assert payload["episodes"] == []

    assert (
        run(
            [
                "subject",
                "collect",
                "proposed-only",
                "--target",
                str(target),
                "--session-glob",
                str(sessions / "*.jsonl"),
                "--include-proposed-signal-pack",
            ]
        )
        == 0
    )
    payload = json.loads((base / "output" / "subject-episodes.json").read_text())
    assert len(payload["episodes"]) == 1


def test_subject_discriminator_rejects_git_ssh_and_keeps_uncertain_review_only(
    tmp_path: Path,
    capsys,
) -> None:
    target = tmp_path / "review-target"
    source = Path("examples/subject-review/server-ssh")
    base = target / ".session-review" / "subjects" / "server-ssh"
    shutil.copytree(source, base)
    output_dir = base / "output"
    output_dir.mkdir(exist_ok=True)

    assert (
        run(
            [
                "subject",
                "collect",
                "server-ssh",
                "--target",
                str(target),
                "--session-glob",
                str(base / "sessions" / "*.jsonl"),
            ]
        )
        == 0
    )
    assert run(["subject", "discriminate", "server-ssh", "--target", str(target)]) == 0

    hints_payload = json.loads((output_dir / "subject-attribution-hints.json").read_text())
    episodes_payload = json.loads((output_dir / "subject-discriminated-episodes.json").read_text())
    collisions_payload = json.loads((output_dir / "subject-collisions.json").read_text())
    rendered = json.dumps(
        {
            "hints": hints_payload,
            "episodes": episodes_payload,
            "collisions": collisions_payload,
        },
        ensure_ascii=False,
    )

    statuses = {item["status"] for item in hints_payload["attribution_hints"]}
    reason_codes = {
        reason
        for item in hints_payload["attribution_hints"]
        for reason in item["reason_codes"]
    }

    assert "rejected" in statuses
    assert "confirmed" in statuses
    assert "ambiguous" in statuses
    assert "negative_signal_matched" in reason_codes
    assert "rejected_by_negative_without_anchor" in reason_codes
    assert "competing_domain_signals" in reason_codes
    assert collisions_payload["collisions"]
    assert len(collisions_payload["collisions"]) >= 2

    assert "git@github.com" not in rendered
    assert "ops@redacted-host" not in rendered
    assert "workspace/server-project" not in rendered
    assert "server host is timing out" not in rendered

    hints_by_status = {item["status"]: item for item in hints_payload["attribution_hints"]}
    rejected = hints_by_status["rejected"]
    ambiguous = hints_by_status["ambiguous"]
    confirmed = hints_by_status["confirmed"]
    assert rejected["review_only"] is True
    assert "rejected_by_negative_without_anchor" in rejected["reason_codes"]
    assert ambiguous["review_only"] is True
    assert "competing_domain_signals" in ambiguous["reason_codes"]
    assert confirmed["review_only"] is False
    assert "direct_usage_with_domain_anchor" in confirmed["reason_codes"]

    assert run(["subject", "review", "server-ssh", "--target", str(target), "--with-collision"]) == 0
    report = (output_dir / "subject-review-report.md").read_text(encoding="utf-8")
    candidates_payload = json.loads(
        (output_dir / "subject-improvement-candidates.json").read_text(encoding="utf-8")
    )
    rendered_candidates = json.dumps(candidates_payload, ensure_ascii=False)

    assert episodes_payload["schema_version"] == "session_review.subject_discriminated_episodes.v0_2"
    findings_payload = json.loads((output_dir / "subject-findings.json").read_text(encoding="utf-8"))
    assert findings_payload["schema_version"] == "session_review.subject_findings.v0_2"
    assert candidates_payload["schema_version"] == "session_review.subject_candidates.v0_2"
    assert "Collisions" in report
    assert "FALSE_POSITIVE" not in report
    assert "review_only=true" in report
    assert "git@github.com" not in report
    assert "ops@redacted-host" not in report
    assert "git@github.com" not in rendered_candidates

    assert run(["subject", "list-candidates", "server-ssh", "--target", str(target)]) == 0
    table = capsys.readouterr().out
    assert "candidate_id" in table
