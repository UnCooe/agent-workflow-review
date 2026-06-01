from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .adapters.codex_jsonl import case_max_sensitivity
from .models import (
    AgentEventKind,
    DebugArtifact,
    RawRef,
    ReviewCase,
    ReviewPacket,
    ReviewReport,
    SensitivityLevel,
    SourceType,
    ToolFamily,
)
from .safety import SafetyPolicy, stable_hash


def packets_from_cases(cases: list[ReviewCase]) -> list[ReviewPacket]:
    return [packet_from_case(case) for case in cases]


def packet_from_case(case: ReviewCase) -> ReviewPacket:
    families = [event.tool_family for event in case.events if event.tool_name]
    mcp_results = [
        event
        for event in case.events
        if event.kind == AgentEventKind.TOOL_RESULT and event.tool_family == ToolFamily.MCP
    ]
    total_output = sum(int(event.output_summary.get("char_count") or 0) for event in case.events)
    feedback = feedback_signals(case)
    return ReviewPacket(
        packet_id=stable_hash(("packet", case.case_id)),
        case_id=case.case_id,
        task_family=case.task_family,
        expected_data=_expected_data(case),
        observed_path=case.tool_path,
        route_quality={
            "expected_route": case.expected_route,
            "actual_route": case.actual_route,
            "wrong_track_rounds": wrong_track_rounds(case),
            "source_pivot_quality": source_pivot_quality(case),
        },
        evidence_quality={
            "conclusion_reached": case.outcome == "resolved",
            "unsupported_claim_risk": unsupported_claim_risk(case),
            "mcp_result_count": len(mcp_results),
        },
        efficiency={
            "tool_calls": len([event for event in case.events if event.tool_name]),
            "output_chars_bucket": output_bucket(total_output),
            "extra_cost": max(0, len(case.tool_path) - 1),
        },
        feedback_signals=feedback,
        safety_summary={
            "max_sensitivity": case_max_sensitivity(case),
            "redaction_status": "pass" if case_max_sensitivity(case) != SensitivityLevel.S3 else "fail",
        },
    )


def build_debug_artifact(
    *,
    artifact_id: str,
    packets: list[ReviewPacket],
    cases: list[ReviewCase],
    findings: list[Any],
    parser_warnings: list[str],
    redaction_warnings: list[str],
) -> DebugArtifact:
    return DebugArtifact(
        artifact_id=artifact_id,
        packets=packets,
        cases=cases,
        findings=findings,
        parser_warnings=parser_warnings,
        redaction_warnings=redaction_warnings,
    )


def build_review_report(
    *,
    profile_name: str,
    cases: list[ReviewCase],
    packets: list[ReviewPacket],
    findings: list[Any],
    candidates: list[Any],
    safety: SafetyPolicy,
) -> ReviewReport:
    now = datetime.now(timezone.utc)
    report_id = stable_hash(("report", profile_name, now.isoformat()))
    ref = safety.raw_ref(
        source_type=SourceType.REPORT,
        source_id=report_id,
        locator={"record_index": 0},
        sensitivity=SensitivityLevel.S0,
    )
    return ReviewReport(
        report_id=report_id,
        window={"generated_at": now.isoformat().replace("+00:00", "Z")},
        profile=profile_name,
        summary=render_summary(cases, findings, candidates),
        metrics={
            "case_count": len(cases),
            "packet_count": len(packets),
            "finding_count": len(findings),
            "candidate_count": len(candidates),
            "task_families": dict(Counter(case.task_family for case in cases)),
        },
        top_findings=findings[:10],
        candidates=candidates[:10],
        safety={
            "redaction_failures": sum(
                1 for packet in packets if packet.safety_summary.get("redaction_status") == "fail"
            ),
            "restricted_refs": sum(
                1
                for case in cases
                for ref_item in case.raw_refs
                if str(ref_item.sensitivity) in {"S2", "S3"}
            ),
        },
        debug_artifact_ref=ref,
    )


def render_report_markdown(report: ReviewReport) -> str:
    lines = [
        "# Session Review Report",
        "",
        f"- profile: `{report.profile}`",
        f"- cases: {report.metrics.get('case_count', 0)}",
        f"- findings: {report.metrics.get('finding_count', 0)}",
        f"- candidates: {report.metrics.get('candidate_count', 0)}",
        f"- redaction_failures: {report.safety.get('redaction_failures', 0)}",
        "",
        "## Summary",
        report.summary,
        "",
        "## Top Findings",
    ]
    if not report.top_findings:
        lines.append("- No findings.")
    for finding in report.top_findings[:10]:
        lines.append(
            f"- `{finding.type}` {finding.verdict} ({finding.confidence}): {finding.rationale}"
        )
    lines.extend(["", "## Candidates"])
    if not report.candidates:
        lines.append("- No candidates.")
    for candidate in report.candidates[:10]:
        lines.append(
            f"- `{candidate.candidate_id}` {candidate.target_type}/{candidate.maturity}: "
            f"{candidate.problem_pattern}"
        )
    return "\n".join(lines) + "\n"


def render_summary(cases: list[ReviewCase], findings: list[Any], candidates: list[Any]) -> str:
    if not cases:
        return "No review cases were found in the selected window."
    verdicts = Counter(str(getattr(finding, "verdict", "unknown")) for finding in findings)
    return (
        f"Reviewed {len(cases)} cases, found {len(findings)} findings and "
        f"{len(candidates)} candidates. Verdicts: {dict(verdicts)}."
    )


def feedback_signals(case: ReviewCase) -> list[str]:
    signals: list[str] = []
    if "mcp_empty_result" in case.recovery_path:
        signals.append("query_returned_empty")
    if "shell_fallback_after_mcp" in case.recovery_path:
        signals.append("had_to_switch_to_shell")
    if len(case.tool_path) >= 4:
        signals.append("repeated_tool_calls")
    if case.outcome == "unresolved":
        signals.append("conclusion_not_reached")
    return signals


def wrong_track_rounds(case: ReviewCase) -> int:
    if case.expected_route == "mcp_first":
        return 0 if case.actual_route in {"mcp_first", "mixed"} else len(case.tool_path)
    if case.expected_route == "source_first":
        return 0 if case.actual_route == "source_first" else len(case.tool_path)
    return 0


def source_pivot_quality(case: ReviewCase) -> str:
    if "shell_fallback_after_mcp" in case.recovery_path:
        return "late"
    if case.actual_route == "source_first":
        return "started_source_first"
    return "not_needed"


def unsupported_claim_risk(case: ReviewCase) -> str:
    if case.outcome == "resolved" and not any(event.tool_name for event in case.events):
        return "high"
    if case.outcome == "resolved" and "mcp_empty_result" in case.recovery_path:
        return "medium"
    return "low"


def output_bucket(chars: int) -> str:
    if chars >= 40_000:
        return "severe"
    if chars >= 20_000:
        return "warning"
    if chars > 0:
        return "normal"
    return "none"


def _expected_data(case: ReviewCase) -> list[str]:
    return case.expected_data or ["evidence_ref"]
