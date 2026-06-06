# Subject Discrimination v0.2

Subject review can scan sessions across projects. That makes recall useful, but
also makes false attribution easier: generic terms such as `ssh`, `proxy`, or
`timeout` can belong to multiple domains.

v0.2 adds a cautious attribution layer. It does not replace human review and it
does not prove root cause.

## Signal Packs

`signal-pack.toml` is a subject-local, auditable proposal. It should be generated
or edited by an agent that has read the specific tool, skill, MCP schema, README,
CLI help, and examples for that subject.

The framework does not ship a universal keyword catalog. A signal pack should
stay small and include:

- positive signals: direct commands, tool names, skill names, MCP names, and
  subject-specific text signals;
- domain anchors: evidence required before an episode can be attributed to the
  subject;
- negative signals: similar contexts that should be excluded;
- ambiguous terms: generic words that require a domain anchor.

Generated packs should start as `status = "proposed"` and be reviewed before
being used for repeated automation. Proposed packs are validation artifacts; they
do not affect collection or discrimination unless a command explicitly passes
`--include-proposed-signal-pack`. Routine automation should use a pack marked
`reviewed` or `active`.

## Discrimination

`session-review subject discriminate` reads collected episodes and writes:

- `subject-discriminated-episodes.json`
- `subject-attribution-hints.json`
- `subject-collisions.json`
- `subject-discrimination-report.md`

Each attribution hint has a status:

- `confirmed`: direct usage and domain anchor support the subject.
- `likely`: evidence supports the subject but is not complete.
- `ambiguous`: generic or incomplete evidence; keep review-only.
- `rejected`: negative signals indicate a competing domain.
- `unknown`: insufficient evidence.

Ambiguous, rejected, and unknown episodes stay review-only.

## Collision

Reviewer collision is not voting. It preserves disagreement between the collector
and the domain discriminator.

For example, a collector may recall an episode because it contains `ssh`, while
the discriminator rejects it because the same episode contains Git remote
signals. The report must keep that conflict visible instead of converting it
into a stronger conclusion.

## Git SSH vs Server SSH

The bundled `examples/subject-review/server-ssh/` corpus demonstrates the target
behavior:

- Git SSH failures should be rejected or kept review-only for a server SSH
  subject.
- Server SSH commands with server-domain anchors may be likely or confirmed.
- Mixed sessions may produce collision notes instead of a single attribution.

## Safety Boundary

Discrimination uses redacted episode summaries and signal ids. It should not
persist raw prompts, raw tool output, raw paths, usernames, hosts, repository
URLs, or secrets.

Attribution hints are evidence summaries, not root-cause statements. Do not use
them to automatically modify tools, skills, MCP schemas, subagent workflows, or
runbooks.
