from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

FORGE_COMMON_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(FORGE_COMMON_ROOT / "src"))

from forge_common.metrics import (
    BoundedMetricsSink,
    DEFAULT_BUCKETS_NS,
    EVENT_REASONS,
    MAX_BUCKETS,
    MAX_COUNTER,
    MAX_VALUE_NS,
    METRIC_NAMES,
)

HOP = "forge_hop_latency_seconds"
E2E = "forge_e2e_latency_seconds"
TURNAROUND = "forge_node_turnaround_seconds"


class IntSubclass(int):
    pass


class StrSubclass(str):
    pass


def test_empty_snapshot() -> None:
    assert BoundedMetricsSink().snapshot() == {
        "unit": "seconds",
        "histograms": [],
        "counters": [],
        "invalid_inputs": 0,
        "rejected_series": 0,
        "saturated_updates": 0,
    }


@pytest.mark.parametrize("metric", sorted(METRIC_NAMES))
def test_exact_conversion_inclusive_buckets_and_overflow(metric: str) -> None:
    sink = BoundedMetricsSink(buckets_ns=(1_000_000, 2_000_000, 4_000_000))
    values = [0, 999_999, 1_000_000, 1_000_001, 2_000_000, 4_000_000, 4_000_001]
    for value in values:
        assert sink.observe(metric, value, input_id="camera", output_id="pose") is None
    assert sink.snapshot()["histograms"] == [
        {
            "metric": metric,
            "input_id": "camera",
            "output_id": "pose",
            "count": 7,
            "sum": sum(values) / 1_000_000_000,
            "min": 0.0,
            "max": 0.004000001,
            "buckets": [
                {"le": 0.001, "count": 3},
                {"le": 0.002, "count": 5},
                {"le": 0.004, "count": 6},
                {"le": None, "count": 7},
            ],
        }
    ]


def test_default_bucket_formula_and_every_boundary() -> None:
    assert DEFAULT_BUCKETS_NS == tuple(1_000_000 * 2**index for index in range(16))
    sink = BoundedMetricsSink()
    values = [0]
    for bound in DEFAULT_BUCKETS_NS:
        values.extend((bound - 1, bound, bound + 1))
    for value in values:
        sink.observe(HOP, value)
    histogram = sink.snapshot()["histograms"][0]
    assert len(histogram["buckets"]) == len(DEFAULT_BUCKETS_NS) + 1
    for bucket, bound in zip(histogram["buckets"], DEFAULT_BUCKETS_NS):
        assert bucket == {
            "le": bound / 1_000_000_000,
            "count": sum(value <= bound for value in values),
        }
    assert histogram["buckets"][-1] == {"le": None, "count": len(values)}


def test_only_overflow_samples() -> None:
    sink = BoundedMetricsSink(buckets_ns=(1,))
    sink.observe(HOP, MAX_VALUE_NS)
    histogram = sink.snapshot()["histograms"][0]
    assert histogram["buckets"] == [
        {"le": 1e-9, "count": 0},
        {"le": None, "count": 1},
    ]
    assert histogram["min"] == histogram["max"] == histogram["sum"]


def test_metric_and_reason_vocabulary() -> None:
    assert METRIC_NAMES == {HOP, E2E, TURNAROUND}
    assert EVENT_REASONS == {
        "received",
        "published",
        "publish_failed",
        "missing_context",
        "invalid_metadata",
        "invalid_version",
        "unknown_version",
        "invalid_publish_time",
        "missing_origin",
        "invalid_origin_time",
        "invalid_origin_id",
        "invalid_parent",
        "clock_error",
        "negative_hop",
        "negative_e2e",
        "negative_turnaround",
        "sink_error",
        "conflicting_origin",
    }
    sink = BoundedMetricsSink()
    for reason in sorted(EVENT_REASONS):
        assert sink.increment(reason) is None
        sink.increment(reason)
    assert sink.snapshot()["counters"] == [
        {
            "metric": "forge_observability_events_total",
            "reason": reason,
            "input_id": "",
            "output_id": "",
            "count": 2,
        }
        for reason in sorted(EVENT_REASONS)
    ]


def test_series_capacity_is_shared_and_existing_keys_still_work() -> None:
    sink = BoundedMetricsSink(max_series=2)
    sink.observe(HOP, 1, input_id="in", output_id="out")
    sink.increment("received", input_id="in", output_id="out")
    sink.observe(E2E, 1, input_id="in", output_id="out")
    sink.increment("published", input_id="in", output_id="out")
    sink.observe(HOP, 1, input_id="other", output_id="out")
    sink.increment("received", input_id="in", output_id="other")
    sink.observe(HOP, 2, input_id="in", output_id="out")
    sink.increment("received", input_id="in", output_id="out")
    snapshot = sink.snapshot()
    assert snapshot["rejected_series"] == 4
    assert snapshot["invalid_inputs"] == 0
    assert len(snapshot["histograms"]) == len(snapshot["counters"]) == 1
    assert snapshot["histograms"][0]["count"] == 2
    assert snapshot["counters"][0]["count"] == 2


