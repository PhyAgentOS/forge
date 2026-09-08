from __future__ import annotations

import math
import pickle
import sys
import weakref
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import MappingProxyType

import pytest

FORGE_COMMON_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(FORGE_COMMON_ROOT / "src"))

import forge_common.observability as observability
from forge_common.metrics import BoundedMetricsSink
from forge_common.observability import (
    ObservationMetadata,
    Observer,
    ReceiveContext,
    parse_metadata,
)

HOP = "forge_hop_latency_seconds"
E2E = "forge_e2e_latency_seconds"
TURNAROUND = "forge_node_turnaround_seconds"
VERSION = "forge_obs_version"
PUBLISH = "forge_publish_time_ns"
ORIGIN = "forge_origin_time_ns"
ORIGIN_ID = "forge_origin_id"
MAX_TIME = 2**63 - 1
MISSING = object()


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


INVALID_TIMES = [
    True,
    False,
    -1,
    MAX_TIME + 1,
    10**1000,
    0.0,
    1.0,
    math.nan,
    math.inf,
    -math.inf,
    "1",
    None,
    [],
    {},
    IntSubclass(1),
]
INVALID_IDS = [
    True,
    False,
    1,
    1.0,
    math.nan,
    math.inf,
    None,
    [],
    {},
    "",
    "x" * 257,
    "界" * 86,
    "\ud800",
    StrSubclass("id"),
]


class FakeClock:
    def __init__(self, wall=0, monotonic=0) -> None:
        self.wall = wall
        self.monotonic = monotonic
        self.calls = []

    def time_ns(self):
        self.calls.append("wall")
        if isinstance(self.wall, Exception):
            raise self.wall
        return self.wall

    def monotonic_ns(self):
        self.calls.append("monotonic")
        if isinstance(self.monotonic, Exception):
            raise self.monotonic
        return self.monotonic

    def set(self, wall, monotonic) -> None:
        self.wall = wall
        self.monotonic = monotonic


class RecordingNode:
    def __init__(self, *, on_send=None, error=None) -> None:
        self.calls = []
        self.on_send = on_send
        self.error = error

    def send_output(self, output_id, payload, metadata, /) -> None:
        self.calls.append((output_id, payload, metadata))
        if self.on_send is not None:
            self.on_send()
        if self.error is not None:
            raise self.error


class BrokenSink:
    def __init__(self) -> None:
        self.calls = []

    def observe(self, metric, value_ns, **labels) -> None:
        self.calls.append(("observe", metric))
        raise RuntimeError("histogram unavailable")

    def increment(self, reason, **labels) -> None:
        self.calls.append(("increment", reason))
        raise RuntimeError("counter unavailable")


def wire(published=100, origin=20, origin_id="source") -> dict:
    metadata = {VERSION: 1, PUBLISH: published}
    if origin is not MISSING:
        metadata[ORIGIN] = origin
    if origin_id is not MISSING:
        metadata[ORIGIN_ID] = origin_id
    return metadata


def event(metadata=None, input_id="camera") -> dict:
    return {"type": "INPUT", "id": input_id, "metadata": metadata}


def histograms(sink) -> dict:
    return {
        (row["metric"], row["input_id"], row["output_id"]): row
        for row in sink.snapshot()["histograms"]
    }


def count(sink, reason, *, input_id="", output_id="") -> int:
    return sum(
        row["count"]
        for row in sink.snapshot()["counters"]
        if (row["reason"], row["input_id"], row["output_id"])
        == (reason, input_id, output_id)
    )


def assert_sample(sink, metric, value_ns, *, input_id="", output_id="") -> None:
    row = histograms(sink)[metric, input_id, output_id]
    assert row["count"] == 1
    assert row["sum"] == row["min"] == row["max"] == value_ns / 1_000_000_000
    assert row["buckets"][-1]["count"] == 1


