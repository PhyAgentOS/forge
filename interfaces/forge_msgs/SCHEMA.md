# forge_msgs Interface Schemas

This directory is the language-neutral contract for `forge_msgs`.

Python (`packages/msgs`), Rust (`crates/forge_msgs`), and C++
(`cpp/forge_msgs`) implementations should treat `forge_msgs.v1.yaml` and its
referenced domain schemas as the source of truth. The implementations are
handwritten, but their Arrow schemas and validation rules should conform to
these interfaces.

## Schema Layout

- `forge_msgs.v1.yaml` is the package manifest and transport contract.
- `common.v1.yaml` defines conventions shared across domains.
- `control.v1.yaml` defines robot and policy control messages.
- `motion.v1.yaml` defines joint trajectory and group motion action payloads.
- `geometry.v1.yaml` defines reusable geometry messages.
- `interaction.v1.yaml` defines human interaction and teleoperation messages.
- `perception.v1.yaml` defines perception result messages.
- `sensor.v1.yaml` defines raw sensor payloads.
- `tool.v1.yaml` defines the Forge ToolEndpoint protocol carrier.

Domain schemas evolve independently. Adding a new message to a domain does not
change the wire contract of existing messages.

## Transport Contract

- Payloads are single-row Apache Arrow `RecordBatch` values.
- The schema does not require Arrow schema metadata. Dora/IPC paths may drop it, so domain payload semantics must be represented as real columns.
- Motion action identity and lifecycle are the explicit exception: they use Dora's well-known event metadata keys `goal_id` and `goal_status`. Action recordings must preserve the Dora event metadata alongside the Arrow payload.
- Core messages do not contain ROS-style `Header`, timestamp, or `frame_id`.
- Sample timing belongs in explicit Dora event context. Derived outputs that
  describe a source sample should preserve that sample's time when supplied;
  Dora's native HLC is regenerated on Node publication, not inherited sample time.
- [Publish/receive observability](../observability/PROTOCOL.md) separately defines optional
  runtime timing parameters. Its origin is the first application publish, not
  sensor acquisition, simulation time, or application processing completion.
- Frame and fixed calibration information should live in Dora metadata, topic
  naming, node configuration, or adapter layers.

## Implementation Coverage

- Python implements the full v1 schema, including `TeleopObservation` and
  `ToolMessage`.
- Rust implements the shared message set used across domains, including
  `ToolMessage` but excluding the Python-only `TeleopObservation`.
- C++ implements the same shared message set as Rust in `cpp/forge_msgs`,
  including `ToolMessage`, with `ToRecordBatch` / `FromRecordBatch` APIs.
- Bidirectional Arrow IPC compatibility for `ToolMessage`, `PointCloudBuffer`,
  and `Imu` is covered between Python and C++; no Rust/Python IPC coverage is
  claimed.

## Messages

### Motion action payloads

`motion.v1.yaml` defines reusable `JointTrajectoryPoint`, `JointTrajectory`, and `JointTolerance` values plus the Goal/Feedback/Result payloads for the `FollowJointTrajectory`, `GripperCommand`, `MoveJoints`, and `MovePose` Dora actions. Durations are non-negative signed 64-bit integer nanoseconds and are relative to action or trajectory start, not wall-clock timestamps. Optional scalar values use Arrow nulls rather than numeric sentinels. Action payloads do not duplicate Dora's `goal_id` or `goal_status` metadata.

`GripperCommand` controls one coordinate selected by the action endpoint and controller configuration, so its payload does not carry `joint_name` or a dynamic unit field. Prismatic coordinates use meters, meters/s, and Newtons; revolute or rotary actuator coordinates use radians, radians/s, and Newton-meters. This permits a logical Piper total-gap coordinate to use meters while an R1-A5 DEX1 motor coordinate remains in radians.

### JointState

Robot or simulator joint observation payload.

Fields:

- `name: list<utf8>`
- `position: list<float64>`
- `velocity: list<float64>`
- `effort: list<float64>`

Rules:

