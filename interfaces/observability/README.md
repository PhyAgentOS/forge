# Forge observability documentation

Forge observability defines application-level publish/receive timing for generic
producers and consumers. It uses optional Dora event metadata without changing
Arrow payload schemas or introducing a new package family.

The metadata contract is v1 and is not yet released. Python helpers belong to
`forge-common`; their current capabilities and restrictions are documented in the
package guide.

## Read this first

| Document | Audience | Purpose |
| --- | --- | --- |
| [Metadata protocol](PROTOCOL.md) | Node and cross-language implementers | Normative measurement boundaries, fields, validation, provenance, clock, and compatibility rules. |
| [Python package guide](../../packages/common/README.md) | Python node authors | Helper usage, public API, local metrics, snapshots, and implementation restrictions. |


## Source of truth

Each fact has one primary owner. Other documents summarize and link instead of
copying the contract or maintaining competing API examples.

| Question | Primary source |
| --- | --- |
| What do publish, receive, origin, and each interval mean? | [`PROTOCOL.md`](PROTOCOL.md) |
| Which metadata values and propagation behaviors are valid? | [`PROTOCOL.md`](PROTOCOL.md) |
| How do I use the Python helpers and metrics sink? | [`packages/common/README.md`](../../packages/common/README.md) and its [tests](../../packages/common/tests) |
| What is implemented, and what still needs validation? | [Package capabilities](../../packages/common/README.md#capability-status) and [tests](../../packages/common/tests) |
| How does runtime timing differ from message sample time? | [Message transport conventions](../forge_msgs/SCHEMA.md#transport-contract) and the [protocol](PROTOCOL.md#2-measurement-boundaries) |
| What changed in a release? | [`CHANGELOG.md`](../../CHANGELOG.md) |

## Contract versus implementation

The protocol is independent of business node roles and monitoring backends. A
node may both receive inputs and publish outputs; it explicitly associates data
with its parent context when provenance is known.

A protocol definition or a passing parser test does not establish real transport,
cross-language, or application coverage. The package guide documents current
validation limits. No documentation in this directory implies automatic
instrumentation of existing dataflows.