def test_exact_chain_timing_uses_publish_boundary_not_send_return() -> None:
    clock = FakeClock(0, 50_000_000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    node = RecordingNode(on_send=lambda: clock.set(2_000_000, 52_000_000))
    payload = object()
    assert observer.publish(node, "raw", payload, new_origin=True) is None
    assert node.calls == [("raw", payload, wire(0, 0, MISSING))]

    clock.set(3_000_000, 100_000_000)
    parent = observer.observe_receive(event(node.calls[0][2]))
    assert isinstance(parent, ReceiveContext)
    assert parent.received_time_ns == 3_000_000
    assert parent.received_monotonic_ns == 100_000_000
    assert parent.input_id == "camera"
    assert_sample(sink, HOP, 3_000_000, input_id="camera")
    assert_sample(sink, E2E, 3_000_000, input_id="camera")

    clock.set(13_000_000, 110_000_000)
    node.on_send = lambda: clock.set(16_000_000, 999_000_000)
    observer.publish(node, "pose", payload, parent=parent)
    assert len(node.calls) == 2
    assert node.calls[1][1] is payload
    assert node.calls[1][2] == wire(13_000_000, 0, MISSING)
    assert_sample(sink, TURNAROUND, 10_000_000, input_id="camera", output_id="pose")

    clock.set(17_000_000, 1_000_000_000)
    observer.observe_receive(event(node.calls[1][2], "pose"))
    assert_sample(sink, HOP, 4_000_000, input_id="pose")
    assert_sample(sink, E2E, 17_000_000, input_id="pose")
    assert clock.calls == ["wall", "monotonic"] * 4
    assert count(sink, "received", input_id="camera") == 1
    assert count(sink, "published", output_id="raw") == 1
    assert count(sink, "published", output_id="pose") == 1
    assert sink.snapshot()["invalid_inputs"] == 0


def test_unknown_source_and_its_descendants_only_have_hop_timing() -> None:
    clock = FakeClock(100, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    node = RecordingNode()
    observer.publish(node, "out", object())
    assert node.calls[0][2] == {VERSION: 1, PUBLISH: 100}
    clock.set(150, 1100)
    parent = observer.observe_receive(event(node.calls[0][2]))
    assert parent.metadata.origin_time_ns is None
    assert_sample(sink, HOP, 50, input_id="camera")
    assert set(histograms(sink)) == {(HOP, "camera", "")}
    assert count(sink, "missing_origin", input_id="camera") == 1
    clock.set(200, 1150)
    observer.publish(node, "derived", object(), parent=parent)
    assert node.calls[1][2] == {VERSION: 1, PUBLISH: 200}
    assert_sample(sink, TURNAROUND, 50, input_id="camera", output_id="derived")


@pytest.mark.parametrize("timestamp", [0, 1, MAX_TIME])
@pytest.mark.parametrize("origin_id", [None, "id", "x" * 256, "界" * 85])
def test_new_origin_matches_publish_and_works_without_sink(
    timestamp, origin_id
) -> None:
    observer = Observer(clock=FakeClock(timestamp, 0))
    node = RecordingNode()
    observer.publish(node, "out", None, new_origin=True, origin_id=origin_id)
    expected = wire(timestamp, timestamp, MISSING if origin_id is None else origin_id)
    assert node.calls == [("out", None, expected)]
    assert observer.sink_errors == 0


def test_explicit_metadata_is_copied_and_parent_does_not_copy_business_fields() -> None:
    clock = FakeClock(150, 1000)
    observer = Observer(clock=clock)
    incoming = {**wire(), "timestamp": 123, "action": ["move"], "traceparent": "trace"}
    before = deepcopy(incoming)
    parent = observer.observe_receive(event(MappingProxyType(incoming)))
    node = RecordingNode()
    clock.set(200, 1100)
    observer.publish(node, "pose", object(), parent=parent)
    assert node.calls[0][2] == wire(200)
    observer.publish(
        node, "forward", object(), parent=parent, metadata=MappingProxyType(incoming)
    )
    outgoing = node.calls[1][2]
    assert outgoing == {**wire(200), "action": ["move"], "traceparent": "trace"}
    assert type(outgoing) is dict
    assert outgoing is not incoming
    assert incoming == before
    outgoing[PUBLISH] = 999
    outgoing["extra"] = True
    assert incoming == before
    assert parent.metadata == parse_metadata(before)


@pytest.mark.parametrize("new_origin", [False, True])
def test_metadata_alone_never_implicitly_inherits_old_origin(new_origin) -> None:
    incoming = {**wire(), "timestamp": "native-hlc", "business": "keep"}
    before = incoming.copy()
    observer = Observer(clock=FakeClock(500, 600))
    node = RecordingNode()
    observer.publish(node, "out", object(), metadata=incoming, new_origin=new_origin)
    expected = {VERSION: 1, PUBLISH: 500, "business": "keep"}
    if new_origin:
        expected[ORIGIN] = 500
    assert node.calls[0][2] == expected
    assert incoming == before


@pytest.mark.parametrize("origin_id", ["source", MISSING])
@pytest.mark.parametrize(
    "explicit", [wire(999, 888, "stale"), {VERSION: True, ORIGIN_ID: "stale"}]
)
def test_derived_parent_wins_over_explicit_stale_observation_fields(
    origin_id, explicit
) -> None:
    clock = FakeClock(150, 1000)
    observer = Observer(clock=clock)
    parent = observer.observe_receive(event(wire(origin_id=origin_id)))
    metadata = {**explicit, "business": "keep", "timestamp": 999}
    before = metadata.copy()
    clock.set(200, 1100)
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent, metadata=metadata)
    assert node.calls[0][2] == {**wire(200, 20, origin_id), "business": "keep"}
    assert metadata == before


def test_fanout_cache_reuse_and_later_events_do_not_change_selected_parent() -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    incoming = wire()
    payload = object()
    cached = (payload, observer.observe_receive(event(incoming, "cached")))
    incoming.update(wire(999, 999, "mutated"))
    clock.set(220, 1100)
    observer.observe_receive(event(wire(210, 200, "later"), "latest"))
    clock.set(230, 1120)
    observer.observe_receive(event(None, "timer"))
    node = RecordingNode()
    explicit_metadata = {"business": "shared", "timestamp": 123}
    for wall, mono, output in [(250, 1200, "left"), (300, 1300, "right")]:
        clock.set(wall, mono)
        observer.publish(
            node, output, cached[0], parent=cached[1], metadata=explicit_metadata
        )
        assert node.calls[-1][1] is payload
        assert node.calls[-1][2] == {**wire(wall), "business": "shared"}
        assert_sample(
            sink, TURNAROUND, mono - 1000, input_id="cached", output_id=output
        )
    assert node.calls[0][2] is not node.calls[1][2]
    node.calls[0][2][ORIGIN] = 999
    assert node.calls[1][2][ORIGIN] == 20
    assert explicit_metadata == {"business": "shared", "timestamp": 123}
    clock.set(400, 1600)
    observer.publish(node, "reused", cached[0], parent=cached[1])
    assert node.calls[2][2] == wire(400)
    assert_sample(sink, TURNAROUND, 600, input_id="cached", output_id="reused")
    observer.publish(node, "unrelated", object())
    assert node.calls[3][2] == {VERSION: 1, PUBLISH: 400}
    assert (TURNAROUND, "timer", "unrelated") not in histograms(sink)


def test_cached_parent_produces_a_turnaround_sample_for_each_repeated_output() -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    node = RecordingNode()
    for wall, mono in [(200, 1100), (300, 1400), (400, 1800)]:
        clock.set(wall, mono)
        observer.publish(node, "out", None, parent=parent)
    assert [call[2] for call in node.calls] == [wire(200), wire(300), wire(400)]
    row = histograms(sink)[TURNAROUND, "camera", "out"]
    assert row["count"] == 3
    assert row["sum"] == 1300 / 1_000_000_000
    assert row["min"] == 100 / 1_000_000_000
    assert row["max"] == 800 / 1_000_000_000
    assert count(sink, "published", output_id="out") == 3


@pytest.mark.parametrize("reference", ["first", "second", "unknown"])
def test_fanin_uses_only_explicit_reference_not_earliest_or_latest(reference) -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parents = {"first": observer.observe_receive(event(wire(), "first"))}
    clock.set(250, 1100)
    parents["second"] = observer.observe_receive(event(wire(220, 200, "b"), "second"))
    clock.set(300, 1200)
    parents["unknown"] = observer.observe_receive(event(None, "unknown"))
    clock.set(400, 1400)
    node = RecordingNode()
    observer.publish(node, "joined", object(), parent=parents[reference])
    expected = {
        "first": wire(400),
        "second": wire(400, 200, "b"),
        "unknown": {VERSION: 1, PUBLISH: 400},
    }
    assert node.calls[0][2] == expected[reference]
    turnarounds = [key for key in histograms(sink) if key[0] == TURNAROUND]
    assert turnarounds == [(TURNAROUND, reference, "joined")]
    assert_sample(
        sink,
        TURNAROUND,
        {"first": 400, "second": 300, "unknown": 200}[reference],
        input_id=reference,
        output_id="joined",
    )


@pytest.mark.parametrize("payload", [None, b"raw", bytearray(b"raw"), [], {}, object()])
def test_payload_is_passed_by_identity_and_send_is_called_exactly_once(payload) -> None:
    node = RecordingNode()
    Observer(clock=FakeClock(0, 0)).publish(node, "out", payload)
    assert len(node.calls) == 1
    assert node.calls[0][0] == "out"
    assert node.calls[0][1] is payload


@pytest.mark.parametrize("broken_sink", [False, True])
def test_send_failure_propagates_unchanged_without_retry_or_success_metrics(
    broken_sink,
) -> None:
    clock = FakeClock(150, 1000)
    sink = BrokenSink() if broken_sink else BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    failure = RuntimeError("transport failed")
    node = RecordingNode(error=failure, on_send=lambda: clock.set(999, 9999))
    payload = object()
    clock.set(200, 1100)
    with pytest.raises(RuntimeError) as raised:
        observer.publish(node, "out", payload, parent=parent)
    assert raised.value is failure
    assert node.calls == [("out", payload, wire(200))]
    if broken_sink:
        assert ("increment", "publish_failed") in sink.calls
        assert ("increment", "published") not in sink.calls
        assert ("observe", TURNAROUND) not in sink.calls
        assert observer.sink_errors == len(sink.calls)
    else:
        assert count(sink, "publish_failed", output_id="out") == 1
        assert count(sink, "published", output_id="out") == 0
        assert (TURNAROUND, "camera", "out") not in histograms(sink)


def test_sink_exceptions_do_not_block_receive_propagation_or_send() -> None:
    clock = FakeClock(150, 1000)
    sink = BrokenSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    node = RecordingNode()
    clock.set(200, 1100)
    observer.publish(node, "out", None, parent=parent)
    assert node.calls == [("out", None, wire(200))]
    assert sink.calls == [
        ("increment", "received"),
        ("observe", HOP),
        ("observe", E2E),
        ("increment", "published"),
        ("observe", TURNAROUND),
    ]
    assert observer.sink_errors == 5
    observer._sink_errors = MAX_TIME - 1
    clock.set(RuntimeError("clock failed"), 1200)
    observer.publish(node, "out", None, parent=parent)
    assert len(node.calls) == 2
    assert node.calls[1][2] == {}
    assert observer.sink_errors == MAX_TIME


@pytest.mark.parametrize("stage", ["receive", "publish"])
@pytest.mark.parametrize("field", ["wall", "monotonic"])
@pytest.mark.parametrize("bad_value", [RuntimeError("clock failed"), *INVALID_TIMES])
def test_partial_clock_failure_only_disables_affected_measurements(
    stage, field, bad_value
) -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    if stage == "receive":
        setattr(clock, field, bad_value)
    parent = observer.observe_receive(event(wire()))
    assert parent.received_time_ns == (
        None if stage == "receive" and field == "wall" else 150
    )
    assert parent.received_monotonic_ns == (
        None if stage == "receive" and field == "monotonic" else 1000
    )
    clock.set(200, 1100)
    if stage == "publish":
        setattr(clock, field, bad_value)
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    expected_metadata = {} if stage == "publish" and field == "wall" else wire(200)
    assert node.calls == [("out", None, expected_metadata)]
    expected_metrics = set()
    if not (stage == "receive" and field == "wall"):
        expected_metrics.update({(HOP, "camera", ""), (E2E, "camera", "")})
        assert_sample(sink, HOP, 50, input_id="camera")
        assert_sample(sink, E2E, 130, input_id="camera")
    if field != "monotonic":
        expected_metrics.add((TURNAROUND, "camera", "out"))
        assert_sample(sink, TURNAROUND, 100, input_id="camera", output_id="out")
    assert set(histograms(sink)) == expected_metrics
    assert count(sink, "clock_error") == 1
    assert count(sink, "published", output_id="out") == 1
    assert clock.calls == ["wall", "monotonic"] * 2
    assert sink.snapshot()["invalid_inputs"] == 0


def test_both_clocks_can_fail_without_blocking_send() -> None:
    clock = FakeClock(RuntimeError("wall failed"), RuntimeError("monotonic failed"))
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent, metadata={"business": 42})
    assert node.calls == [("out", None, {"business": 42})]
    assert histograms(sink) == {}
    assert count(sink, "clock_error") == 2
    assert count(sink, "published", output_id="out") == 1