- `name` must be non-empty.
- `name` items must be unique.
- `position`, `velocity`, and `effort` must either be empty or have the same length as `name`.
- Units follow common robotics convention: revolute position in radians, velocity in radians/s, effort in Nm; prismatic position in meters, velocity in meters/s, effort in N.

### JointCommand

Joint command payload for robot drivers and controllers.

Fields:

- `name: list<utf8>`
- `mode: utf8`
- `position: list<float64>`
- `velocity: list<float64>`
- `effort: list<float64>`
- `kp: list<float64>`
- `kd: list<float64>`

Rules:

- `name` must be non-empty.
- `name` items must be unique.
- `mode` must be one of `position`, `velocity`, `effort`, or `hybrid`. Payloads written before `mode` existed may omit it; readers treat missing or null `mode` as `position`.
- Each numeric list must either be empty or have the same length as `name`.
- `name` is the update set for this message. A consumer MUST update only listed joints; joints omitted from `name` retain their previous command target and MUST NOT be reset or filled with zero implicitly.
- Multiple disjoint command sources may therefore share an actuator-facing stream as ordered sparse updates. Arbitration is still required if sources can command the same joint.
- `kp` and `kd` are optional low-level gains. For Unitree-style low-level control, map `position -> q`, `velocity -> dq`, `effort -> tau`, `kp -> kp`, and `kd -> kd`.

### LocomotionCommand

Planar body-frame locomotion velocity command for ground robots.

Fields:

- `vx: float64`
- `vy: float64`
- `wz: float64`

Rules:

- `vx` is body-frame X linear velocity in m/s; positive means forward.
- `vy` is body-frame Y linear velocity in m/s; positive means left. Differential-drive robots may require `vy == 0`.
- `wz` is angular velocity about the body-frame Z axis in rad/s; positive means counter-clockwise and corresponds to ROS2 `Twist.angular.z` / yaw rate.
- `vx`, `vy`, and `wz` must be finite values.
- Stop is represented by `vx=0`, `vy=0`, and `wz=0`.
- This message intentionally does not include `mode`, `duration`, `gait`, `body_height`, `frame_id`, timestamp, or full 6D `Twist` fields.

### AudioChunk

Uncompressed audio buffer payload for streaming microphone or audio sources.

Fields:

- `sample_rate: uint32`
- `channels: uint32`
- `sample_format: utf8`
- `frame_count: uint32`
- `data: large_binary`

Rules:

- `sample_rate` and `channels` must be greater than zero.
- `sample_format` must be one of `f32le` or `s16le`.
- `frame_count` is the number of audio frames per channel, not the total sample count.
- `data` is interleaved PCM bytes. Multi-channel data is ordered by frame, e.g. `frame0_ch0`, `frame0_ch1`, `frame1_ch0`.
- `data` length must equal `frame_count * channels * bytes_per_sample`.
- Multi-byte sample formats are little-endian.
- `f32le` is little-endian float32 PCM. Producers should use normalized sample values in `[-1.0, 1.0]`.
- `s16le` is little-endian signed int16 PCM.
- Timing comes from the Dora event context. Source id, microphone placement, and calibration should live in Dora metadata, topic naming, node configuration, or adapter layers.

### Image

Uncompressed image payload.

Fields:

- `height: uint32`
- `width: uint32`
- `encoding: utf8`
- `step: uint32`
- `data: large_binary`

Rules:

- `step` is the full row length in bytes and may include row padding.
- `data` length must equal `step * height`.
- For known encodings, `step` must be at least `width * bytes_per_pixel`.
- Multi-byte pixel encodings are little-endian.
- Compressed `jpeg`, `png`, or `webp` payloads use `CompressedImage`.

Recommended encodings:

- `rgb8`
- `bgr8`
- `mono8`
- `16UC1`
- `32FC1`

### CompressedImage

Compressed image bitstream payload.

Fields:

- `format: utf8`
- `data: large_binary`

Recommended formats:

- `jpeg`
- `png`
- `webp`

### PointCloud

Organized or unorganized XYZ point cloud with common optional attributes.

Fields:

