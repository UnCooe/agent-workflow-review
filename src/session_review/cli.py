from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from session_review.adapters.codex_jsonl import iter_recent_session_files, parse_session_files
from session_review.candidates import (
    build_candidates,
    can_export,
    create_decision,
    latest_decision_by_candidate,
    render_candidates_markdown,
)
from session_review.config import ReviewProfile, ReviewerPack, load_review_profile, load_reviewer_pack
from session_review.exporters.debug_runbook import render_debug_runbook_seed
from session_review.models import (
    CandidateStatus,
    ImprovementCandidate,
    PromotionDecision,
    ReviewCase,
    ReviewFinding,
)
from session_review.packets import (
    build_debug_artifact,
    build_review_report,
    packets_from_cases,
    render_report_markdown,
)
from session_review.reviewers import run_reviewers
from session_review.safety import SafetyPolicy, stable_hash
from session_review.subject.collectors import collect_subject_episodes
from session_review.subject.config import (
    dump_default_objective,
    dump_default_subject,
    load_subject_pack,
    subject_dir,
)
from session_review.subject.exporters import render_subject_candidate_export
from session_review.subject.models import SubjectEpisode, SubjectFinding, SubjectImprovementCandidate
from session_review.subject.prompts import write_prompts
from session_review.subject.review import (
    render_subject_candidates_table,
    review_subject_episodes,
)