@pytest.mark.parametrize("field", ["wall", "monotonic"])
def test_new_origin_with_partial_clock_failure_does_not_invent_timestamps(
    field,
) -> None:
    clock = FakeClock(100, 1000)
    setattr(clock, field, RuntimeError("clock failed"))
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    node = RecordingNode()
    observer.publish(node, "out", None, new_origin=True, origin_id="new")
    expected = {} if field == "wall" else wire(100, 100, "new")
    assert node.calls == [("out", None, expected)]
    assert count(sink, "clock_error") == 1
    assert count(sink, "published", output_id="out") == 1


@pytest.mark.parametrize("version", [2, MAX_TIME])
@pytest.mark.parametrize("with_parent", [False, True])
def test_unknown_version_metadata_is_opaque_and_forwarded_unchanged(
    version, with_parent
) -> None:
    incoming = {
        VERSION: version,
        PUBLISH: {"future": [1]},
        ORIGIN: True,
        ORIGIN_ID: ["future-id"],
        "forge_future_field": ["opaque"],
        "timestamp": "native-hlc",
        "business": "keep",
    }
    before = deepcopy(incoming)
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(incoming))
    assert parent.metadata == ObservationMetadata(
        version=version, issues=("unknown_version",)
    )
    assert histograms(sink) == {}
    clock.set(200, 1100)
    node = RecordingNode()
    observer.publish(
        node,
        "out",
        None,
        parent=parent if with_parent else None,
        metadata=MappingProxyType(incoming),
    )
    assert node.calls[0][2] == {
        key: value for key, value in before.items() if key != "timestamp"
    }
    assert node.calls[0][2] is not incoming
    assert node.calls[0][2][PUBLISH] is incoming[PUBLISH]
    assert incoming == before
    assert count(sink, "unknown_version", input_id="camera") == 1
    assert count(sink, "unknown_version", output_id="out") == 1
    assert all(key[0] == TURNAROUND for key in histograms(sink))


