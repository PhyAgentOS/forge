# forgelab_common

`forgelab_common` provides lightweight Rust utilities shared by Forge crates:

- tracing initialization;
- optional application-level publish/receive observability; and
- bounded local latency histograms and diagnostic counters.

The crates.io distribution is named `forgelab_common`; its Rust library target
remains `forge_common` so imports retain the family name. The observability core
does not depend on Dora, Arrow, OpenTelemetry, or an exporter and starts no
threads or network activity.

Related documentation:

- [Observability documentation index](../../interfaces/observability/README.md)
- [Metadata protocol](../../interfaces/observability/PROTOCOL.md)

## Capability status

The Rust observability APIs are implemented in the source tree and are not yet
released. Transport adapters and applications still own typed metadata conversion,
Dora calls, parent selection, snapshot scheduling, and export. Real Dora typed
metadata conformance and application instrumentation remain pending.

## Tracing usage

```rust
use forge_common::logger::init_tracing;

fn main() {
    init_tracing("my-node");
}
```

`init_tracing` initializes global tracing once and uses `RUST_LOG` for log-level
filtering when it is set.

## Observability model

The complete example at
[`examples/publish_receive.rs`](examples/publish_receive.rs) provides a concrete
transport parameter enum, metadata lookup adapter, publication-field application,
in-memory send, receive, derived publish, and snapshot output. Run it from the
repository root:

```bash
cargo run -p forgelab_common --example publish_receive --locked
```

Use one `Observer` per local receive-to-publish timing domain. Call
`observe_receive` immediately after Dora yields an INPUT, before filtering,
decoding, or queuing, and retain the returned `ReceiveContext` with the actual
data.

A transport adapter classifies the top-level metadata with `MetadataContainer`
and maps only supported typed values to `MetadataValueRef`:

```rust
use forge_common::observability::{
    MetadataContainer, MetadataValueRef, Observer,
};

let context = observer.observe_receive(
    "image",
    MetadataContainer::Mapping,
    |key| parameter_to_metadata_ref(parameters.get(key)),
);
```

Here `parameter_to_metadata_ref` is application adapter code: native int64 values
become `MetadataValueRef::Integer`, UTF-8 strings become `String`, present values
of every other type become `Other`, and absent keys become `None`. Pass `Missing`
for absent metadata and `Invalid` for a non-mapping dynamic container. Unknown
versions are opaque: the parser reads only `forge_obs_version` and does not inspect
future fields.

### Publishing

Publishing is a two-phase API because the common crate does not own Dora. Prepare
after payload construction, apply the returned fields to the exact outgoing
metadata mapping, call the transport exactly once, then pass that same result to
`finish_publication`:

```rust
use forge_common::observability::{PublicationMode, Observer};

let prepared = observer.prepare_publication(
    "out",
    PublicationMode::Derived { parent: &context },
    outgoing_observation.as_ref(),
)?;
apply_publication_fields(&mut outgoing_parameters, prepared.fields());
let result = node.send_output("out".into(), outgoing_parameters, payload);
observer.finish_publication(prepared, result)?;
```

`apply_publication_fields` is transport adapter code and must implement all three
variants exactly:

- `PreserveOpaque`: leave Forge observability keys unchanged;
- `Clear`: remove every key in `OBSERVATION_KEYS`;
- `V1(fields)`: remove every known key, insert `OBSERVATION_VERSION`, insert the
  publish time, and insert optional origin fields from `fields`.

The `outgoing_observation` argument must be parsed from the same outgoing mapping
that `apply_publication_fields` mutates. This correspondence is required to keep
unknown-version metadata opaque. The common core cannot enforce mapping copies or
Dora's Python-only flattened `timestamp` handling.

Publication modes are role-independent:

| Mode | Behavior |
| --- | --- |
| `PublicationMode::NewOrigin` | Starts an explicitly selected path; origin and publish use the same sample. |
| `PublicationMode::Derived` | Refreshes publish time and inherits only the explicit parent's valid origin. |
| `PublicationMode::Unlinked` | Publishes hop timing without inventing an origin. |

For a USB camera image output, prepare the complete image payload first and use
`NewOrigin`; do not inherit timing from a tick input. A device
`capture_timestamp_ns` remains separate sample-time data and must not replace
`forge_origin_time_ns`.

A failed send is returned unchanged and counted only as `PublishFailed`; it is not
retried or recorded as a successful publish or turnaround.

## Local metrics

`BoundedMetricsSink` is a thread-safe, std-only aggregate sink. It records exact
integer nanoseconds for:

- `forge_hop_latency_seconds`;
- `forge_e2e_latency_seconds`; and
- `forge_node_turnaround_seconds`.

Metric exporters obtain names through `LatencyMetric::as_str()` and
`EVENT_METRIC_NAME`. Snapshots intentionally retain nanoseconds (`sum_ns`,
`min_ns`, `max_ns`, and `le_ns`) so adapters can convert to seconds without losing
bucket precision.

Histograms and counters share a configurable series limit. Labels are bounded by
UTF-8 bytes and must describe configured input/output ports, never origin,
message, or trace IDs. Counts saturate at `i64::MAX`; invalid inputs, rejected new
series, and saturated updates have separate unlabelled diagnostics.

```rust
use std::sync::Arc;

use forge_common::metrics::{BoundedMetricsSink, MetricsConfig};
use forge_common::observability::Observer;

let sink = Arc::new(BoundedMetricsSink::new(MetricsConfig::default())?);
let observer = Observer::new(Some(Arc::clone(&sink)));

let interval = sink.take_snapshot(); // atomically returns and clears the interval
```

The application schedules snapshots and export outside its critical path. Empty
intervals do not prove health, and whole-process failure requires external
monitoring.

## Testing

Run the crate tests from the workspace root:

```bash
cargo test -p forgelab_common --locked
cargo clippy -p forgelab_common --all-targets -- -D warnings
```

Unit tests cover parsing, issue ordering, origin propagation, unknown versions,
fan-out, clock faults, failed sends, bounded aggregation, saturation, reset, and
thread safety. The runnable example is also compiled by Cargo's all-target checks.
These checks do not substitute for typed Dora transport conformance or end-to-end
application tests.