- `width/height: uint32`
- `is_dense: bool`
- `x/y/z: list<float32>`
- `intensity: list<float32>`
- `red/green/blue: list<uint8>`

Rules:

- XYZ coordinates use meters.
- Canonical writers emit exactly one row in the declared field order and mark
  every top-level field non-nullable. Required scalar/list cells and list items
  are non-null.
- Readers resolve required fields by name and require their exact physical Arrow
  types. They may accept reordered fields and ignore unknown fields. Missing,
  duplicated, null, or malformed required fields are invalid.
- `width * height` equals the point count. Because Arrow `list<T>` uses signed
  32-bit offsets, the point count must not exceed `2,147,483,647`.
- Organized points use row-major order: `index = row * width + column`. Invalid
  slots remain present so point and source-pixel indices stay aligned.
- Unorganized clouds use `height=1` and `width=point_count`. The recommended
  empty unorganized shape is `width=0`, `height=1`.
- `is_dense=true` guarantees every XYZ component is finite. Canonical producers
  should encode an invalid organized slot as NaN in all three components and set
  `is_dense=false`; consumers treat any non-finite XYZ component as invalid.
  `is_dense=false` does not require an invalid point to be present.
- `intensity` is empty or has one value per point. Producers must document its
  quantity and scale before populating it.
- RGB is all-or-none, has one byte per channel per point, and is not packed into
  a floating-point value. Consumers check XYZ validity before using geometry,
  even when an invalid organized slot retains source-pixel color.
- The coordinate frame comes from Dora metadata or stable node configuration,
  never Arrow schema metadata. Camera clouds should use a documented optical
  frame with X right, Y down, and Z forward. Timing comes from the Dora event
  context; derived outputs preserve the source event time.

PointCloud v1 is Forge's normalized, computation-oriented SoA representation.
It is not a raw vendor, PCL, or ROS `PointCloud2` byte layout. Layout-preserving
decoded point records use the separately named `PointCloudBuffer`; `PointCloud`
is unchanged. High-throughput borrowed/view APIs may be added without changing
this wire contract.

### PointCloudBuffer

Single-row, layout-preserving buffer for decoded Cartesian point records with
dynamic fixed-width attributes. It is the cross-node boundary between a device
or ROS adapter and a consumer that still needs per-point timing, laser channel,
or vendor attributes. It is not an undecoded device packet and does not imply a
specific calibration, filtering, deskew, transform, or registration stage.

Fields:

- `width/height: uint32`
- `is_dense: bool`
- `byte_order: utf8` (`little_endian` or `big_endian`)
- `point_stride: uint32`
- `row_stride: uint64`
- `fields: list<struct<name: utf8, offset: uint32, datatype: utf8, count: uint32>>`
- `data: large_binary`

The datatype set is closed to fixed-width numeric values: `int8`, `uint8`,
`int16`, `uint16`, `int32`, `uint32`, `int64`, `uint64`, `float32`, and
`float64`. Field names are non-empty and unique, counts are positive, every
field fits within `point_stride`, and declared field byte ranges do not overlap.
Point and row padding are valid. All size and offset calculations are checked;
`row_stride >= width * point_stride` and `len(data) == row_stride * height`.
If `width=0`, the only valid shape is `height=1`, `row_stride=0`, and empty data;
`point_stride` and the field descriptors still declare the record layout.

The canonical `fields` list item Arrow field is nullable, its struct children are
non-nullable, and actual null struct items or children are invalid. Every cloud
declares one scalar `x`, `y`, and `z` field. The three fields use the
same `float32` or `float64` datatype and meters. `is_dense=true` guarantees
finite XYZ values. Unorganized clouds use `height=1`. Organized byte offsets are
`row * row_stride + column * point_stride + field.offset`.

Canonical writers are little-endian, emit the declared top-level order and
non-nullability, and sort descriptors by offset then name. Readers resolve
required top-level columns by name, require exact Arrow physical types, and may
ignore additional top-level columns. Readers honor `byte_order` and never rely
on field alignment. `intensity` is an optional producer-defined scalar;
`time_offset_ns: uint32` and `ring: uint16` are recommended optional names, not a
LiDAR profile or mandatory field set. Device-specific fixed-width descriptors
may remain alongside them. Once dynamic layout and per-point attributes are no
longer needed, producers should publish normalized `PointCloud` instead.