@pytest.mark.parametrize("metadata", [None, {"business": "keep"}, wire()])
def test_unknown_parent_without_opaque_metadata_never_fabricates_v1(metadata) -> None:
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(150, 1000), sink=sink)
    parent = observer.observe_receive(event({VERSION: 2, PUBLISH: 100, ORIGIN: 20}))
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent, metadata=metadata)
    expected = {"business": "keep"} if metadata == {"business": "keep"} else {}
    assert node.calls == [("out", None, expected)]
    assert count(sink, "unknown_version", output_id="out") == 1


def test_parse_metadata_is_pure_and_detached(monkeypatch) -> None:
    def unexpected_io():
        pytest.fail("parse_metadata must not read clocks or process state")

    monkeypatch.setattr(observability.time, "time_ns", unexpected_io)
    monkeypatch.setattr(observability.time, "monotonic_ns", unexpected_io)
    monkeypatch.setattr(observability.os, "getpid", unexpected_io)
    metadata = {**wire(), "timestamp": object(), "business": [1, 2]}
    before = metadata.copy()
    parsed = parse_metadata(MappingProxyType(metadata))
    assert parsed == ObservationMetadata(1, 100, 20, "source", ())
    assert parse_metadata(metadata) == parsed
    assert metadata == before
    metadata.clear()
    assert parsed == ObservationMetadata(1, 100, 20, "source", ())