def test_keys_include_both_labels_metric_and_reason() -> None:
    sink = BoundedMetricsSink()
    for input_id, output_id in [("a", "b"), ("b", "a"), ("a", "a")]:
        for metric in METRIC_NAMES:
            sink.observe(metric, 0, input_id=input_id, output_id=output_id)
        for reason in ("received", "published"):
            sink.increment(reason, input_id=input_id, output_id=output_id)
    snapshot = sink.snapshot()
    assert len(snapshot["histograms"]) == 9
    assert len(snapshot["counters"]) == 6


@pytest.mark.parametrize("label", ["input_id", "output_id"])
def test_label_length_limit_does_not_truncate(label: str) -> None:
    sink = BoundedMetricsSink(max_label_length=3)
    sink.observe(HOP, 0, **{label: "端口名"})
    sink.increment("received", **{label: "abc"})
    for value in ["abcd", "端口名字", "x" * 100_000]:
        sink.observe(HOP, 0, **{label: value})
        sink.increment("received", **{label: value})
    snapshot = sink.snapshot()
    assert snapshot["invalid_inputs"] == 6
    assert snapshot["histograms"][0][label] == "端口名"
    assert snapshot["histograms"][0]["count"] == 1
    assert snapshot["counters"][0][label] == "abc"
    assert snapshot["counters"][0]["count"] == 1


@pytest.mark.parametrize(
    "value",
    [
        -1,
        True,
        False,
        0.0,
        1.0,
        math.inf,
        -math.inf,
        math.nan,
        None,
        "1",
        [],
        {},
        MAX_VALUE_NS + 1,
        10**1000,
        IntSubclass(1),
    ],
)
def test_invalid_observations_are_counted_without_allocating_series(value) -> None:
    sink = BoundedMetricsSink()
    sink.observe(HOP, value)
    snapshot = sink.snapshot()
    assert snapshot["invalid_inputs"] == 1
    assert snapshot["histograms"] == []
    assert snapshot["rejected_series"] == 0


@pytest.mark.parametrize("name", ["", "unknown", "x" * 100_000, None, [], {}, 1, True])
def test_invalid_metric_and_reason(name) -> None:
    sink = BoundedMetricsSink()
    sink.observe(name, 0)
    sink.increment(name)
    assert sink.snapshot()["invalid_inputs"] == 2
    assert sink.snapshot()["histograms"] == sink.snapshot()["counters"] == []


def test_string_subclasses_are_not_retained() -> None:
    sink = BoundedMetricsSink()
    sink.observe(StrSubclass(HOP), 1)
    sink.increment(StrSubclass("received"))
    assert sink.snapshot()["invalid_inputs"] == 2


@pytest.mark.parametrize("label", ["input_id", "output_id"])
@pytest.mark.parametrize(
    "value", [None, 1, True, 0.0, math.inf, math.nan, [], {}, StrSubclass("a")]
)
def test_invalid_label_types(label: str, value) -> None:
    sink = BoundedMetricsSink()
    sink.observe(HOP, 1, **{label: value})
    sink.increment("received", **{label: value})
    assert sink.snapshot()["invalid_inputs"] == 2
    assert sink.snapshot()["histograms"] == sink.snapshot()["counters"] == []


@pytest.mark.parametrize("name", ["max_series", "max_label_length"])
@pytest.mark.parametrize(
    "value", [0, -1, True, False, 1.0, math.nan, math.inf, "1", None, MAX_COUNTER + 1]
)
def test_invalid_resource_configuration(name: str, value) -> None:
    with pytest.raises(ValueError):
        BoundedMetricsSink(**{name: value})


@pytest.mark.parametrize(
    "bounds",
    [
        (),
        [],
        None,
        "123",
        {1, 2},
        range(1, 4),
        (0,),
        (-1,),
        (True,),
        (1.0,),
        (math.nan,),
        (math.inf,),
        (-math.inf,),
        ("1",),
        (None,),
        (2, 1),
        (1, 1),
        (MAX_VALUE_NS + 1,),
        (IntSubclass(1),),
        tuple(range(1, MAX_BUCKETS + 2)),
    ],
)
def test_invalid_bucket_configuration(bounds) -> None:
    with pytest.raises(ValueError):
        BoundedMetricsSink(buckets_ns=bounds)


