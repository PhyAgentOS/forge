# Changelog

Forge package families are versioned independently. Python, Rust, and C++ implementations of the same logical family share one package version and protected GitLab tag; language-neutral interface versions are tracked separately.

## Unreleased

## forge-common 2.1.0 - 2026-09-08

Protected tag: `forge-common-v2.1.0`

- Define the optional Forge publish/receive observability v1 metadata contract without changing Arrow payload schemas.
- Add dependency-free Python `forge-common` receive contexts and publish helpers with explicit provenance, clock injection, and isolated metrics failures.
- Add std-only Rust `forge_common` typed metadata parsing, receive contexts, explicit publication modes, two-phase send completion, and injectable clocks without a Dora or Arrow dependency.
- Add bounded local latency histograms, diagnostic counters, and interval snapshots in Python and Rust; no exporter threads, network dependencies, or automatic business-node instrumentation are enabled.
- Add runnable Python and Rust examples covering new-origin publication, receive observation, derived publication, metadata adaptation, and interval snapshots; document C++ observability as pending rather than presenting an unavailable API.
- Add Common tests to Python CI. Real Dora topology tests, typed cross-language metadata conformance, C++ observability, and application instrumentation remain follow-up work.

## forge-common 2.0.0 - 2026-09-04

Protected tag: `forge-common-v2.0.0`

- Align Common with the coordinated Forge 2.0 release train without changing its public utility APIs.
- Preserve the Python, Rust, and C++ logging contracts from the 1.x line.

## forge-kinematics 2.0.0 - 2026-09-04

Protected tag: `forge-kinematics-v2.0.0`

- Align Kinematics with the coordinated Forge 2.0 release train without changing its solver APIs or optional dependency model.
- Preserve the Pinocchio-based FK, Jacobian, and IK behavior from the 1.x line.

## forge-policy 2.0.0 - 2026-09-03

Protected tag: `forge-policy-v2.0.0`

- Migrate the Python Dora runner dependency from `dora-rs 0.4.1` to the compatible Dora 1.x line (`>=1.0,<2`), currently locked and validated with 1.0.1.
- Require the Forge Msgs 2 carrier line.

## forge-robot 2.0.0 - 2026-09-03

Protected tag: `forge-robot-v2.0.0`

- Migrate the Python Dora runner dependency and optional C++ node bridge from Dora 0.4.1 to the compatible Dora 1.x line; Python is currently locked to 1.0.1 and the reproducible C++ source build is pinned to `v1.0.1`.
- Require the Forge Msgs 2 carrier line while keeping the driver contract and node input/output semantics unchanged.

## forge-msgs 2.0.0 - 2026-09-03

Protected tag: `forge-msgs-v2.0.0`

- Move the public Rust `RecordBatch` API from Arrow 54 to Arrow 59 for Dora 1.0 interoperability.
- Preserve the cross-language `forge_msgs.v1` wire schema; the package major and wire-schema version are now tracked independently.

## forge-tool 2.0.0 - 2026-09-03

Protected tag: `forge-tool-v2.0.0`

- Move the optional Dora carrier integration to the Forge Msgs 2 line.

## forge-tool 1.0.0 - 2026-08-27

Protected tag: `forge-tool-v1.0.0`

- Promote the Tool protocol line to its first stable major: the `forge.tool.endpoint/v1alpha1` wire contract, Query/Action/Session dispatch, and semantics-scoped control (`cancel` for Action, `stop` for Session) are the supported surface.
- Preserve an established terminal result when `start()` raises after terminal progression, and reply with the preserved result instead of a rejection or `unknown` (ported from the master tool line during the master->dev merge).
- Track the forge-msgs 1.3.0 line merged from master: the `forge_msgs.ToolMessage` carrier and sensor messages (IMU, point cloud buffer) ship with this release.

## forge-tool 0.2.0 - 2026-08-27

Protected tag: `forge-tool-v0.2.0`