@pytest.mark.parametrize(
    ("metadata", "issue"),
    [
        (None, "missing_context"),
        ({}, "missing_context"),
        ({"timestamp": 123, "business": 1}, "missing_context"),
        (True, "invalid_metadata"),
        (1, "invalid_metadata"),
        ("metadata", "invalid_metadata"),
        ([], "invalid_metadata"),
    ],
)
def test_missing_and_nonmapping_metadata_are_safe(metadata, issue) -> None:
    assert parse_metadata(metadata) == ObservationMetadata(issues=(issue,))
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(150, 1000), sink=sink)
    parent = observer.observe_receive(event(metadata))
    assert histograms(sink) == {}
    assert count(sink, issue, input_id="camera") == 1
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    assert node.calls == [("out", None, {VERSION: 1, PUBLISH: 150})]


@pytest.mark.parametrize("value", [MISSING, 0, *INVALID_TIMES])
def test_invalid_or_missing_version_disables_all_wire_timing(value) -> None:
    metadata = wire()
    if value is MISSING:
        del metadata[VERSION]
    else:
        metadata[VERSION] = value
    assert parse_metadata(metadata) == ObservationMetadata(issues=("invalid_version",))
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(150, 1000), sink=sink)
    observer.observe_receive(event(metadata))
    assert histograms(sink) == {}
    assert count(sink, "invalid_version", input_id="camera") == 1


