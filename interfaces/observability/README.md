# Forge observability documentation

Forge observability defines application-level publish/receive timing for generic
producers and consumers. It uses optional Dora event metadata without changing
Arrow payload schemas or introducing a new package family.

The metadata contract is v1 and is not yet released. Python and Rust helpers
belong to `forge-common`; their current capabilities and restrictions are
documented in their package guides.

## Language support and examples

| Library | Observability status | Usage and example |
| --- | --- | --- |
| Python `forge-common` | Metadata parsing, publishing, receive contexts, and bounded metrics implemented. | [Python guide](../../packages/common/README.md#observability-quickstart) and [runnable example](../../packages/common/examples/publish_receive.py). |
| Rust `forgelab_common` / `forge_common` | Transport-independent parser, two-phase publishing, receive contexts, and bounded metrics implemented. | [Rust guide](../../crates/forge_common/README.md#observability-model) and [runnable example](../../crates/forge_common/examples/publish_receive.rs). |
| C++ `forge_common` | Logging only; observability pending. | [C++ capability status](../../cpp/forge_common/README.md#capability-status); no observability example is provided for an API that does not exist. |

The runnable examples use dependency-free in-memory transports. They demonstrate
the library contracts, not completed Dora integration. A concrete application
must still adapt its typed Dora metadata and place calls at the protocol's exact
publish/receive boundaries.

## Read this first

| Document | Audience | Purpose |
| --- | --- | --- |
| [Metadata protocol](PROTOCOL.md) | Node and cross-language implementers | Normative measurement boundaries, fields, validation, provenance, clock, and compatibility rules. |
| [Python package guide](../../packages/common/README.md) | Python node authors | Helper usage, public API, local metrics, snapshots, and implementation restrictions. |
| [Rust crate guide](../../crates/forge_common/README.md) | Rust node authors | Transport-independent parser, two-phase publication API, local metrics, and adapter responsibilities. |

## Source of truth

Each fact has one primary owner. Other documents summarize and link instead of
copying the contract or maintaining competing API examples.

| Question | Primary source |
| --- | --- |
| What do publish, receive, origin, and each interval mean? | [`PROTOCOL.md`](PROTOCOL.md) |
| Which metadata values and propagation behaviors are valid? | [`PROTOCOL.md`](PROTOCOL.md) |
| How do I use the language helpers and metrics sinks? | [Language support and examples](#language-support-and-examples) plus the Python and Rust package guides. |
| What is implemented, and what still needs validation? | [Language support](#language-support-and-examples), package capability sections, and implementation tests. |
| How does runtime timing differ from message sample time? | [Message transport conventions](../forge_msgs/SCHEMA.md#transport-contract) and the [protocol](PROTOCOL.md#2-measurement-boundaries) |
| What changed in a release? | [`CHANGELOG.md`](../../CHANGELOG.md) |

## Contract versus implementation

The protocol is independent of business node roles and monitoring backends. A
node may both receive inputs and publish outputs; it explicitly associates data
with its parent context when provenance is known.

A protocol definition or a passing parser test does not establish real transport,
cross-language, or application coverage. The package guides document current
validation limits. No documentation in this directory implies automatic
instrumentation of existing dataflows.