- Add the dependency-free `forge-tool` Python package family.
- Define endpoint descriptors, attempt-scoped `ToolExecutionKey`, authoritative terminal results, structured control/error/status/event models, and async Query/Action/Session Endpoint protocols.
- Add the generic ToolEndpoint v1alpha1 invoke/status/result/control/event message family, strict deterministic UTF-8 JSON codec, typed payload adapters, endpoint instance identity, event sequence, and descriptor registration conversion.
- Define ToolEndpoint as a provider-side SPI/Wire boundary for embedding a binding/handler in concrete Dora business nodes; identity/correlation, lifecycle, terminal-result, and endpoint-sequence value rules remain protocol semantics, while P0 provides no stateful replay, deduplication, retention, retry, or exactly-once execution guarantee.
- Build normal execution responses directly from their originating request envelope so endpoint nodes do not need to retain a complete invoke context solely for correlation.
- Add correlated `endpoint.registry.response` management acknowledgements with strict `EndpointRegistryResponse` lease/revision/error invariants, request-derived factories, and management response correlation validation; require `request_id` for register, unregister, and Registry response while forbidding it on unsolicited endpoint health status.
- Simplify endpoint management to periodic `endpoint.register` announce/upsert/lease renewal plus `endpoint.unregister` and `endpoint.registry.response`; remove `endpoint.heartbeat` and its factory API.
- Allow every `tool.*` logical envelope to omit `endpoint_instance_id` before provider routing, including correlated invoke responses/errors when Gateway selection fails; require it on every `endpoint.*` message, keep `None` response correlation exact, and make the cross-language Arrow carrier column nullable accordingly.
- Align Registry semantics with the current Gateway: a static configured/trusted route authorizes `endpoint_id` and owns at most one current instance; descriptor-equal same-instance register renews without a revision, a new instance atomically replaces current with one revision, same-instance descriptor changes are rejected, matching unregister and expiry remove current with one revision, absent unregister is effect-idempotently accepted, and a stale instance cannot remove current.
- Define `registry_revision` only as a process-local availability-state revision reported after the current decision; Gateway restart begins empty and periodic register restores availability.
- Document the current Query-only Gateway scope: it routes invoke and correlates only terminal response/`tool.error`; `endpoint.status` and `tool.event` remain protocol/model messages but are not accepted or routed. Status validation is deferred to future availability/health needs, and event correlation to future Action/Session needs.
- Document the existing simple experimental HTTP Query discovery/invoke bridge and Dora logical caller vertical bridge, plus the completed concrete YOLO Dora provider-node embedding and first real Query vertical; additional providers, a general runner, the complete caller-facing Tool Runtime API, stable Dora caller contract, Action/Session, SSE, and MCP remain future work.
- Narrow execution guarantees to identity and correlation: `request_id` identifies one exchange, while `invocation_id + attempt_id` identifies one execution attempt. P0 provides no replay/dedup/exactly-once behavior and does not automatically retry Query; Action/retry and any bounded stateful deduplication remain future requirement-driven design.
- Declare this v1alpha1 contract the first atomic tagged/public Tool release contract; earlier untagged prototypes are incompatible and mixed Python/Rust/C++/Gateway/provider deployments are unsupported.
- Add a transport-independent Query-first `ToolEndpointHandler` with exact descriptor-to-implementation mapping validation, endpoint-instance route checks, correlated terminal results, and structured endpoint rejection handling; it owns no Dora node or execution state.
- Add an optional `forge_tool.dora` in-memory Arrow carrier binding that bridges one `forge_msgs.ToolMessage` input to the logical Query handler and returns a response `RecordBatch` without owning a Dora node, event loop, or metadata; it bounds raw carriers, raw payload JSON, accepted logical requests, and responses, reserves correlated-error headroom, rejects IPC bytes for upstream bounded decode, and keeps the base `forge-tool` install dependency-free.
- Keep message-specific logical payload validation in `forge-tool`; the cross-language `forge_msgs.ToolMessage` is its Arrow transport carrier.
- Keep the complete caller-facing Tool Runtime API, stable caller contracts, Gateway implementation, concrete provider-node Dora I/O wiring, ToolSpec, and concrete endpoint adapters outside this initial protocol package; the completed external YOLO provider wiring and current Gateway Query bridges do not change the package boundary, and a future general runner is optional rather than the only integration path.

- Implement Session dispatch in `ToolEndpointHandler`: Session operations share the Action start path (per-operation admission, duplicate suppression, bounded early-event buffering, terminal-result barriers, and `unknown` convergence), and Action and Session share status, result, and control dispatch.
- Scope control dispatch by semantics: `cancel` is admitted only for Action operations and `stop` only for Session operations, with other control requests rejected as unsupported.
- Generalize the bound-provider typing from `ActionToolEndpoint` to `ActionToolEndpoint | SessionToolEndpoint` across the event emitter, start-invoke, and result paths.
- Route Action and Session invoke messages through the `forge_tool.dora` asynchronous event-sink path while keeping `handle_invoke` and `handle_input` Query-only.

