# forge-common

`forge-common` provides dependency-free Python utilities shared by Forge packages:

- logging configuration and logger helpers;
- optional application-level publish/receive observability; and
- bounded local latency histograms and diagnostic counters.

The observability modules do not import Dora, Arrow, or a monitoring SDK. They do
not own a node, event loop, background thread, or exporter.

Related documentation:

- [Observability documentation index](../../interfaces/observability/README.md)
- [Metadata protocol](../../interfaces/observability/PROTOCOL.md)


## Capability status

The observability APIs are available starting with `forge-common 2.1.0`.

```bash
pip install 'forge-common>=2.1.0,<3'
```

| Capability | Status |
| --- | --- |
| Logging helpers | Available in the current Common release. |
| Metadata parsing and explicit publish/receive contexts | Implemented in Python and Rust. |
| Bounded histograms, counters, and interval snapshots | Implemented in Python and Rust. |
| Metadata validation and generic topology unit tests | Implemented. |
| Real Dora topology, typed cross-language metadata conformance, and C++ implementation | Pending. |
| Application-wide instrumentation and overhead validation | Pending. |

The application remains responsible for its Dora dependency, input loop, actual
parent selection, snapshot scheduling, and export. Unit-test coverage does not
imply that an existing dataflow is instrumented. Rust users should follow the
[Rust crate guide](../../crates/forge_common/README.md).

## Logging usage

```python
from forge_common import configure_from_env, get_logger

configure_from_env()
logger = get_logger(__name__)
logger.info("Forge component started")
```

### Environment variables

- `FORGE_LOG_LEVEL`: log level, such as `DEBUG`, `INFO`, `WARNING`, `ERROR`, or
  `CRITICAL`.
- `FORGE_LOG_FILE`: optional file path for log output.
- `FORGE_LOG_CONSOLE`: whether console logging is enabled (`true` or `false`).
- `FORGE_LOG_STREAM`: console stream (`stdout` or `stderr`).

Use `configure_from_env()` in applications or node entry points, then call
`get_logger(__name__)` inside individual modules.

## Observability quickstart

A complete dependency-free example is available at
[`examples/publish_receive.py`](examples/publish_receive.py). Run it from the
repository root:

```bash
uv run --package forge-common python packages/common/examples/publish_receive.py
```

It creates a new camera origin, observes the image at a consumer, performs a
derived publish, and prints hop, end-to-end, and turnaround aggregates. It uses
an in-memory sender and deterministic clock so the metadata and measured values
are directly inspectable.

The Dora-oriented snippets below assume an existing `node` with an `out` port and
an already prepared `payload`. The helper uses a structural sender interface; it
does not construct a Dora node.

```python
from forge_common.metrics import BoundedMetricsSink
from forge_common.observability import Observer

sink = BoundedMetricsSink(max_series=256)
observer = Observer(sink=sink)
observer.publish(node, "out", payload, new_origin=True)
```

A forwarding consumer retains the actual input context:

```python
for event in node:
    if event.get("type") == "STOP":
        break
    if event.get("type") != "INPUT" or event.get("kind") not in (None, "dora"):
        continue

    context = observer.observe_receive(event)
    observer.publish(
        node,
        "out",
        event["value"],
        parent=context,
        metadata=event.get("metadata"),
    )
```

Call `observe_receive()` before application decoding or queuing. Cache its context
with the actual data. A join selects an explicit reference input, not the most
recent event or timer. Supplying the incoming metadata is appropriate for pure
forwarding; derived outputs should include only their applicable business fields.

### Publication modes

| Call | Behavior |
| --- | --- |
| `publish(..., new_origin=True)` | Starts a reference path; origin and publish use the same clock sample. |
| `publish(..., parent=context)` | Retains valid origin information and refreshes publish time. |
| `publish(...)` | Records publish time only; provenance is unknown. |

`parent` and `new_origin=True` are mutually exclusive. Optional `origin_id` is
accepted only for a new origin and follows the protocol's UTF-8 length limit. It
is never a metrics label. `Observer()` without a sink still propagates timing;
there is no global Node patching or automatic instrumentation.

## Public API

| Symbol | Module | Purpose |
| --- | --- | --- |
| `Observer` | `forge_common.observability` | Owns a local timing domain and optional metrics sink; exposes `observe_receive()` and `publish()`. |
| `ReceiveContext` | `forge_common.observability` | Immutable input context obtained from `observe_receive()` and carried with data. |
| `ObservationMetadata` | `forge_common.observability` | Normalized timing fields and parser issues. |
| `parse_metadata()` | `forge_common.observability` | Parses metadata without I/O or a payload dependency. |
| `Clock`, `SystemClock` | `forge_common.observability` | Injectable clocks and the default system/monotonic implementation. |
| `Sender` | `forge_common.observability` | Structural `send_output(output_id, data, metadata)` interface. |
| `MetricsSink` | `forge_common.observability` | Local `observe()` and `increment()` aggregation interface. |
| `BoundedMetricsSink` | `forge_common.metrics` | Thread-safe bounded aggregation with `snapshot(reset=False)`. |

