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