### Imu

Decoded SI-unit inertial sample modeled after the useful semantics of ROS 2
`sensor_msgs/Imu` without copying its `Header` or numeric sentinels.

Fields:

- `orientation: struct<qx: float64, qy: float64, qz: float64, qw: float64>?`
- `angular_velocity: struct<x: float64, y: float64, z: float64>` in rad/s
- `linear_acceleration: struct<x: float64, y: float64, z: float64>` in m/s²
- `orientation_covariance: list<float64>`
- `angular_velocity_covariance: list<float64>`
- `linear_acceleration_covariance: list<float64>`
- `temperature_celsius: float64?`

Null orientation means unavailable; a present quaternion is finite, non-zero,
and never silently normalized. Orientation/vector struct children are
non-nullable. Angular velocity and linear acceleration are required and finite.
Each covariance is either empty when unavailable or a finite row-major 3x3
matrix with non-negative diagonal. Covariance list item metadata is nullable,
but actual null items are invalid. Orientation covariance
must be empty when orientation is null and represents local small-angle X/Y/Z
error, not a quaternion 4x4 covariance. Temperature is null when unavailable
and finite when present. Named quaternion fields force adapters to explicitly
convert vendor order; redundant roll, pitch, and yaw are omitted. Timing and
sensor frame follow the package-wide Dora event/configuration conventions.

### Perception result sets

`perception.v1.yaml` defines model-independent perception payloads:

- `Classification`: class hypotheses with fields `class_id: list<utf8>` and
  `score: list<float32>`. The lists may both be empty; otherwise they must have
  equal length, class ids must be unique, and scores must be finite values in
  `[0, 1]`. Scores are independent confidences and need not sum to `1`.
  Producers should emit hypotheses in descending score order. Display names and
  model-specific label semantics remain producer or model configuration.
- `Detection2DSet`: oriented pixel-space boxes and flattened classification
  hypotheses. Axis-aligned boxes use `rotation=0`, and high-level constructors
  may fill that default automatically while serialized Arrow payloads still
  include the `rotation` column.
- `Detection3DSet`: oriented metric boxes and flattened classification
  hypotheses. Axis-aligned boxes use identity orientation (`qx=0`, `qy=0`,
  `qz=0`, `qw=1`), and high-level constructors may fill that default while
  serialized Arrow payloads still include quaternion columns.
- `Keypoint2DSet`: flattened 2D keypoints grouped by instance. Arrow field order
  is `instance_id`, `detection_id`, `track_id`, `keypoint_offset`,
  `keypoint_id`, `x`, `y`, `score`.
- `Keypoint3DSet`: flattened 3D keypoints grouped by instance. Arrow field order
  is `instance_id`, `detection_id`, `track_id`, `keypoint_offset`,
  `keypoint_id`, `x`, `y`, `z`, `score`.
- `SegmentationMaskSet`: cropped binary instance masks positioned in a source
  image. Arrow field order places `score: list<float32>` immediately after
  `data`; it is either empty or has one finite `[0, 1]` value per `mask_id`.
  `score` is a producer-defined mask ranking or confidence value and is
  comparable only within the same producer, model, model version, and inference
  configuration. It is not guaranteed to be a calibrated probability or true
  IoU. SAM adapters may map predicted IoU to `score` by default; YOLO adapters
  may map the associated detection confidence or leave `score` empty. Standalone
  masks can omit detection and track associations, and high-level constructors
  may fill empty associations and zero offsets while serialized Arrow payloads
  still include those columns.

