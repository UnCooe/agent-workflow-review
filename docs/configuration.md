# Configuration

## review-profile.toml

`review-profile.toml` controls project scope, output paths, safety defaults,
enabled reviewers, and promotion thresholds.

```toml
[project]
name = "example-project"
root = "."
codex_home = "~/.codex"
output_dir = ".session-review/output"

[safety]
include_raw_text = false
hash_identifiers = true
max_export_sensitivity = "S1"
default_ttl_days = 30
```

`project.root` is used to filter Codex sessions by cwd/path signal. When
auto-discovering sessions, the CLI defaults to the current working directory if
no project root is configured.

## reviewer-pack.toml

`reviewer-pack.toml` describes project-specific tool families and task
families.

```toml
[tool_families]
mcp = ["inspect_request", "get_trace"]
shell = ["exec_command", "bash"]
subagent = ["spawn_agent", "wait_agent", "close_agent"]

[task_families.provider_verification]
trigger_keywords = ["provider", "vendor"]
preferred_route = "mcp_first"
expected_data = ["provider", "evidence_ref"]
```

This file is the main project-specific customization surface in v0.1. It is
configuration, not a DSL.

## subject signal-pack.toml

Subject review uses subject-local configuration under:

```text
.session-review/
  subjects/
    <subject_id>/
      subject.toml
      objective.toml
      signal-pack.toml
```

`signal-pack.toml` is the v0.2 customization surface for cross-project subjects.
It should be generated or edited for one subject, not reused as a global keyword
catalog. Proposed packs do not affect collection or discrimination by default;
mark reviewed packs as `reviewed`/`active`, or pass
`--include-proposed-signal-pack` for a local experiment.

```toml
[pack]
id = "server-ssh-signals"
version = "0.2.0"
generated_by = "codex"
status = "proposed"
source_refs = ["redacted-fixture"]

[positive_signals]
commands = ["ssh ops@"]
text = ["server host", "journalctl", "systemctl"]
error_signals = ["timeout", "connection reset"]

[domain_anchors]
required_any = ["ops@", "server host", "journalctl", "systemctl"]
required_all = []

[negative_signals]
exclude_contexts = ["git@github.com", "git remote", "ssh -T"]
commands = ["git push", "git fetch", "ssh -T git@"]
text = ["GitHub", "repository remote"]

[ambiguous_terms]
terms = ["ssh", "timeout", "permission denied"]
require_domain_anchor = true
```

Use `session-review subject validate-signals <subject_id>` to inspect a signal
pack. Use `session-review subject discriminate <subject_id>` to produce
attribution hints and collision notes before reviewing with
`session-review subject review <subject_id> --with-collision`.