SCHEMA_REVIEW = "session_review.review.v0"
SCHEMA_CANDIDATES = "session_review.candidates.v0"
SCHEMA_DECISIONS = "session_review.decisions.v0"
SCHEMA_SUBJECT_EPISODES = "session_review.subject_episodes.v0"
SCHEMA_SUBJECT_FINDINGS = "session_review.subject_findings.v0"
SCHEMA_SUBJECT_CANDIDATES = "session_review.subject_candidates.v0"


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def main() -> None:
    raise SystemExit(run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session-review")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a project-local .session-review workspace.")
    init.add_argument("--target", default=".")
    init.set_defaults(func=cmd_init)

    review = sub.add_parser("review", help="Parse Codex JSONL and produce packets/report/artifact.")
    review.add_argument("--profile", default=None)
    review.add_argument("--reviewer-pack", default=None)
    review.add_argument("--since-days", type=int, default=7)
    review.add_argument("--session", action="append", default=[], help="Explicit Codex session JSONL path.")
    review.add_argument("--session-glob", default=None, help="Glob for session JSONL files.")
    review.add_argument("--project-root", default=None)
    review.add_argument("--output-dir", default=None)
    review.set_defaults(func=cmd_review)

    extract = sub.add_parser("extract", help="Extract improvement candidates from review packets.")
    extract.add_argument("--packets", required=True)
    extract.add_argument("--profile", default=None)
    extract.add_argument("--decisions", default=None)
    extract.add_argument("--output-dir", default=None)
    extract.set_defaults(func=cmd_extract)

    decide = sub.add_parser("decide", help="Record a manual promotion lifecycle decision.")
    decide.add_argument("--candidates", default=".codex-local/session-review/improvement-candidates.json")
    decide.add_argument("--decisions", default=None)
    decide.add_argument("--candidate", required=True)
    decide.add_argument("--decision", required=True, choices=[item.value for item in CandidateStatus])
    decide.add_argument("--reason", required=True)
    decide.add_argument("--reviewer", default="manual")
    decide.set_defaults(func=cmd_decide)

    list_candidates = sub.add_parser("list-candidates", help="List extracted improvement candidates.")
    list_candidates.add_argument("--candidates", default=".codex-local/session-review/improvement-candidates.json")
    list_candidates.add_argument("--format", choices=["table", "json"], default="table")
    list_candidates.set_defaults(func=cmd_list_candidates)

    export = sub.add_parser("export", help="Export a manually staged/promoted debug_runbook_seed.")
    export.add_argument("--candidates", default=".codex-local/session-review/improvement-candidates.json")
    export.add_argument("--decisions", default=None)
    export.add_argument("--candidate", required=True)
    export.add_argument("--target", required=True, choices=["debug_runbook_seed"])
    export.add_argument("--output", default=None)
    export.set_defaults(func=cmd_export)

    subject = sub.add_parser("subject", help="Run subject-scoped cross-project review.")
    subject_sub = subject.add_subparsers(dest="subject_command", required=True)

    subject_init = subject_sub.add_parser("init", help="Create a subject review workspace.")
    subject_init.add_argument("subject_id")
    subject_init.add_argument("--target", default=".")
    subject_init.set_defaults(func=cmd_subject_init)

    subject_scaffold = subject_sub.add_parser("scaffold", help="Generate subject scaffold prompts.")
    subject_scaffold.add_argument("subject_id")
    subject_scaffold.add_argument("--target", default=".")
    subject_scaffold.add_argument("--tool-root", action="append", default=[])
    subject_scaffold.add_argument("--skill-path", action="append", default=[])
    subject_scaffold.add_argument("--mcp-schema", action="append", default=[])
    subject_scaffold.set_defaults(func=cmd_subject_scaffold)

    subject_collect = subject_sub.add_parser("collect", help="Collect subject episodes across sessions.")
    subject_collect.add_argument("subject_id")
    subject_collect.add_argument("--target", default=".")
    subject_collect.add_argument("--all-projects", action="store_true")
    subject_collect.add_argument("--since-hours", type=int, default=24)
    subject_collect.add_argument("--session", action="append", default=[])
    subject_collect.add_argument("--session-glob", default=None)
    subject_collect.set_defaults(func=cmd_subject_collect)

    subject_review = subject_sub.add_parser("review", help="Review collected subject episodes.")
    subject_review.add_argument("subject_id")
    subject_review.add_argument("--target", default=".")
    subject_review.add_argument("--episodes", default=None)
    subject_review.set_defaults(func=cmd_subject_review)

    subject_list = subject_sub.add_parser("list-candidates", help="List subject improvement candidates.")
    subject_list.add_argument("subject_id")
    subject_list.add_argument("--target", default=".")
    subject_list.add_argument("--format", choices=["table", "json"], default="table")
    subject_list.set_defaults(func=cmd_subject_list_candidates)

    subject_export = subject_sub.add_parser("export", help="Export a subject candidate review bundle.")
    subject_export.add_argument("subject_id")
    subject_export.add_argument("--target", default=".")
    subject_export.add_argument("--candidate", required=True)
    subject_export.add_argument("--output", default=None)
    subject_export.set_defaults(func=cmd_subject_export)
    return parser


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser()
    review_dir = target / ".session-review"
    output_dir = review_dir / "output"
    exports_dir = review_dir / "exports"
    review_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    _write_text_if_missing(review_dir / "review-profile.toml", _default_profile_toml())
    _write_text_if_missing(review_dir / "reviewer-pack.toml", _default_reviewer_pack_toml())
    _write_text_if_missing(review_dir / ".gitignore", "output/\nexports/\n*.json\n*.md\n*.yaml\n")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile).expanduser() if args.profile else None
    profile = load_review_profile(profile_path)
    reviewer_pack_path = _resolve_reviewer_pack_path(args.reviewer_pack, profile_path)
    reviewer_pack = load_reviewer_pack(reviewer_pack_path)
    safety = _safety_from_profile(profile)
    output_dir = _output_dir(profile, args.output_dir)

    session_paths = [Path(item).expanduser() for item in args.session]
    if args.session_glob:
        session_paths.extend(Path(item).expanduser() for item in sorted(glob.glob(args.session_glob, recursive=True)))
    if not session_paths:
        session_paths = list(
            iter_recent_session_files(Path(profile.project.codex_home), since_days=args.since_days)
        )
    project_root = _project_root(
        args.project_root,
        profile,
        profile_path=profile_path,
        enforce_default=not args.session,
    )

    parsed = parse_session_files(
        session_paths,
        safety=safety,
        reviewer_pack=reviewer_pack,
        project_root=project_root,
    )
    packets = packets_from_cases(parsed.cases)
    findings = run_reviewers(
        cases=parsed.cases,
        packets=packets,
        enabled=profile.reviewers.enabled,
        reviewer_pack=reviewer_pack,
        min_cases_for_proposal=profile.thresholds.min_cases_for_proposal,
    )
    artifact = build_debug_artifact(
        artifact_id=stable_hash(("debug-artifact", [case.case_id for case in parsed.cases])),
        packets=packets,
        cases=parsed.cases,
        findings=findings,
        parser_warnings=parsed.parser_warnings,
        redaction_warnings=parsed.redaction_warnings,
    )
    report = build_review_report(
        profile_name=profile.project.name,
        cases=parsed.cases,
        packets=packets,
        findings=findings,
        candidates=[],
        safety=safety,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    review_payload = {
        "schema_version": SCHEMA_REVIEW,
        "generated_at": _now(),
        "profile_path": str(profile_path) if profile_path else None,
        "reviewer_pack_path": str(reviewer_pack_path) if reviewer_pack_path else None,
        "session_files": len(session_paths),
        "cases": _dump_models(parsed.cases),
        "packets": _dump_models(packets),
        "findings": _dump_models(findings),
        "parser_warnings": parsed.parser_warnings,
        "redaction_warnings": parsed.redaction_warnings,
    }
    _write_json(output_dir / "review-packets.json", review_payload)
    _write_json(output_dir / "debug-artifact.json", artifact.model_dump(mode="json"))
    (output_dir / "review-report.md").write_text(render_report_markdown(report), encoding="utf-8")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    packet_path = Path(args.packets).expanduser()
    payload = _read_json(packet_path)
    profile = _load_profile_for_extract(args.profile, payload)
    decisions = _read_decisions(args.decisions, packet_path.parent)
    cases = [ReviewCase.model_validate(item) for item in payload.get("cases", [])]
    findings = [ReviewFinding.model_validate(item) for item in payload.get("findings", [])]
    candidates = build_candidates(
        cases=cases,
        findings=findings,
        profile=profile,
        decisions=decisions,
    )
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else packet_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        output_dir / "improvement-candidates.json",
        {
            "schema_version": SCHEMA_CANDIDATES,
            "generated_at": _now(),
            "source_packets_ref": {"file_hash": stable_hash(str(packet_path)), "suffix": packet_path.suffix},
            "candidates": _dump_models(candidates),
        },
    )
    (output_dir / "improvement-candidates.md").write_text(
        render_candidates_markdown(candidates),
        encoding="utf-8",
    )
    return 0


