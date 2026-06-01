from __future__ import annotations

import json
from pathlib import Path

from session_review.cli import run


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