@pytest.mark.parametrize(
    "bounds",
    [(MAX_VALUE_NS - 1, MAX_VALUE_NS), [1, MAX_VALUE_NS - 1, MAX_VALUE_NS]],
)
def test_bucket_bounds_must_remain_distinct_in_float_seconds(bounds) -> None:
    assert bounds[-2] < bounds[-1]
    assert bounds[-2] / 1_000_000_000 == bounds[-1] / 1_000_000_000
    with pytest.raises(ValueError, match="strictly increasing.*float seconds"):
        BoundedMetricsSink(buckets_ns=bounds)


@pytest.mark.parametrize(
    "bounds", [(MAX_VALUE_NS,), (MAX_VALUE_NS - 4096, MAX_VALUE_NS)]
)
def test_max_bucket_bound_is_allowed_when_float_seconds_are_distinct(bounds) -> None:
    sink = BoundedMetricsSink(buckets_ns=bounds)
    for bound in bounds:
        sink.observe(HOP, bound)
    snapshot = sink.snapshot()
    assert snapshot["histograms"][0]["buckets"] == [
        {"le": bound / 1_000_000_000, "count": index + 1}
        for index, bound in enumerate(bounds)
    ] + [{"le": None, "count": len(bounds)}]
    assert json.loads(json.dumps(snapshot, allow_nan=False)) == snapshot


def test_bucket_configuration_is_copied_and_size_is_bounded() -> None:
    bounds = list(range(1, MAX_BUCKETS + 1))
    sink = BoundedMetricsSink(buckets_ns=bounds)
    bounds[:] = [1000]
    for _ in range(10_000):
        sink.observe(HOP, 1)
    histogram = sink.snapshot()["histograms"][0]
    assert len(histogram["buckets"]) == MAX_BUCKETS + 1
    assert histogram["buckets"][0] == {"le": 1e-9, "count": 10_000}
    assert len(sink._histograms[(HOP, "", "")].bins) == MAX_BUCKETS + 1


def test_invalid_input_takes_precedence_over_series_capacity() -> None:
    sink = BoundedMetricsSink(max_series=1)
    sink.increment("received")
    sink.observe(HOP, -1)
    sink.increment("unknown")
    assert sink.snapshot()["invalid_inputs"] == 2
    assert sink.snapshot()["rejected_series"] == 0


def test_reset_clears_interval_and_releases_series_budget() -> None:
    sink = BoundedMetricsSink(max_series=2)
    sink.observe(HOP, 500)
    sink.increment("received")
    sink.increment("published")
    sink.observe(HOP, -1)
    before = sink.snapshot()
    assert sink.snapshot(False) == before
    assert sink.snapshot(reset=True) == before
    empty = BoundedMetricsSink().snapshot()
    assert sink.snapshot() == sink.snapshot(reset=True) == empty
    sink.observe(E2E, 0)
    sink.increment("published")
    after = sink.snapshot()
    assert after["histograms"][0]["metric"] == E2E
    assert after["histograms"][0]["count"] == 1
    assert after["histograms"][0]["min"] == after["histograms"][0]["max"] == 0.0
    assert after["counters"][0]["reason"] == "published"
    assert after["invalid_inputs"] == after["rejected_series"] == 0


@pytest.mark.parametrize("value", [0, 1, "yes", None, [], math.nan])
def test_reset_requires_bool_and_does_not_clear_on_error(value) -> None:
    sink = BoundedMetricsSink()
    sink.observe(HOP, 1)
    with pytest.raises(ValueError):
        sink.snapshot(reset=value)
    assert sink.snapshot()["histograms"][0]["count"] == 1


def test_snapshot_is_fully_detached() -> None:
    sink = BoundedMetricsSink()
    sink.observe(HOP, 1)
    sink.increment("received")
    expected = sink.snapshot()
    detached = sink.snapshot()
    detached["histograms"][0]["buckets"][0]["count"] = -1
    detached["histograms"][0]["count"] = -1
    detached["counters"][0]["count"] = -1
    detached["histograms"].clear()
    detached["invalid_inputs"] = -1
    assert sink.snapshot() == expected
    sink.observe(HOP, 2)
    assert expected["histograms"][0]["count"] == 1