def cmd_list_candidates(args: argparse.Namespace) -> int:
    candidate_path = Path(args.candidates).expanduser()
    payload = _read_json(candidate_path)
    candidates = [ImprovementCandidate.model_validate(item) for item in payload.get("candidates", [])]
    if args.format == "json":
        print(json.dumps(_dump_models(candidates), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(_render_candidate_table(candidates))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    candidate_path = Path(args.candidates).expanduser()
    payload = _read_json(candidate_path)
    candidates = [ImprovementCandidate.model_validate(item) for item in payload.get("candidates", [])]
    decisions_path = _decision_path(args.decisions, candidate_path.parent)
    decisions = _read_decisions(args.decisions, candidate_path.parent)
    candidates, decision = create_decision(
        candidates=candidates,
        candidate_id=args.candidate,
        status=args.decision,
        reason=args.reason,
        reviewer=args.reviewer,
    )
    payload["generated_at"] = _now()
    payload["candidates"] = _dump_models(candidates)
    _write_json(candidate_path, payload)
    decisions.append(decision)
    _write_decisions(decisions_path, decisions)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    candidate_path = Path(args.candidates).expanduser()
    payload = _read_json(candidate_path)
    candidates = [ImprovementCandidate.model_validate(item) for item in payload.get("candidates", [])]
    decisions_path = _decision_path(args.decisions, candidate_path.parent)
    decisions = _read_decisions(str(decisions_path), candidate_path.parent)
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    candidate = by_id.get(args.candidate)
    if candidate is None:
        raise SystemExit(f"Unknown candidate: {args.candidate}")
    if not can_export(candidate, decisions):
        raise SystemExit("Candidate is not staged/promoted with export_allowed=true.")
    decision = latest_decision_by_candidate(decisions)[candidate.candidate_id]
    text = render_debug_runbook_seed(candidate=candidate, decision=decision)
    output_path = (
        Path(args.output).expanduser()
        if args.output
        else candidate_path.parent / "debug-runbook-seeds" / f"{candidate.candidate_id}.yaml"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    return 0


def cmd_subject_init(args: argparse.Namespace) -> int:
    base = subject_dir(args.target, args.subject_id)
    (base / "output").mkdir(parents=True, exist_ok=True)
    (base / "exports").mkdir(parents=True, exist_ok=True)
    _write_text_if_missing(base / "subject.toml", dump_default_subject(args.subject_id))
    _write_text_if_missing(base / "objective.toml", dump_default_objective())
    write_prompts(base, args.subject_id, tool_roots=[], skill_paths=[], mcp_schemas=[])
    return 0


def cmd_subject_scaffold(args: argparse.Namespace) -> int:
    base = subject_dir(args.target, args.subject_id)
    if not (base / "subject.toml").exists():
        (base / "output").mkdir(parents=True, exist_ok=True)
        (base / "exports").mkdir(parents=True, exist_ok=True)
        _write_text_if_missing(base / "subject.toml", dump_default_subject(args.subject_id))
        _write_text_if_missing(base / "objective.toml", dump_default_objective())
    write_prompts(
        base,
        args.subject_id,
        tool_roots=args.tool_root,
        skill_paths=args.skill_path,
        mcp_schemas=args.mcp_schema,
    )
    return 0


def cmd_subject_collect(args: argparse.Namespace) -> int:
    base = subject_dir(args.target, args.subject_id)
    pack = load_subject_pack(base)
    session_paths = _subject_session_paths(args)
    safety = SafetyPolicy(include_raw_text=False, hash_identifiers=True)
    result = collect_subject_episodes(session_paths, pack=pack, safety=safety)
    output_dir = base / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_SUBJECT_EPISODES,
        "generated_at": _now(),
        "subject_id": args.subject_id,
        "session_files": len(session_paths),
        "episodes": _dump_models(result.episodes),
        "parser_warnings": result.parser_warnings,
        "redaction_warnings": result.redaction_warnings,
    }
    _write_json(output_dir / "subject-episodes.json", payload)
    (output_dir / "subject-collect-summary.md").write_text(
        _render_subject_collect_summary(args.subject_id, result.episodes, result.parser_warnings),
        encoding="utf-8",
    )
    _write_json(
        output_dir / "subject-debug-artifact.json",
        {
            "schema_version": "session_review.subject_debug_artifact.v0",
            "subject_id": args.subject_id,
            "episodes": _dump_models(result.episodes),
            "parser_warnings": result.parser_warnings,
            "redaction_warnings": result.redaction_warnings,
        },
    )
    return 0


def cmd_subject_review(args: argparse.Namespace) -> int:
    base = subject_dir(args.target, args.subject_id)
    pack = load_subject_pack(base)
    episode_path = Path(args.episodes).expanduser() if args.episodes else base / "output" / "subject-episodes.json"
    payload = _read_json(episode_path)
    episodes = [SubjectEpisode.model_validate(item) for item in payload.get("episodes", [])]
    findings, candidates, report = review_subject_episodes(episodes, pack=pack)
    output_dir = base / "output"
    _write_json(
        output_dir / "subject-findings.json",
        {
            "schema_version": SCHEMA_SUBJECT_FINDINGS,
            "generated_at": _now(),
            "subject_id": args.subject_id,
            "findings": _dump_models(findings),
        },
    )
    _write_json(
        output_dir / "subject-improvement-candidates.json",
        {
            "schema_version": SCHEMA_SUBJECT_CANDIDATES,
            "generated_at": _now(),
            "subject_id": args.subject_id,
            "candidates": _dump_models(candidates),
        },
    )
    (output_dir / "subject-review-report.md").write_text(report, encoding="utf-8")
    return 0


def cmd_subject_list_candidates(args: argparse.Namespace) -> int:
    base = subject_dir(args.target, args.subject_id)
    path = base / "output" / "subject-improvement-candidates.json"
    payload = _read_json(path)
    candidates = [SubjectImprovementCandidate.model_validate(item) for item in payload.get("candidates", [])]
    if args.format == "json":
        print(json.dumps(_dump_models(candidates), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(render_subject_candidates_table(candidates))
    return 0


def cmd_subject_export(args: argparse.Namespace) -> int:
    base = subject_dir(args.target, args.subject_id)
    payload = _read_json(base / "output" / "subject-improvement-candidates.json")
    candidates = [SubjectImprovementCandidate.model_validate(item) for item in payload.get("candidates", [])]
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    candidate = by_id.get(args.candidate)
    if candidate is None:
        raise SystemExit(f"Unknown subject candidate: {args.candidate}")
    output = Path(args.output).expanduser() if args.output else base / "exports" / f"{candidate.candidate_id}.yaml"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_subject_candidate_export(candidate), encoding="utf-8")
    return 0


def _resolve_reviewer_pack_path(raw: str | None, profile_path: Path | None) -> Path | None:
    if raw:
        return Path(raw).expanduser()
    sibling = (profile_path.parent if profile_path else Path.cwd()) / "reviewer-pack.toml"
    return sibling if sibling.exists() else None


def _safety_from_profile(profile: ReviewProfile) -> SafetyPolicy:
    return SafetyPolicy(
        include_raw_text=profile.safety.include_raw_text,
        hash_identifiers=profile.safety.hash_identifiers,
        default_ttl_days=profile.safety.default_ttl_days,
    )


def _output_dir(profile: ReviewProfile, override: str | None) -> Path:
    raw = override or profile.project.output_dir
    return Path(raw).expanduser()


def _project_root(
    raw: str | None,
    profile: ReviewProfile,
    *,
    profile_path: Path | None,
    enforce_default: bool,
) -> Path | None:
    if raw:
        return Path(raw).expanduser()
    if profile.project.root:
        root = Path(profile.project.root).expanduser()
        if root.is_absolute() or profile_path is None:
            return root
        base = profile_path.parent.parent if profile_path.parent.name == ".session-review" else profile_path.parent
        return base / root
    return Path.cwd() if enforce_default else None


def _subject_session_paths(args: argparse.Namespace) -> list[Path]:
    paths = [Path(item).expanduser() for item in args.session]
    if args.session_glob:
        paths.extend(Path(item).expanduser() for item in sorted(glob.glob(args.session_glob, recursive=True)))
    if paths:
        return paths
    if not args.all_projects:
        raise SystemExit("subject collect requires --session/--session-glob or explicit --all-projects")
    codex_home = Path("~/.codex").expanduser()
    cutoff = datetime.now(timezone.utc).timestamp() - args.since_hours * 3600
    sessions = codex_home / "sessions"
    if not sessions.exists():
        return []
    return [path for path in sorted(sessions.glob("**/*.jsonl")) if path.stat().st_mtime >= cutoff]


def _render_subject_collect_summary(
    subject_id: str,
    episodes: list[SubjectEpisode],
    warnings: list[str],
) -> str:
    lines = [
        "# Subject Collect Summary",
        "",
        f"- subject: `{subject_id}`",
        f"- episodes: {len(episodes)}",
        f"- parser_warnings: {len(warnings)}",
        "",
    ]
    if episodes:
        lines.append("## Episodes")
        for episode in episodes:
            lines.append(
                f"- `{episode.episode_id}` score={episode.relevance_score} "
                f"signals={','.join(episode.matched_signals)} outcome={episode.outcome_hint}"
            )
    return "\n".join(lines) + "\n"


def _load_profile_for_extract(raw: str | None, payload: dict[str, Any]) -> ReviewProfile:
    profile_path = raw or payload.get("profile_path")
    if profile_path and Path(str(profile_path)).expanduser().exists():
        return load_review_profile(profile_path)
    return ReviewProfile()


def _read_decisions(raw: str | None, output_dir: Path) -> list[PromotionDecision]:
    path = _decision_path(raw, output_dir)
    if not path.exists():
        return []
    payload = _read_json(path)
    return [PromotionDecision.model_validate(item) for item in payload.get("decisions", [])]


def _write_decisions(path: Path, decisions: list[PromotionDecision]) -> None:
    _write_json(
        path,
        {
            "schema_version": SCHEMA_DECISIONS,
            "generated_at": _now(),
            "decisions": _dump_models(decisions),
        },
    )


def _decision_path(raw: str | None, output_dir: Path) -> Path:
    return Path(raw).expanduser() if raw else output_dir / "promotion-decisions.json"


def _dump_models(items: list[Any]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in items]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_text_if_missing(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(text, encoding="utf-8")


def _render_candidate_table(candidates: list[ImprovementCandidate]) -> str:
    headers = [
        "candidate_id",
        "target_type",
        "maturity",
        "export_allowed",
        "score",
        "support",
        "problem_pattern",
    ]
    rows = [
        [
            candidate.candidate_id,
            str(candidate.target_type),
            str(candidate.maturity),
            str(candidate.export_allowed).lower(),
            ",".join(f"{key}={value}" for key, value in sorted(candidate.score.items())),
            str(len(candidate.supporting_findings)),
            candidate.problem_pattern,
        ]
        for candidate in candidates
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows)) if rows else len(headers[index])
        for index in range(len(headers))
    ]
    lines = ["  ".join(headers[index].ljust(widths[index]) for index in range(len(headers)))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(row[index].ljust(widths[index]) for index in range(len(headers)))
        for row in rows
    )
    return "\n".join(lines)


def _default_profile_toml() -> str:
    return """[project]
name = "session-review"
root = "."
codex_home = "~/.codex"
output_dir = ".session-review/output"

[safety]
include_raw_text = false
hash_identifiers = true
max_export_sensitivity = "S1"
default_ttl_days = 30

[reviewers]
enabled = ["mcp_efficacy", "skill_utility", "subagent_value", "shell_fallback", "path_stability"]

[thresholds]
min_cases_for_proposal = 3
min_cases_for_staged = 6
max_secret_leak_count = 0

[promotion]
targets = ["mcp_tool", "skill", "subagent_pattern", "debug_runbook_seed"]
"""


def _default_reviewer_pack_toml() -> str:
    return """[pack]
id = "default"
version = "0.1.0"

[tool_families]
mcp = []
shell = ["exec_command", "bash"]
subagent = ["spawn_agent", "wait_agent", "close_agent"]
skill = []

[task_families.example]
trigger_keywords = []
preferred_route = "unknown"
expected_data = ["evidence_ref"]

[reviewers.path_stability]
group_by = ["task_family", "recommended_path"]
min_consistency_ratio = 0.7
"""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