For both keypoint messages, `instance_id` values are unique. `detection_id` and
`track_id` have one entry per instance and default to the empty string when no
association is available. `keypoint_offset` has length `len(instance_id) + 1`,
starts at `0`, is monotonically non-decreasing, and ends at the flattened
keypoint count; an empty result uses `[0]`. `keypoint_id`, all coordinate lists,
and `score` have the same flattened length. Keypoint ids are unique within each
instance, coordinates are finite, and scores are finite values in `[0, 1]`.
A score of `0` marks an unavailable keypoint, which consumers must not use for
control.

`Keypoint2DSet` coordinates are pixels in the source image; producers using
resized, cropped, padded, or otherwise preprocessed model inputs must inverse-map
results to source-image coordinates. `Keypoint3DSet` coordinates use meters,
and their coordinate frame follows the transport convention: Dora metadata,
topic naming, node configuration, or an adapter layer supplies frame context.
Keypoint ids and skeleton topology remain producer configuration. These
messages represent keypoint observations, are model-independent, and are not
geometric `Pose` messages.

Label maps, topology, producer-specific score semantics, and frame context stay
in producer/model/node configuration or the existing transport conventions;
they are not repeated in per-frame payloads or separate low-frequency info
messages. No timestamp, `Header`, or frame field is added to these perception
messages.

For schema evolution, `SegmentationMaskSet` writers emit the `score` column.
Readers may accept an older payload with no score column as `score=[]`.

Empty detections use empty per-detection lists and
`hypothesis_offset=[0]`. Detection class display names remain model
configuration rather than per-frame payload data. OpenCV-style local features
such as ORB, SIFT, or SuperPoint descriptors are not part of the core streaming
schema; keep them inside a node unless a future pipeline needs cross-node
feature reuse.

### Pose

Header-less 3D pose payload with position and quaternion orientation.

Fields:

- `x: float64`
- `y: float64`
- `z: float64`
- `qx: float64`
- `qy: float64`
- `qz: float64`
- `qw: float64`

Rules:

- Quaternion `qx`, `qy`, `qz`, `qw` must not be all zero.
- Implementations should not silently normalize quaternion values.
- 2D pose should use helpers such as `Pose.from_xy_yaw(...)`, not a separate wire schema.

### PoseSet

Single-row named collection of poses.

Fields:

- `name: list<utf8>`
- `x: list<float64>`
- `y: list<float64>`
- `z: list<float64>`
- `qx: list<float64>`
- `qy: list<float64>`
- `qz: list<float64>`
- `qw: list<float64>`

Rules:

- `name` must be non-empty.
- `name` items must be unique.
- Every numeric list must have the same length as `name`.
- Each quaternion must not be all zero.

### TeleopObservation

XR device raw teleoperation observation.

Fields:

- `device: list<utf8>`: XR source ids; recommended ids are `left`, `right`,
  and `headset`.
- `x/y/z: list<float64>`: positions in meters.
- `qx/qy/qz/qw: list<float64>`: quaternion components in `xyzw` order.
- `confidence: list<float64>`: producer-defined per-device confidence in
  `[0, 1]`; it need not come directly from the XR SDK.
- `buttons_json: utf8`: JSON object whose digital values are booleans and
  analog trigger/grip values are finite numbers.
- `axes_json: utf8`: JSON object whose values are finite numbers or numeric
  arrays such as `[x, y]` joystick pairs.

Rules:

- `device` must be non-empty and unique.
- Consumers must match entries by device id and must not rely on list order.
- Pose and confidence lists must have the same length as `device`.
- Every pose and confidence value must be finite.
- Confidence values must be in the inclusive range `[0, 1]`.
- Each quaternion must not be all zero.
- `buttons_json` and `axes_json` must contain JSON objects.
- A producer may represent an unavailable pose with zero position, identity
  quaternion, and confidence `0`; consumers must not use a zero-confidence pose
  for control.
- Poses are expressed in the producer-configured XR tracking frame. The
  tracking-frame origin, handedness, and axis directions must be documented in
  producer configuration because they are not carried in this payload.
- Recommended button ids are `A`, `B`, `X`, `Y`, `left_trigger`,
  `right_trigger`, `left_grip`, `right_grip`, `left_joystick_click`, and
  `right_joystick_click`. Recommended axis ids are `left_axis` and `right_axis`;
  documented producer-specific derived-control ids are allowed.
