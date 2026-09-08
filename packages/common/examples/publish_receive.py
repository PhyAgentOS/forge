"""Minimal new-origin -> receive -> derived-publish observability example."""

from __future__ import annotations

import json
from typing import Any

from forge_common.metrics import BoundedMetricsSink
from forge_common.observability import Observer


class MutableClock:
    def __init__(self, wall_ns: int, monotonic_ns: int) -> None:
        self.wall_ns = wall_ns
        self.monotonic_value_ns = monotonic_ns

    def set(self, wall_ns: int, monotonic_ns: int) -> None:
        self.wall_ns = wall_ns
        self.monotonic_value_ns = monotonic_ns

    def time_ns(self) -> int:
        return self.wall_ns

    def monotonic_ns(self) -> int:
        return self.monotonic_value_ns


class InMemorySender:
    """Small stand-in for Dora's send_output in this dependency-free example."""

    def __init__(self) -> None:
        self.outputs: list[tuple[str, Any, dict[str, Any]]] = []

    def send_output(
        self, output_id: str, data: Any, metadata: dict[str, Any], /
    ) -> None:
        self.outputs.append((output_id, data, metadata))


def main() -> None:
    start_ns = 1_700_000_000_000_000_000
    camera_clock = MutableClock(start_ns, 10_000_000)
    camera = Observer(clock=camera_clock)
    camera_sender = InMemorySender()

    camera.publish(
        camera_sender,
        "image",
        b"prepared image payload",
        new_origin=True,
        origin_id="usb-camera/image",
        metadata={"capture_timestamp_ns": start_ns - 2_000_000},
    )
    _, payload, camera_metadata = camera_sender.outputs.pop()

    sink = BoundedMetricsSink(buckets_ns=[1_000_000, 5_000_000, 10_000_000])
    viewer_clock = MutableClock(start_ns + 5_000_000, 100_000_000)
    viewer = Observer(clock=viewer_clock, sink=sink)
    context = viewer.observe_receive(
        {
            "type": "INPUT",
            "id": "image",
            "value": payload,
            "metadata": camera_metadata,
        }
    )

    viewer_clock.set(start_ns + 13_000_000, 108_000_000)
    forwarding_sender = InMemorySender()
    viewer.publish(
        forwarding_sender,
        "forwarded_image",
        payload,
        parent=context,
        metadata=camera_metadata,
    )
    _, _, forwarded_metadata = forwarding_sender.outputs.pop()

    assert camera_metadata["forge_publish_time_ns"] == start_ns
    assert camera_metadata["forge_origin_time_ns"] == start_ns
    assert forwarded_metadata["forge_publish_time_ns"] == start_ns + 13_000_000
    assert forwarded_metadata["forge_origin_time_ns"] == start_ns
    assert forwarded_metadata["capture_timestamp_ns"] == start_ns - 2_000_000

    print(json.dumps(sink.snapshot(reset=True), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
