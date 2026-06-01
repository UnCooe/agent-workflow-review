from __future__ import annotations

import argparse
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


SCHEMA_REVIEW = "session_review.review.v0"
SCHEMA_CANDIDATES = "session_review.candidates.v0"
SCHEMA_DECISIONS = "session_review.decisions.v0"


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def main() -> None:
    raise SystemExit(run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="session-review")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser("review", help="Parse Codex JSONL and produce packets/report/artifact.")
    review.add_argument("--profile", default=None)
    review.add_argument("--reviewer-pack", default=None)
    review.add_argument("--since-days", type=int, default=7)
    review.add_argument("--session", action="append", default=[], help="Explicit Codex session JSONL path.")
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

    export = sub.add_parser("export", help="Export a manually staged/promoted debug_runbook_seed.")
    export.add_argument("--candidates", default=".codex-local/session-review/improvement-candidates.json")
    export.add_argument("--decisions", default=None)
    export.add_argument("--candidate", required=True)
    export.add_argument("--target", required=True, choices=["debug_runbook_seed"])
    export.add_argument("--output", default=None)
    export.set_defaults(func=cmd_export)
    return parser


def cmd_review(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile).expanduser() if args.profile else None
    profile = load_review_profile(profile_path)
    reviewer_pack_path = _resolve_reviewer_pack_path(args.reviewer_pack, profile_path)
    reviewer_pack = load_reviewer_pack(reviewer_pack_path)
    safety = _safety_from_profile(profile)
    output_dir = _output_dir(profile, args.output_dir)

    session_paths = [Path(item).expanduser() for item in args.session]
    if not session_paths:
        session_paths = list(
            iter_recent_session_files(Path(profile.project.codex_home), since_days=args.since_days)
        )

    parsed = parse_session_files(session_paths, safety=safety, reviewer_pack=reviewer_pack)
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
            "source_packets": str(packet_path),
            "candidates": _dump_models(candidates),
        },
    )
    (output_dir / "improvement-candidates.md").write_text(
        render_candidates_markdown(candidates),
        encoding="utf-8",
    )
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


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