@pytest.mark.parametrize("field", [PUBLISH, ORIGIN])
@pytest.mark.parametrize("value", [MISSING, *INVALID_TIMES])
def test_invalid_time_only_disables_dependent_measurements(field, value) -> None:
    metadata = wire()
    if value is MISSING:
        del metadata[field]
    else:
        metadata[field] = value
    parsed = parse_metadata(metadata)
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(150, 1000), sink=sink)
    parent = observer.observe_receive(event(metadata))
    if field == PUBLISH:
        assert parsed == ObservationMetadata(
            1, None, 20, "source", ("invalid_publish_time",)
        )
        assert set(histograms(sink)) == {(E2E, "camera", "")}
        assert_sample(sink, E2E, 130, input_id="camera")
        issue = "invalid_publish_time"
    else:
        issue = "missing_origin" if value is MISSING else "invalid_origin_time"
        assert parsed == ObservationMetadata(
            1, 100, None, None, (issue, "invalid_origin_id")
        )
        assert set(histograms(sink)) == {(HOP, "camera", "")}
        assert_sample(sink, HOP, 50, input_id="camera")
    assert count(sink, issue, input_id="camera") == 1
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    expected = wire(150) if field == PUBLISH else {VERSION: 1, PUBLISH: 150}
    assert node.calls[0][2] == expected


@pytest.mark.parametrize("value", INVALID_IDS)
def test_invalid_optional_id_does_not_invalidate_timing_or_origin(value) -> None:
    metadata = wire(origin_id=value)
    assert parse_metadata(metadata) == ObservationMetadata(
        1, 100, 20, None, ("invalid_origin_id",)
    )
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(metadata))
    assert_sample(sink, HOP, 50, input_id="camera")
    assert_sample(sink, E2E, 130, input_id="camera")
    assert count(sink, "invalid_origin_id", input_id="camera") == 1
    clock.set(200, 1100)
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    assert node.calls[0][2] == wire(200, 20, MISSING)


def test_missing_optional_id_is_not_an_error() -> None:
    assert parse_metadata(wire(origin_id=MISSING)) == ObservationMetadata(1, 100, 20)


@pytest.mark.parametrize("timestamp", [0, MAX_TIME])
def test_time_boundaries_are_legal_and_zero_durations_are_recorded(timestamp) -> None:
    metadata = wire(timestamp, timestamp, MISSING)
    assert parse_metadata(metadata) == ObservationMetadata(1, timestamp, timestamp)
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(timestamp, timestamp), sink=sink)
    parent = observer.observe_receive(event(metadata))
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    assert node.calls[0][2] == metadata
    assert_sample(sink, HOP, 0, input_id="camera")
    assert_sample(sink, E2E, 0, input_id="camera")
    assert_sample(sink, TURNAROUND, 0, input_id="camera", output_id="out")


def test_origin_after_publish_conflict_preserves_valid_hop() -> None:
    metadata = wire(100, 101)
    assert parse_metadata(metadata) == ObservationMetadata(
        1, 100, None, None, ("conflicting_origin", "invalid_origin_id")
    )
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(150, 1000), sink=sink)
    parent = observer.observe_receive(event(metadata))
    assert_sample(sink, HOP, 50, input_id="camera")
    assert set(histograms(sink)) == {(HOP, "camera", "")}
    assert count(sink, "conflicting_origin", input_id="camera") == 1
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    assert node.calls[0][2] == {VERSION: 1, PUBLISH: 150}


def test_wall_clock_rollback_drops_conflicting_origin_but_not_turnaround() -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    clock.set(10, 1100)
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    assert node.calls[0][2] == {VERSION: 1, PUBLISH: 10}
    assert parent.metadata.origin_time_ns == 20
    assert count(sink, "conflicting_origin", output_id="out") == 1
    assert_sample(sink, TURNAROUND, 100, input_id="camera", output_id="out")


