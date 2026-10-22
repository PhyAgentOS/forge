# Contributing to Forge

Thank you for your interest in contributing to Forge. `master` carries the current
Forge 2.x line, while `stable/1.x` preserves the Dora 0.4-compatible Forge 1.x line
for critical fixes. Focused changes, compatibility analysis, and cross-language
tests are especially important.

## Development Setup

Install the Python workspace:

```bash
uv sync --all-packages --all-extras --dev
```

Build the Rust workspace:

```bash
cargo build --workspace
```

## Checks

Before opening a pull request, run:

```bash
python scripts/check_release_versions.py
uv run ruff check .
uv run python scripts/check_forge_msgs_schema.py
uv run pytest packages/msgs/tests packages/policy/tests packages/robot/tests packages/kinematics/tests packages/tool/tests
cargo test --workspace --locked
```

If you change the Python workspace dependencies, update `uv.lock`:

```bash
uv lock
```

If you change Rust dependencies, update the root `Cargo.lock`:

```bash
cargo update
```

## Message Schema Changes

`interfaces/forge_msgs/forge_msgs.v1.yaml` is the manifest for the canonical
Forge message schemas. Message definitions live in the versioned domain files
referenced by that manifest. When changing message fields or semantics:

1. Update the appropriate domain schema and `interfaces/forge_msgs/SCHEMA.md`.
2. Update both Python and Rust implementations when applicable.
3. Add or update round-trip tests.
4. Run `uv run python scripts/check_forge_msgs_schema.py`.
5. Document compatibility implications in the pull request.

If a message is intentionally unavailable in one language, declare its current
`implementations` in the domain schema rather than implying cross-language
support.

## Pull Request Guidelines

- Keep pull requests focused on one behavior or package area.
- Include tests for behavior changes and bug fixes.
- Avoid committing generated build outputs, virtual environments, or local
  machine configuration.
- Do not include secrets, private tokens, private URLs, or hardware credentials.
- For robot hardware integrations, keep vendor SDKs and large binary artifacts
  out of this core repository unless explicitly discussed.

## Reporting Bugs

When filing an issue, include:

- The package or crate involved.
- Python, Rust, uv, and Dora versions where relevant.
- A minimal reproduction or failing test when possible.
- Expected behavior and actual behavior.

## License

By contributing, you agree that your contributions are licensed under the Apache
License, Version 2.0.

## Node README contract

When creating or changing a downstream Adapter/Operator Node repository, maintain a
capability summary in its root README. This is a documentation requirement; it does
not require a data-plane Node to implement ToolEndpoint or HTTP.

Count independent production Dora Node entrypoints, not aliases, CLI subcommands,
classes, packages, test sinks, or fake plants. List debug/example Nodes separately.
For each supported mode, count concrete Endpoint IDs and their operations separately.
ToolSpec aliases and readiness are deployment concerns: never infer the number of
caller-visible Tools from the number of Nodes, Endpoints, or operations.

Required fields:

| Field | Required content |
| --- | --- |
| Production Nodes | Count, names, source/CLI and packaged entrypoints; aliases and optional modes |
| Auxiliary programs | Test/fake/example Nodes and offline CLI utilities, excluded from production count |
| Tool support | Data-plane only, provider, caller, Gateway, or an explicit combination; enabling configuration |
| Endpoints | Count and exact `endpoint_id`, provider Node, and descriptor source link; write `0` when absent |
| Operations | Per-Endpoint count and exact operation names; Query/Action/Session, concurrency, lifecycle controls |
| Direct HTTP Tool API | Method/path, body/result contract, errors and units, or `0 (via Gateway)`; unrelated APIs listed separately |
| Tool bindings | `tool_id -> endpoint_id / operation -> provider Node`; distinguish deployed examples from fixed contracts |
| Compatibility | Wire/schema versions, dependency locks, Gateway/Node build revisions and unsupported combinations |
| Source and examples | Descriptor/handler, dataflow, config, request/result schema, build instructions and release provenance |

Start with this Markdown template and replace every placeholder:

```markdown
## Node, Endpoint and Tool API

| Item | Implementation |
| --- | --- |
| Production Nodes | <count; names; source/CLI and packaged entrypoints> |
| Auxiliary programs | <debug Nodes, aliases and offline CLI utilities> |
| Tool support | <data-plane only / provider / caller / Gateway; activation mode> |
| Endpoints | <count; endpoint_id; provider Node; descriptor link> |
| Operations | <per-Endpoint count; operation; semantics; concurrency and controls> |
| Direct HTTP Tool API | <count and method/path table, or 0; Gateway owns external API> |
| Gateway Tools | <deployment-configured count; exact example config or no fixed Tool ID> |

<tool_id -> endpoint_id / operation -> provider Node mapping>
<request/result contract, units/frames, errors, config and source links>
<compatibility and build/release provenance>
```

A data-plane-only controller may consume Action-shaped Dora goal/feedback/result
messages without implementing a ToolEndpoint. A provider can expose several
operations at one Endpoint; a Gateway routes providers without becoming their
business Endpoint. Optional provider modes must document the non-provider default.
Session uses stop and Action uses cancel; accepted control is not a terminal result.

Before review, compare the README against package scripts, build outputs, descriptor,
handler registration, Gateway ToolSpec and dataflow. Update the summary in the same
change as any interface or capability change. Do not invent an HTTP URL for a Dora
provider, advertise a directory absent from the tree, or infer released-binary
capability from current source. Keep examples generic and exclude local machine
configuration, credentials, runtime records and private deployment details.
