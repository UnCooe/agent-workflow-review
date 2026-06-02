from __future__ import annotations

import json
from pathlib import Path

from session_review.cli import run


def test_session_review_init_creates_project_workspace(tmp_path: Path) -> None:
    target = tmp_path / "demo-project"

    assert run(["init", "--target", str(target)]) == 0

    review_dir = target / ".session-review"
    profile = (review_dir / "review-profile.toml").read_text(encoding="utf-8")
    assert (review_dir / "reviewer-pack.toml").exists()
    assert (review_dir / ".gitignore").exists()
    assert (review_dir / "output").is_dir()
    assert (review_dir / "exports").is_dir()
    assert 'codex_home = "~/.codex"' in profile
    assert "include_raw_text = false" in profile


def test_session_review_cli_review_extract_decide_export(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    profile = tmp_path / "review-profile.toml"
    pack = tmp_path / "reviewer-pack.toml"
    session = tmp_path / "session.jsonl"
    profile.write_text(
        f"""
[project]
name = "test-project"
codex_home = "{tmp_path.as_posix()}"
output_dir = "{output_dir.as_posix()}"

[thresholds]
min_cases_for_proposal = 3

[reviewers]
enabled = ["path_stability"]
""".strip(),
        encoding="utf-8",
    )
    pack.write_text(
        """
[tool_families]
mcp = ["inspect_tts_request"]

[task_families.tts_provider_verification]
trigger_keywords = ["provider"]
preferred_route = "mcp_first"
expected_data = ["provider", "evidence_ref"]
""".strip(),
        encoding="utf-8",
    )
    records = []
    for index in range(3):
        records.extend(
            [
                {
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": f"第 {index} 次确认 provider",
                    }
                },
                {
                    "payload": {
                        "type": "function_call",
                        "name": "inspect_tts_request",
                        "call_id": f"mcp-{index}",
                        "arguments": json.dumps({"request_id": f"req-{index}"}),
                    }
                },
                {
                    "payload": {
                        "type": "function_call_output",
                        "call_id": f"mcp-{index}",
                        "output": json.dumps({"total": 1, "provider": "cartesia"}),
                    }
                },
                {"payload": {"type": "message", "role": "assistant", "content": "已确认 provider。"}},
            ]
        )
    session.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    assert run(["review", "--profile", str(profile), "--reviewer-pack", str(pack), "--session", str(session)]) == 0
    assert (output_dir / "review-packets.json").exists()
    assert (output_dir / "debug-artifact.json").exists()
    assert (output_dir / "review-report.md").exists()

    assert run(["extract", "--packets", str(output_dir / "review-packets.json"), "--profile", str(profile)]) == 0
    candidates_payload = json.loads((output_dir / "improvement-candidates.json").read_text(encoding="utf-8"))
    candidate_id = candidates_payload["candidates"][0]["candidate_id"]

    assert (
        run(
            [
                "decide",
                "--candidates",
                str(output_dir / "improvement-candidates.json"),
                "--candidate",
                candidate_id,
                "--decision",
                "staged",
                "--reason",
                "manual review passed",
            ]
        )
        == 0
    )
    export_path = output_dir / "seed.yaml"
    assert (
        run(
            [
                "export",
                "--candidates",
                str(output_dir / "improvement-candidates.json"),
                "--candidate",
                candidate_id,
                "--target",
                "debug_runbook_seed",
                "--output",
                str(export_path),
            ]
        )
        == 0
    )
    assert "debug_runbook_seed.v0" in export_path.read_text(encoding="utf-8")


def test_session_review_list_candidates_outputs_table_and_json(tmp_path: Path, capsys) -> None:
    candidate_file = tmp_path / "improvement-candidates.json"
    candidate_file.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "candidate_id": "cand-1",
                        "candidate_key": "key",
                        "target_type": "debug_runbook_seed",
                        "problem_pattern": "Stable debugging path observed.",
                        "trigger_signals": ["path_stability"],
                        "proposed_change": "Review seed.",
                        "recommended_path": ["inspect_request"],
                        "anti_patterns": [],
                        "fallback_rules": [],
                        "supporting_findings": ["finding-1", "finding-2"],
                        "maturity": "proposal",
                        "score": {"coverage": 60, "safety": 100},
                        "export_allowed": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert run(["list-candidates", "--candidates", str(candidate_file)]) == 0
    table_output = capsys.readouterr().out
    assert "candidate_id" in table_output
    assert "cand-1" in table_output
    assert "debug_runbook_seed" in table_output
    assert "Stable debugging path observed." in table_output

    assert run(["list-candidates", "--candidates", str(candidate_file), "--format", "json"]) == 0
    json_output = json.loads(capsys.readouterr().out)
    assert json_output[0]["candidate_id"] == "cand-1"


def test_redacted_demo_review_extract_and_safety(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    assert (
        run(
            [
                "review",
                "--profile",
                "examples/session-review/review-profile.toml",
                "--reviewer-pack",
                "examples/session-review/reviewer-pack.toml",
                "--session",
                "examples/session-review/sessions/redacted-session.jsonl",
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    review_payload = json.loads((output_dir / "review-packets.json").read_text(encoding="utf-8"))
    rendered_review = json.dumps(review_payload, ensure_ascii=False)
    assert "Confirm provider routing for redacted request alpha" not in rendered_review
    assert "confirmed provider routing with evidence" not in rendered_review
    assert "text_hash:" in rendered_review

    assert run(["extract", "--packets", str(output_dir / "review-packets.json")]) == 0
    candidate_payload = json.loads((output_dir / "improvement-candidates.json").read_text(encoding="utf-8"))
    rendered_candidates = json.dumps(candidate_payload, ensure_ascii=False)
    assert candidate_payload["candidates"]
    assert "Confirm provider routing for redacted request alpha" not in rendered_candidates