### Metadata handling

Parent selection does not copy business metadata. The caller supplies an optional
mapping appropriate for the output; the helper copies it and removes Dora's
receive-layer `timestamp`. It does not mutate input mappings or inherit native
HLC timestamps.

Known timing fields supplied through `metadata=` are rebuilt from explicit
publication arguments, not treated as an implicit parent. Unknown-version
metadata is forwarded opaquely, except for `timestamp`. Full opaque forwarding
requires the original mapping; `ReceiveContext` retains no unknown mutable values.
The [protocol](../../interfaces/observability/PROTOCOL.md#4-publication-and-propagation)
defines version and propagation behavior.

The Python parser accepts exact built-in `int` and `str` values for the respective
wire fields, not subclasses; booleans are not integer timestamps. Optional invalid
IDs do not discard valid timing. Package tests cover normalized results and issue
ordering; real cross-language conformance requires typed Dora metadata tests.

### Clocks and failure handling

Use the same `Observer` for a local receive-to-publish turnaround. Its domain token
and process ID must match the received context. Another Observer, another process,
or a deserialized context may still propagate origin, but cannot use that local
monotonic sample. `invalid_parent` reports unavailable local turnaround timing,
not rejection of the business publication.

For deterministic tests, inject a `Clock` with `time_ns()` and `monotonic_ns()`.
Both return nonnegative int64 nanoseconds. Failed or invalid samples disable only
the affected measurements. A custom clock can reject independently known-invalid
intervals; `SystemClock` does not monitor synchronization or detect every jump.
Cross-machine comparisons require documented clock synchronization.

Invalid local arguments raise before sending. The helper calls `send_output()`
exactly once, propagates sender exceptions unchanged, and does not record failed
sends as successful publications or turnarounds. It never retries.

Sink exceptions are isolated and counted in the saturating
`observer.sink_errors` diagnostic, even when the sink itself is broken. Metrics
failures do not suppress or replace a business send. A custom sink must not
perform blocking I/O on the hot path.

## Local metrics

The sink accepts nanoseconds and exports seconds for these histograms:

| Metric | Interval |
| --- | --- |
| `forge_hop_latency_seconds` | Incoming publish to receive. |
| `forge_e2e_latency_seconds` | Selected first publish to receive. |
| `forge_node_turnaround_seconds` | Explicit parent receive to publish, including waiting. |

`forge_observability_events_total` uses the fixed reason set exported as
`EVENT_REASONS`. A custom sink implements:

```python
observe(metric, value_ns, *, input_id="", output_id="")
increment(reason, *, input_id="", output_id="")
```

Both histograms and counters share `max_series`, which defaults to 1024. Port
labels are limited to 128 characters by default. Use one sink per configured
node/measurement scope, or qualify the scope in an exporter; do not merge unrelated
pipelines with identical port names or use origin/trace IDs as labels.

Default finite bounds are 1 ms, 2 ms, ..., 32.768 s, plus overflow. Custom
`buckets_ns` must have 1..64 strictly increasing positive int64 bounds that remain
distinct when exported as floating-point seconds. Aggregation and bucket
comparisons use integer nanoseconds. Counts saturate at `2^63 - 1`.

The sink retains aggregates, not samples. Invalid updates, new series rejected at
capacity, and saturated updates have separate unlabelled diagnostic totals.
Negative measured intervals become anomaly counters rather than zero samples;
invalid origin information does not erase independently valid hop measurements.

### Interval snapshots

The host application schedules snapshots outside its critical path:

```python
import json

from forge_common import get_logger

logger = get_logger(__name__)
interval = sink.snapshot(reset=True)
logger.info("observability %s", json.dumps(interval, allow_nan=False))
```

`snapshot(reset=True)` atomically returns and clears the interval, including
series identities and diagnostic totals. Each histogram contains cumulative
inclusive buckets, count, sum, min, and max in seconds. The overflow bucket has
`le: null`, avoiding non-finite JSON numbers.

Empty intervals contain no stale histogram samples and must not be presented as
healthy zero latency. Exporters can derive approximate quantiles and budget
ratios from the buckets; the core does not claim exact P99. Whole-process failure
requires external monitoring.

The package starts no thread, timer, file output, or network exporter. The host
owns scheduling even when input stops. No hard-real-time guarantee or overhead
budget has been established.

## Testing

From the Forge repository root:

```bash
uv run pytest packages/common/tests
uv run ruff check packages/common
```

Tests cover metadata validation, timing formulas, explicit provenance, fan-out,
joins, cached reuse, unknown versions, faults, interval resets, and resource
bounds. The runnable example covers the public new-origin, receive, derived
publish, and snapshot flow.

Python 3.11 syntax and imports without site-packages are checked. Those checks do
not substitute for testing every supported interpreter, real Dora transport,
cross-language exchange, or application overhead.
