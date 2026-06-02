# Concepts

## ReviewCase

A `ReviewCase` is a bounded slice of one agent session. v0.1 primarily splits
cases by user turn. One session can produce multiple cases.

## ReviewPacket

A `ReviewPacket` is the low-noise input for reviewers. It summarizes route
quality, evidence quality, efficiency, feedback signals, and safety status.

## ReviewFinding

A `ReviewFinding` is a deterministic reviewer conclusion. Built-in reviewers
cover MCP efficacy, skill utility, subagent value, shell fallback, and path
stability.

## ImprovementCandidate

An `ImprovementCandidate` is a human-reviewable proposal. It can target MCP
tools, skills, subagent patterns, or debug-runbook seeds.

## PromotionDecision

A `PromotionDecision` records human review. Candidates must be manually staged
or promoted before export.

## Candidate Lifecycle

- `observed`: automatically observed from evidence.
- `proposal`: enough cases support a candidate.
- `reviewed`: a human confirmed the problem is real.
- `staged`: a human assigned owner/eval expectations; export is allowed.
- `promoted`: merged or adopted in the target system.
- `deprecated`: later evidence shows the candidate is stale, misleading, or no
  longer useful.