def test_numeric_state_saturates_and_exports_finite_seconds() -> None:
    sink = BoundedMetricsSink(buckets_ns=(MAX_VALUE_NS,))
    sink.observe(HOP, MAX_VALUE_NS)
    sink.increment("received")
    # Seed the last representable update without running 2**63 observations.
    histogram = sink._histograms[(HOP, "", "")]
    histogram.count = MAX_COUNTER - 1
    histogram.bins[0] = MAX_COUNTER - 1
    histogram.sum_ns = (MAX_COUNTER - 1) * MAX_VALUE_NS
    sink._counters[("received", "", "")] = MAX_COUNTER - 1
    sink.observe(HOP, MAX_VALUE_NS)
    sink.increment("received")
    before = sink.snapshot()
    sink.observe(HOP, 0)
    sink.increment("received")
    after = sink.snapshot()
    assert after["saturated_updates"] == 2
    assert after["histograms"] == before["histograms"]
    assert after["counters"] == before["counters"]
    exported = after["histograms"][0]
    assert exported["count"] == MAX_COUNTER
    assert exported["buckets"][-1]["count"] == MAX_COUNTER
    assert exported["sum"] == (MAX_COUNTER * MAX_VALUE_NS) / 1_000_000_000
    for value in [
        exported["sum"],
        exported["min"],
        exported["max"],
        exported["buckets"][0]["le"],
    ]:
        assert isinstance(value, float)
        assert math.isfinite(value)
    assert json.loads(json.dumps(after, allow_nan=False)) == after
    sink.snapshot(reset=True)
    assert sink.snapshot()["saturated_updates"] == 0


def test_diagnostic_totals_saturate_without_new_label_state() -> None:
    sink = BoundedMetricsSink(max_series=1)
    sink.increment("received")
    sink._invalid_inputs = MAX_COUNTER - 1
    sink._rejected_series = MAX_COUNTER - 1
    sink._saturated_updates = MAX_COUNTER - 1
    sink._counters[("received", "", "")] = MAX_COUNTER
    for index in range(10):
        sink.observe(HOP, -1, input_id=str(index))
        sink.increment("unknown", input_id=str(index))
        sink.observe(HOP, 0, input_id=str(index))
        sink.increment("published", input_id=str(index))
        sink.increment("received")
    snapshot = sink.snapshot()
    assert snapshot["invalid_inputs"] == MAX_COUNTER
    assert snapshot["rejected_series"] == MAX_COUNTER
    assert snapshot["saturated_updates"] == MAX_COUNTER
    assert len(snapshot["counters"]) == 1
    assert snapshot["histograms"] == []


def test_concurrent_updates_and_capacity() -> None:
    sink = BoundedMetricsSink(max_series=3, buckets_ns=(5, 10))
    sink.observe(HOP, 0)
    sink.increment("received")
    barrier = Barrier(8)

    def update(worker: int) -> None:
        barrier.wait(timeout=10)
        for index in range(500):
            sink.observe(HOP, 10)
            sink.increment("received")
            sink.increment("published", input_id=f"{worker}:{index}")
            sink.observe(HOP, -1)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update, range(8)))
    snapshot = sink.snapshot()
    assert snapshot["histograms"][0]["count"] == 4001
    assert snapshot["histograms"][0]["buckets"] == [
        {"le": 5e-9, "count": 1},
        {"le": 1e-8, "count": 4001},
        {"le": None, "count": 4001},
    ]
    assert snapshot["counters"][0]["count"] == 4001
    assert len(snapshot["counters"]) == 2
    assert snapshot["rejected_series"] == 3999
    assert snapshot["invalid_inputs"] == 4000


def test_concurrent_reset_never_loses_or_duplicates_samples() -> None:
    sink = BoundedMetricsSink(buckets_ns=(1, 10))
    barrier = Barrier(5)

    def update() -> None:
        barrier.wait(timeout=10)
        for _ in range(1000):
            sink.observe(HOP, 5)
            sink.increment("received")
            sink.increment("unknown")

    def collect() -> list[dict]:
        barrier.wait(timeout=10)
        return [sink.snapshot(reset=True) for _ in range(300)]

    with ThreadPoolExecutor(max_workers=5) as executor:
        writers = [executor.submit(update) for _ in range(4)]
        reader = executor.submit(collect)
        for writer in writers:
            writer.result(timeout=20)
        intervals = reader.result(timeout=20)
    intervals.append(sink.snapshot(reset=True))
    histograms = [
        histogram for interval in intervals for histogram in interval["histograms"]
    ]
    counters = [counter for interval in intervals for counter in interval["counters"]]
    assert sum(histogram["count"] for histogram in histograms) == 4000
    assert sum(histogram["sum"] for histogram in histograms) == pytest.approx(
        4000 * 5e-9
    )
    assert sum(counter["count"] for counter in counters) == 4000
    assert sum(interval["invalid_inputs"] for interval in intervals) == 4000
    for histogram in histograms:
        assert histogram["buckets"][0]["count"] == 0
        assert histogram["buckets"][1]["count"] == histogram["count"]
        assert histogram["buckets"][-1]["count"] == histogram["count"]
    assert sink.snapshot() == BoundedMetricsSink().snapshot()