@pytest.mark.parametrize("wall", [10, 50])
def test_negative_latencies_are_counted_not_clamped_to_zero(wall) -> None:
    clock = FakeClock(wall, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    assert count(sink, "negative_hop", input_id="camera") == 1
    if wall < 20:
        assert histograms(sink) == {}
        assert count(sink, "negative_e2e", input_id="camera") == 1
    else:
        assert set(histograms(sink)) == {(E2E, "camera", "")}
        assert_sample(sink, E2E, wall - 20, input_id="camera")
    clock.set(200, 999)
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    assert node.calls[0][2] == wire(200)
    assert (TURNAROUND, "camera", "out") not in histograms(sink)
    assert count(sink, "negative_turnaround", input_id="camera", output_id="out") == 1
    assert count(sink, "published", output_id="out") == 1


def test_large_valid_latency_is_not_discarded() -> None:
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(MAX_TIME, MAX_TIME), sink=sink)
    observer.observe_receive(event(wire(0, 0, MISSING)))
    assert_sample(sink, HOP, MAX_TIME, input_id="camera")
    assert_sample(sink, E2E, MAX_TIME, input_id="camera")


def test_default_system_clock_delegates_to_wall_and_monotonic_clocks(
    monkeypatch,
) -> None:
    monkeypatch.setattr(observability.time, "time_ns", lambda: 150)
    monkeypatch.setattr(observability.time, "monotonic_ns", lambda: 1000)
    observer = Observer()
    context = observer.observe_receive(event(wire()))
    assert context.received_time_ns == 150
    assert context.received_monotonic_ns == 1000
    node = RecordingNode()
    observer.publish(node, "out", None, new_origin=True)
    assert node.calls == [("out", None, wire(150, 150, MISSING))]


def test_context_does_not_keep_payload_event_or_business_metadata_alive() -> None:
    class Payload:
        pass

    class Event(dict):
        pass

    payload = Payload()
    business = Payload()
    incoming = Event(**event({**wire(), "business": business}), value=payload)
    references = [weakref.ref(item) for item in (payload, business, incoming)]
    context = Observer(clock=FakeClock(150, 1000)).observe_receive(incoming)
    del incoming, payload, business
    assert all(reference() is None for reference in references)
    assert context.metadata == ObservationMetadata(1, 100, 20, "source")


def test_serialized_context_cannot_reuse_local_monotonic_domain() -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    restored = pickle.loads(pickle.dumps(parent))
    clock.set(200, 1100)
    node = RecordingNode()
    observer.publish(node, "out", None, parent=restored)
    assert node.calls[0][2] == wire(200)
    assert (TURNAROUND, "camera", "out") not in histograms(sink)
    assert count(sink, "invalid_parent", output_id="out") == 1


@pytest.mark.parametrize("domain_change", ["observer", "pid"])
def test_foreign_local_context_inherits_origin_without_monotonic_subtraction(
    monkeypatch, domain_change
) -> None:
    monkeypatch.setattr(observability.os, "getpid", lambda: 100)
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    if domain_change == "observer":
        # Even sharing the same clock object is not permission to reuse the domain.
        observer = Observer(clock=clock, sink=sink)
    else:
        monkeypatch.setattr(observability.os, "getpid", lambda: 101)
    clock.set(200, 1100)
    node = RecordingNode()
    observer.publish(node, "out", None, parent=parent)
    assert node.calls[0][2] == wire(200)
    assert (TURNAROUND, "camera", "out") not in histograms(sink)
    assert count(sink, "invalid_parent", output_id="out") == 1
    assert count(sink, "negative_turnaround", input_id="camera", output_id="out") == 0
    assert count(sink, "published", output_id="out") == 1


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("context", "metadata", ObservationMetadata()),
        ("context", "received_time_ns", 1),
        ("context", "received_monotonic_ns", 1),
        ("context", "input_id", "other"),
        ("context", "_domain", object()),
        ("context", "_pid", 1),
        ("metadata", "version", 2),
        ("metadata", "publish_time_ns", 1),
        ("metadata", "origin_time_ns", 1),
        ("metadata", "origin_id", "other"),
        ("metadata", "issues", ()),
    ],
)
def test_receive_context_and_parsed_metadata_are_frozen(target, field, value) -> None:
    context = Observer(clock=FakeClock(150, 1000)).observe_receive(event(wire()))
    instance = context if target == "context" else context.metadata
    with pytest.raises(FrozenInstanceError):
        setattr(instance, field, value)
    with pytest.raises(FrozenInstanceError):
        delattr(instance, field)