- Timing comes from the Dora event context or an adapter layer; v1 has no
  timestamp field.
- The current implementation is Python-only.

### Text

Single UTF-8 text payload for ASR transcripts, LLM responses, TTS input, and
other text streams.

Fields:

- `text: utf8`

Rules:

- `text` must be a valid UTF-8 string.
- Producers should omit empty outputs when there is no meaningful text.

### ToolMessage

Cross-language carrier for one Forge ToolEndpoint v1alpha1 logical message.
`tool.v1.yaml` requires exactly one row and these ten Arrow columns in order:

1. `protocol: utf8` (non-null)
2. `message_type: utf8` (non-null)
3. `request_id: utf8` (nullable)
4. `invocation_id: utf8` (nullable)
5. `attempt_id: utf8` (nullable)
6. `endpoint_id: utf8` (non-null)
7. `endpoint_instance_id: utf8` (nullable for `tool.*`; required for `endpoint.*`)
8. `operation: utf8` (nullable)
9. `sequence: int64` (nullable)
10. `payload_json: utf8` (non-null)

The nullable logical-envelope fields are always present as columns and use Arrow
null when omitted; empty strings are not null sentinels. `endpoint_instance_id` may be
omitted on any `tool.*` logical message before provider routing, but every `endpoint.*`
message requires it. `payload_json` is the
JSON encoding of the logical message payload object, not the full carrier
envelope. It is a bounded strict JSON object: keys are unique, strings and keys
contain valid Unicode scalar values, numbers are finite, integers fit
`±(2^53-1)`, and nesting is at most 64 levels. The carrier has no observation
timestamp; Dora event context supplies transport observation time. `forge_msgs`
validates the carrier schema and generic correlation rules, while message-specific
payload validation remains in `forge-tool`.

Python, Rust, and C++ implement the carrier. Bidirectional Arrow IPC
compatibility is covered between Python and C++.

### PolicyCommand

Command payload sent from gateway to policy through Dora.

Delivery topic:

- `policy_command`

Fields:

- `policy_id: utf8`
- `command: utf8`
- `request_id: utf8`
- `inputs_json: utf8`

Rules:

- `policy_id` must be non-empty. Use `default` for single-policy setups.
- `command` must be non-empty and use snake_case.
- `request_id` may be empty when no response is expected.
- `inputs_json` must parse as a JSON object. Use `{}` when there are no inputs.
- A `PolicyCommandStatus` response is optional.

Recommended commands:

- `load`
- `start`
- `stop`
- `pause`
- `resume`
- `reset`
- `start_recording`
- `stop_recording`
- `discard_recording`
- `load_playback`
- `start_playback`
- `pause_playback`
- `resume_playback`
- `reset_playback`
- `seek_playback`
- `set_playback_rate`

### PolicyCommandStatus

Optional status payload sent from policy to gateway through Dora.

Delivery topic:

- `policy_command_status`

Fields:

- `policy_id: utf8`
- `command: utf8`
- `request_id: utf8`
- `status: utf8`
- `message: utf8`
- `outputs_json: utf8`

Rules:

- This message is optional. Policies may omit status output when no response is required.
- `policy_id` and `command` must be non-empty.
- `request_id` should match the originating `PolicyCommand` when present.
- `status` must be one of `accepted`, `rejected`, `running`, `done`, or `error`.
- `message` may be empty.
- `outputs_json` must parse as a JSON object. Use `{}` when there are no outputs.

## Cross-Language Implementation Notes

- Python, Rust, and C++ structs should use the same field names as the schema.
- Arrow field order should match the message's domain schema.
- Implementations should reject duplicate joint names and invalid list lengths.
- Implementations should not infer missing columns unless a field explicitly defines a compatibility default. Otherwise, a payload either conforms to the schema or fails validation.
- Helper methods may convert to fixed-order arrays, but fixed order is not part of the wire schema.
- Run `uv run python scripts/check_forge_msgs_schema.py` after changing schema
  files.
