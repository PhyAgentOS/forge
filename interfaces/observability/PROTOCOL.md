# Forge Observability Metadata Protocol v1

- Status: **unreleased**
- Metadata version: `forge_obs_version = 1`

This document is the normative language-neutral contract for application-level
publish/receive timing carried in Dora event metadata. Related documentation:

- [Documentation index](README.md)
- [Python package guide](../../packages/common/README.md)
- [Rust crate guide](../../crates/forge_common/README.md)
- [Message transport conventions](../forge_msgs/SCHEMA.md#transport-contract)

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** describe protocol
requirements. Package capabilities and validation status belong to the package
guide, not to the field definitions below.

## 1. Scope

The protocol defines application publication and reception times, an optional
reference origin, and their propagation through explicitly associated messages.
The same contract applies to every producer and consumer, independent of its
business role.

It does not define sensor acquisition time, processing completion, hardware
execution, a complete multi-input causal graph, deduplication, or first-response
analysis. No Arrow payload or Arrow schema metadata changes are required.

## 2. Measurement boundaries

| Event | Boundary |
| --- | --- |
| Publish | The application clock sample immediately before constructing outgoing timing metadata and calling `send_output`, after payload preparation. |
| Receive | The clock sample immediately after Dora hands an INPUT to the application, before application decoding, queuing, or processing. |
| Origin | An explicitly designated first publish, not sensor acquisition time. |

```text
hop latency     = receive system time - incoming publish time
e2e latency     = receive system time - incoming origin time
node turnaround = outgoing publish monotonic time - parent receive monotonic time
```

Hop latency includes sending, transport, queues, and scheduling, not just the
network. Turnaround includes waiting and caching, not just computation.
End-to-end latency covers the selected origin-to-this-receiver path, excluding
work before its first publish or after its final receive.

Each reception is a sample. Repeated outputs, fan-out, or duplicate delivery can
produce multiple samples for one origin; this is not first-response timing for
every source input. Measurements from different origins or endpoints SHOULD NOT
be combined into one end-to-end series without an explicit shared scope.

## 3. Metadata fields and validation

### 3.1 Field definitions

Fields are flat Dora event parameters, not a nested dictionary:

| Key | Type | Requirement | Meaning |
| --- | --- | --- | --- |
| `forge_obs_version` | int64 | Required for decoding | Exactly `1` for this version. |
| `forge_publish_time_ns` | int64 | Required on an instrumented v1 publish with a valid system clock | Current application publish time. |
| `forge_origin_time_ns` | int64 | Optional; required for end-to-end timing | First application publish time on the selected reference path. |
| `forge_origin_id` | UTF-8 string | Optional; requires a valid origin time | Reference origin identity, not used in time arithmetic. |

Times MUST be Unix epoch nanoseconds in `[0, 2^63 - 1]`. Zero is valid, not a
missing sentinel. Floats, booleans, nulls, strings, and integer overflow are invalid
for integer fields.

An origin ID MUST contain 1..256 UTF-8 bytes without unpaired surrogates. It need
not be a UUID, but producers that generate IDs SHOULD avoid reuse across
restarts. The ID is not a current-message ID, trace ID, or business idempotency key.
No uniqueness validation or deduplication is implied.

Receive and local monotonic times remain local. Producers MUST NOT accumulate
per-hop timestamp arrays, path arrays, or calculated latency values in these
fields.

### 3.2 Independent field validity

No observability fields means legacy or uninstrumented input. Receivers MUST NOT
reject the business message merely because timing metadata is absent or invalid.
For v1, independently usable fields remain usable when another field is bad:

- A missing or invalid origin does not discard valid publish timing.
- A missing or invalid publish time does not discard an independently valid origin.
- If both times are valid but origin > publish, origin is invalid and publish
  remains usable.
- A bad optional ID is discarded without discarding valid times.
- An ID without a valid origin is invalid.

A valid integer version in `[1, 2^63 - 1]` other than 1 is unknown and MUST NOT be
interpreted as v1. Missing, noninteger, zero, negative, or overflowing versions
are invalid.

### 3.3 Parser results

Receivers normalize fields and report ordered issue codes as follows:

| Condition | Result |
| --- | --- |
| Missing context, including top-level null | `missing_context`; stop decoding. |
| Other non-mapping metadata | `invalid_metadata`; stop decoding. |
| Invalid version | `invalid_version`; stop decoding. |
| Unknown version | Preserve the version, report `unknown_version`, and stop decoding. |
| Invalid publish time | `invalid_publish_time`; continue with independent fields. |
| Missing or invalid origin time | `missing_origin` or `invalid_origin_time`. |
| Valid origin time later than valid publish time | `conflicting_origin`; discard origin. |
| Present ID without a valid origin or with an invalid value | `invalid_origin_id`; discard ID. |

For decoded v1 input, applicable issues appear in the table's order.
Language-binding restrictions and defensive tests belong to each implementation.
Real cross-language conformance MUST be validated with typed Dora metadata rather
than JSON fixtures that cannot represent every Dora parameter boundary.

## 4. Publication and propagation

### 4.1 Publication modes

A publisher selects one of three modes:

| Mode | Rule |
| --- | --- |
| New origin | Explicitly designate a new path. Sample time once and set publish and origin to that value. An optional origin ID may be supplied. |
| Derived output | Explicitly associate an input context. Refresh publish and inherit only its valid origin and optional ID. Missing origin stays missing. |
| Unlinked output | Generate publish timing only. Do not create an origin at an unknown point in the chain. |

Parent selection and new-origin creation are mutually exclusive. Known timing
fields supplied as ordinary metadata MUST NOT implicitly select a parent.
Instrumentation MUST NOT introduce retries, suppress business sender failures,
or count a failed send as a successful publish or turnaround.

### 4.2 Input association

An input context MUST remain associated with the actual data through caching,
queues, and asynchronous work. The publisher MUST NOT infer provenance from a
global last-input value, a later event, or a timer.

Fan-out requires independent output metadata. A join explicitly selects one
actual reference input; that origin represents one lineage, not all input
dependencies. A missing reference MUST NOT silently select a different input.
When provenance is unavailable, only independently valid hop timing is reported.

Turnaround requires a valid local receive-to-publish clock domain. A foreign local
clock sample MUST NOT be used for turnaround, even if its origin can still be
propagated. Binding-specific domain checks belong to the package guide.

### 4.3 Other metadata and unknown versions

Parent selection does not copy business metadata. The caller supplies parameters
appropriate for the output; pure forwarding may supply the incoming metadata.
The helper copies that mapping rather than editing it in place. Domain-specific
parameters follow their own propagation contracts.

The Dora receive-layer `timestamp` key is excluded from forwarded user parameters,
avoiding Python's flattened parameter overriding the new native timestamp.
Native HLC is not an inherited sample or origin time. Unrelated parameters are
preserved; this protocol does not generate trace context.

For known or invalid input versions, outgoing v1 timing fields are cleared and
rebuilt from explicit publication arguments. Supplied unknown-version metadata is
forwarded opaquely, except for Dora's `timestamp`; its Forge fields MUST NOT be
decoded or updated by a v1-only publisher. Creating a v1 origin within that opaque
metadata is invalid.

An unknown-version parent without opaque metadata does not authorize a v1
replacement: outgoing known v1 fields are omitted. Full opaque forwarding requires
the original metadata mapping; a normalized receive context need not retain
unknown mutable values. A v1-only observer skips unknown-version wire timing.

Every boundary advertised as an instrumented hop must be covered by an appropriate
implementation. A legacy node that preserves an old publish time cannot be
automatically detected from these fields. Their presence at the endpoint alone
is not evidence of complete instrumentation.

## 5. Clocks and failures

System time comparisons require a shared, sufficiently synchronized time basis.
Dora HLC does not synchronize physical clocks. Cross-machine SLA reporting MUST
state synchronization conditions and uncertainty. Local durations use a valid
local monotonic clock domain; monotonic values are not compared across machines.

Negative differences produce anomaly reports, never zero samples. Large positive
differences remain samples unless independent clock diagnostics establish
invalidity. Wall and monotonic clock failures are isolated independently.

With an unavailable publish wall clock, known timing fields are omitted rather
than reusing stale times. Opaque unknown-version metadata remains untouched.
Telemetry failures MUST NOT change the underlying business send or cause a retry.
This does not make invalid local API arguments valid.

Replay performance starts a new explicit origin for the current run. Acquisition,
simulation, and device time belong to separate data semantics and MUST NOT be
written into runtime timing fields. The parser cannot infer a clock domain or
synchronization quality from a number.

## 6. Implementation guidance

Implementations SHOULD keep local aggregation bounded and separate propagation
from collection. Disabling a metrics sink must not refresh origins or leave stale
publish times on an otherwise instrumented boundary.

Metric labels SHOULD describe configured measurement scopes and ports, not origin,
message, or trace IDs. Empty intervals are not evidence of health, and whole-process
failure requires external monitoring.

The protocol does not mandate an exporter, scheduler, tracing backend, histogram
layout, or hard-real-time guarantee. The
[Python package guide](../../packages/common/README.md) and
[Rust crate guide](../../crates/forge_common/README.md) define their current helper
APIs, metrics, snapshot formats, bounds, and restrictions.
