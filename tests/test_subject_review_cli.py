from __future__ import annotations

import json
import shutil
from pathlib import Path

from session_review.cli import run


def test_subject_init_and_scaffold_generate_workspace_and_prompts(tmp_path: Path) -> None:
    target = tmp_path / "project"

    assert run(["subject", "init", "vpn-tool", "--target", str(target)]) == 0
    base = target / ".session-review" / "subjects" / "vpn-tool"

    assert (base / "subject.toml").exists()
    assert (base / "objective.toml").exists()
    assert (base / "output").is_dir()
    assert (base / "exports").is_dir()
    assert "include_raw_text" not in (base / "subject.toml").read_text(encoding="utf-8")

    assert (
        run(
            [
                "subject",
                "scaffold",
                "vpn-tool",
                "--target",
                str(target),
                "--tool-root",
                "tools/vpn",
                "--skill-path",
                "skills/vpn/SKILL.md",
                "--mcp-schema",
                "schemas/vpn.json",
            ]
        )
        == 0
    )
    scaffold_prompt = (base / "scaffold-prompt.md").read_text(encoding="utf-8")
    automation_prompt = (base / "codex-automation-prompt.md").read_text(encoding="utf-8")
    assert "tools/vpn" in scaffold_prompt
    assert "Do not modify tools" in scaffold_prompt
    assert "last 24 hours" in automation_prompt
    assert "Do not automatically modify tools" in automation_prompt


def test_subject_collect_review_list_and_export_cross_project(tmp_path: Path, capsys) -> None:
    target = tmp_path / "review-target"
    source = Path("examples/subject-review/vpn-tool")
    base = target / ".session-review" / "subjects" / "vpn-tool"
    shutil.copytree(source, base)
    output_dir = base / "output"
    exports_dir = base / "exports"
    output_dir.mkdir(exist_ok=True)
    exports_dir.mkdir(exist_ok=True)

    sessions_glob = str(base / "sessions" / "*.jsonl")
    assert (
        run(
            [
                "subject",
                "collect",
                "vpn-tool",
                "--target",
                str(target),
                "--session-glob",
                sessions_glob,
            ]
        )
        == 0
    )
    episodes_payload = json.loads((output_dir / "subject-episodes.json").read_text(encoding="utf-8"))
    rendered_episodes = json.dumps(episodes_payload, ensure_ascii=False)
    episodes = episodes_payload["episodes"]
    all_signals = {signal for episode in episodes for signal in episode["matched_signals"]}

    assert len(episodes) >= 2
    assert "direct_usage" in all_signals
    assert "missed_opportunity" in all_signals
    assert "fallback:repeated_inefficient_path" in all_signals
    assert any(episode["before_context"] for episode in episodes)
    assert any(episode["after_context"] for episode in episodes)
    assert "workspace/project-a" not in rendered_episodes
    assert "Server connection is timing out" not in rendered_episodes
    assert "success: connected through alternate region" not in rendered_episodes
    assert "netctl region test --target redacted-host" not in rendered_episodes
    assert "session_file_hash" in rendered_episodes

    assert run(["subject", "review", "vpn-tool", "--target", str(target)]) == 0
    report = (output_dir / "subject-review-report.md").read_text(encoding="utf-8")
    candidates_payload = json.loads(
        (output_dir / "subject-improvement-candidates.json").read_text(encoding="utf-8")
    )
    findings_payload = json.loads((output_dir / "subject-findings.json").read_text(encoding="utf-8"))
    rendered_candidates = json.dumps(candidates_payload, ensure_ascii=False)
    assert "Subject Review Report" in report
    assert findings_payload["schema_version"] == "session_review.subject_findings.v0_2"
    assert candidates_payload["schema_version"] == "session_review.subject_candidates.v0_2"
    assert candidates_payload["candidates"]
    assert any(
        item["target_type"] in {"tool_interface", "skill", "docs"}
        for item in candidates_payload["candidates"]
    )
    assert "Server connection is timing out" not in report
    assert "Server connection is timing out" not in rendered_candidates

    assert run(["subject", "list-candidates", "vpn-tool", "--target", str(target)]) == 0
    table = capsys.readouterr().out
    assert "candidate_id" in table
    assert "vpn-tool" not in table or "target_type" in table

    assert (
        run(
            [
                "subject",
                "list-candidates",
                "vpn-tool",
                "--target",
                str(target),
                "--format",
                "json",
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    candidate_id = listed[0]["candidate_id"]

    assert (
        run(
            [
                "subject",
                "export",
                "vpn-tool",
                "--target",
                str(target),
                "--candidate",
                candidate_id,
            ]
        )
        == 0
    )
    export_text = (exports_dir / f"{candidate_id}.yaml").read_text(encoding="utf-8")
    assert "subject_candidate_review_bundle.v0" in export_text
    assert "auto_promote: false" in export_text


def test_subject_collect_requires_explicit_scope_when_no_sessions(tmp_path: Path) -> None:
    target = tmp_path / "project"
    assert run(["subject", "init", "vpn-tool", "--target", str(target)]) == 0

    try:
        run(["subject", "collect", "vpn-tool", "--target", str(target)])
    except SystemExit as exc:
        assert "requires --session/--session-glob or explicit --all-projects" in str(exc)
    else:
        raise AssertionError("subject collect should require an explicit scope")
