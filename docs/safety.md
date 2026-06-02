# Safety

## Defaults

v0.1 defaults to `include_raw_text=false`. With this setting:

- user messages are stored as text hash and character count;
- assistant messages are stored as text hash and character count;
- S2 text fields are represented as hash/char count summaries;
- raw source material is represented through `RawRef`;
- reports and candidates should not include raw prompt snippets.

## RawRef

`RawRef` is an opaque source reference. It stores source type, source hash,
safe locator fields, sensitivity, TTL, and access policy. It does not store raw
session text.

## Project Filtering

When a project root is known, sessions are included only if the session cwd or
session file path matches that root. If ownership cannot be confirmed, the
parser records a warning and skips the session.

## Manual Promotion

The tool can generate candidates automatically, but export requires a manual
`decide` step. A single session should not be treated as proof of best practice.