## forge-msgs 1.3.0 - 2026-08-24

Protected tag: `forge-msgs-v1.3.0`

- Add the cross-language `PointCloudBuffer` message for decoded Cartesian point records whose fixed-width fields, byte offsets, point stride, row stride, padding, byte order, and additional sensor attributes must survive a node boundary. Keep normalized `PointCloud` unchanged for registered clouds, maps, ICP, visualization, and consumers that no longer need the source record layout.
- Require scalar meter-valued `x`, `y`, and `z` fields while leaving intensity, per-point time, laser channel, and vendor attributes optional. Canonical writers sort descriptors and emit little-endian bytes; readers validate checked sizes, unique non-overlapping fields, exact data length, canonical empty shape, dense finite XYZ, endian conversion, and unaligned access.
- Add safe local `PointCloudBufferView` APIs: Python exposes retained, read-only NumPy strided field views over Arrow payload storage; Rust and C++ provide checked descriptor, point, scalar, and fixed-array element access without unaligned pointer casts.
- Add the cross-language `Imu` message with nullable named-XYZW orientation, required SI-unit angular velocity and linear acceleration, optional 3x3 covariance lists, and nullable temperature. Arrow null and empty covariance lists replace ROS numeric sentinels; quaternion values remain unnormalized.
- Cover the two messages with Python, Rust, and C++ schema/validation tests plus bidirectional Python/C++ Arrow IPC interoperability, including organized row padding, big-endian input, null nested IMU values, malformed layouts, and invalid UTF-8 descriptor names.

## forge-tool 0.1.0 - 2026-08-24

Protected tag: `forge-tool-v0.1.0`

- Add the dependency-free `forge-tool` Python package with validated endpoint descriptors, attempt-scoped `ToolExecutionKey`, authoritative terminal results, structured status/control/event/error models, and async Query/Action/Session ToolEndpoint SPI.
- Define the generic ToolEndpoint v1alpha1 invoke/status/result/control/event/error and endpoint-management message family, strict UTF-8 JSON codec, closed typed payload schemas, complete request-derived envelope factories, and response correlation validation.
- Add periodic `endpoint.register` lease renewal, `endpoint.unregister`, correlated `endpoint.registry.response`, process-local Registry revision semantics, trusted configured-route authority, and exact management identity rules.
- Add `ToolEndpointHandler`: Query returns a correlated terminal result directly; Action supports start/status/result/cancel/events, per-operation admission, bounded early events and execution retention, response-first early-event tuple ordering, terminal-result barriers, lifecycle validation, terminal `unknown` convergence, and retained-key duplicate suppression.
- Add optional `forge_tool.dora` Arrow integration for Query and Action. It bounds carriers, raw payload JSON, accepted requests, and responses; reserves correlated-error headroom; enforces an acknowledged response-before-event publication barrier; rejects IPC bytes for bounded upstream decode; and owns no Dora node, event loop, ports, or metadata.
- Add the exact ten-column `forge_msgs.ToolMessage` carrier in Python, Rust, and C++; keep message-specific payload validation in `forge-tool` and cover bidirectional Python/C++ Arrow IPC interop.
- Keep ToolEndpoint as a provider-side SPI/Wire boundary. The external Gateway and YOLO integration currently validate an experimental endpoint-oriented Query bridge, not the target ToolSpec/Runtime caller boundary; the complete caller-facing Runtime, stable Web/Dora caller contracts, Gateway Action/events, Session dispatch, SSE, and MCP remain outside Tool 0.1.0.
- Define identity and correlation without promising replay, persistent idempotency, exactly-once execution, or automatic retry. Action duplicate suppression is private, bounded, and ends on eviction or process restart.
- Release v1alpha1 as the first coordinated tagged/public Tool contract; earlier untagged prototypes are incompatible and mixed logical-package/carrier/Gateway/provider deployments are unsupported.

## forge-msgs 1.2.0 - 2026-08-24

Protected tag: `forge-msgs-v1.2.0`

This backward-compatible message-family release is coordinated with Tool 0.1.0. Msgs is
published first so the Tool Dora binding can resolve its carrier dependency.