@pytest.mark.parametrize(
    "bad_event",
    [
        {},
        {"type": "STOP"},
        {"type": "ERROR"},
        {"type": "input"},
        {"type": "INPUT", "kind": "timer"},
        {"type": "INPUT", "kind": "custom"},
    ],
)
def test_non_dora_input_is_api_misuse_without_clock_or_sink_side_effects(
    bad_event,
) -> None:
    clock = FakeClock()
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    with pytest.raises(ValueError):
        observer.observe_receive(bad_event)
    assert clock.calls == []
    assert sink.snapshot() == BoundedMetricsSink().snapshot()


@pytest.mark.parametrize("kind", [None, "dora"])
def test_explicit_dora_input_kind_is_accepted(kind) -> None:
    context = Observer(clock=FakeClock()).observe_receive({**event(), "kind": kind})
    assert context.input_id == "camera"


@pytest.mark.parametrize("input_id", [None, True, 1, [], {}, StrSubclass("camera")])
def test_invalid_input_label_does_not_break_timing(input_id) -> None:
    sink = BoundedMetricsSink()
    observer = Observer(clock=FakeClock(150, 1000), sink=sink)
    context = observer.observe_receive(event(wire(), input_id))
    assert context.input_id == ""
    assert_sample(sink, HOP, 50)
    assert_sample(sink, E2E, 130)
    assert sink.snapshot()["invalid_inputs"] == 0


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"new_origin": 1}, TypeError),
        ({"new_origin": None}, TypeError),
        ({"new_origin": "yes"}, TypeError),
        ({"parent": {}}, TypeError),
        ({"parent": object()}, TypeError),
        ({"metadata": []}, TypeError),
        ({"metadata": "metadata"}, TypeError),
        ({"origin_id": "id"}, ValueError),
        ({"new_origin": True, "metadata": {VERSION: 2}}, ValueError),
    ],
)
def test_publish_api_misuse_raises_before_clocks_sink_or_send(options, error) -> None:
    clock = FakeClock()
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    node = RecordingNode()
    before = deepcopy(options.get("metadata"))
    with pytest.raises(error):
        observer.publish(node, "out", None, **options)
    assert options.get("metadata") == before
    assert node.calls == []
    assert clock.calls == []
    assert sink.snapshot() == BoundedMetricsSink().snapshot()


@pytest.mark.parametrize(
    "options", [{"new_origin": True}, {"origin_id": "replacement"}]
)
def test_parent_cannot_be_combined_with_new_origin_or_origin_id(options) -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    before = sink.snapshot()
    clock.calls.clear()
    node = RecordingNode()
    with pytest.raises(ValueError):
        observer.publish(node, "out", None, parent=parent, **options)
    assert node.calls == []
    assert clock.calls == []
    assert sink.snapshot() == before


@pytest.mark.parametrize(
    "replacement",
    [
        {"metadata": object()},
        {"metadata": ObservationMetadata(1, 100, -1)},
        {"metadata": ObservationMetadata(1, 100, 20, "")},
        {"metadata": ObservationMetadata(2, 100)},
        {"metadata": ObservationMetadata(True)},
        {"metadata": ObservationMetadata(IntSubclass(1))},
        {"metadata": ObservationMetadata(1, 100, 20, issues=("not-an-issue",))},
        {"received_time_ns": "bad"},
        {"received_monotonic_ns": "bad"},
        {"input_id": 1},
        {"_domain": None},
        {"_pid": True},
        {"_pid": -1},
    ],
)
def test_corrupted_public_parent_is_rejected_before_side_effects(replacement) -> None:
    clock = FakeClock(150, 1000)
    sink = BoundedMetricsSink()
    observer = Observer(clock=clock, sink=sink)
    parent = observer.observe_receive(event(wire()))
    corrupted = replace(parent, **replacement)
    before = sink.snapshot()
    clock.calls.clear()
    node = RecordingNode()

    with pytest.raises(ValueError, match="invalid receive context"):
        observer.publish(node, "out", None, parent=corrupted)

    assert node.calls == []
    assert clock.calls == []
    assert sink.snapshot() == before


@pytest.mark.parametrize(
    "origin_id", [value for value in INVALID_IDS if value is not None]
)
def test_new_origin_rejects_invalid_explicit_id_before_send(origin_id) -> None:
    clock = FakeClock()
    observer = Observer(clock=clock)
    node = RecordingNode()
    with pytest.raises(ValueError):
        observer.publish(node, "out", None, new_origin=True, origin_id=origin_id)
    assert node.calls == []
    assert clock.calls == []