- Add `tool.v1.yaml` and `forge_msgs.ToolMessage` alongside `PolicyCommand` and `PolicyCommandStatus` in the canonical cross-language schema and the Python, Rust, and C++ carrier implementations.
- Define an exact ten-column, single-row Arrow carrier whose optional columns use null, whose `payload_json` contains the logical payload object, and which has no observation timestamp; Python `from_arrow()` additionally supports a caller-configured pre-validation raw payload byte limit.
- Add `endpoint.registry.response` to Python, Rust, and C++ `ToolMessage` carriers and enforce the management exchange `request_id` matrix across all three implementations.
- Add bidirectional Python/C++ Arrow IPC compatibility coverage for the carrier; Rust provides Arrow `RecordBatch` conversion without a claimed Rust/Python IPC interop test.
- Require C++20 for `forge_msgs_cpp` and propagate that requirement to CMake consumers with `cxx_std_20`.

## forge-msgs 1.1.0 - 2026-08-22

Protected tag: `forge-msgs-v1.1.0`

- Add Python `PointCloudBatch.from_numpy()` and `PointCloudView.from_arrow()` APIs with explicit NumPy copy/casting policies, read-only Arrow-backed field views, and local payload-buffer sharing for high-throughput producers and consumers.
- Align the Python `PointCloud` writer with the canonical non-nullable top-level Arrow schema already emitted by Rust and C++; field names, order, physical value types, payload values, and the canonical v1 contract are unchanged, and readers remain compatible with prior nullable schemas.
- Harden `PointCloud` readers across Python, Rust, and C++ with exact single-row validation, stricter malformed-payload checks, duplicate-field rejection, complete IPC stream consumption, and expanded Python/C++ interoperability coverage.
- Document organized-cloud ordering, invalid-point, frame/time, buffer ownership, and future packed-format semantics without changing the v1 fields.

## Package-family 1.0.1 releases - 2026-08-13

| Package family | Version | Protected tag |
|---|---:|---|
| Common | `1.0.1` | `forge-common-v1.0.1` |
| Msgs | `1.0.1` | `forge-msgs-v1.0.1` |
| Robot | `1.0.1` | `forge-robot-v1.0.1` |
| Policy | `1.0.1` | `forge-policy-v1.0.1` |
| Kinematics | `1.0.1` | `forge-kinematics-v1.0.1` |

### Packaging

- Include the complete Apache-2.0 `LICENSE` in every Python wheel and source distribution.
- Add one repository-level command to cleanly build and validate all Python distributions and generate `SHA256SUMS`.
- Validate wheel and sdist identity, Apache-2.0 metadata, exact license layout, and unexpected output entries.
- Add guarded, single-family release candidate builds while retaining all-family local validation.
- Make CI validate all five Python distributions instead of only `forge-kinematics`.
- Add repository, changelog, and issue tracker links to the published Python package metadata.

## Initial package-family 1.0 releases - 2026-08-02

| Package family | Version | Protected tag |
|---|---:|---|
| Common | `1.0.0` | `forge-common-v1.0.0` |
| Msgs | `1.0.0` | `forge-msgs-v1.0.0` |
| Robot | `1.0.0` | `forge-robot-v1.0.0` |
| Policy | `1.0.0` | `forge-policy-v1.0.0` |
| Kinematics | `1.0.0` | `forge-kinematics-v1.0.0` |

The initial tags may all point to the same clean, fully validated commit. A family can use an independent version and tag in later releases.

### Stable contracts

- `forge-msgs`: `interfaces/forge_msgs/*.v1.yaml` is the canonical cross-language message contract; Python, Rust, and C++ implementations use compatible Apache Arrow schemas and validation semantics.
- `forge-msgs`: existing field names, field order, Arrow types, null behavior, units, and sparse command semantics are stable for the `1.x` line.
- `forge-robot`: the Python and C++ implementations define the stable robot driver protocol, safety helpers, Dora runner inputs, and outputs.
- `forge-policy`: the Python package defines the stable `PolicyAdapter` plug-in protocol and Policy Operator runner contract.
- `forge-kinematics`: the Python package defines the stable FK, Jacobian, and inverse-kinematics API.
- `forge-common`: Python, Rust, and C++ provide shared utilities; Python and C++ logging use the documented `FORGE_LOG_*` environment variables.

### Compatibility policy

- Compatible bug fixes increment the affected family patch version.
- Backward-compatible additions increment the affected family minor version.
- Removing fields, changing field types or units, reinterpreting values, or breaking a family API requires that family to increment its major version.
- Historical generic tags such as `v1.0.0` predate this release model and must not be moved or reused.
