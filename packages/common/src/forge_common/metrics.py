"""Dependency-free, bounded latency histograms and observability event counters.

Only aggregate state is retained; this module starts no threads and performs no
I/O. Nanoseconds are used internally so bucket comparisons remain exact. Public
snapshots use seconds and contain only JSON-compatible, finite numeric values.
Bucket bounds must remain strictly increasing after conversion to float seconds;
configurations whose bounds collapse through rounding raise ValueError during
construction. A single MAX_VALUE_NS bound is valid.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from threading import Lock
from typing import Any

__all__ = [
    "DEFAULT_BUCKETS_NS",
    "EVENT_REASONS",
    "MAX_BUCKETS",
    "MAX_COUNTER",
    "MAX_VALUE_NS",
    "METRIC_NAMES",
    "BoundedMetricsSink",
]

METRIC_NAMES = frozenset(
    {
        "forge_hop_latency_seconds",
        "forge_e2e_latency_seconds",
        "forge_node_turnaround_seconds",
    }
)
EVENT_REASONS = frozenset(
    {
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
)
# Sixteen inclusive upper bounds: 1 ms, 2 ms, ..., 32.768 s, plus overflow.
DEFAULT_BUCKETS_NS = tuple(1_000_000 * 2**index for index in range(16))
MAX_BUCKETS = 64  # Finite bounds, excluding the mandatory overflow bucket.
MAX_COUNTER = 2**63 - 1
MAX_VALUE_NS = 2**63 - 1
_NS_PER_SECOND = 1_000_000_000
_EVENT_METRIC = "forge_observability_events_total"


@dataclass(slots=True)
class _Histogram:
    bins: list[int]
    count: int = 0
    sum_ns: int = 0
    min_ns: int = MAX_VALUE_NS
    max_ns: int = 0


class BoundedMetricsSink:
    """Thread-safe sink with a shared histogram/counter series budget.

    Args:
        max_series: Positive integer limit across BOTH kinds of series. A
            histogram key is (metric, input_id, output_id); a counter key is
            (reason, input_id, output_id). Buckets do not consume separate keys.
        max_label_length: Positive maximum character length of each port label.
            Oversized labels are rejected, not truncated or retained.
        buckets_ns: A list or tuple of 1..MAX_BUCKETS strictly increasing integer
            upper bounds in [1, MAX_VALUE_NS], also strictly increasing when
            converted to float seconds. The configuration is copied and fixed
            for the lifetime of the sink. Overflow is added automatically.

    Configuration errors raise ValueError, including bools and incorrect types.
    observe/increment instead discard invalid input and increment invalid_inputs.
    Only exact built-in str/int types are accepted (not bool or subclasses).
    Latencies must be in [0, MAX_VALUE_NS], approximately 292 years. This bound
    keeps sums finite on export and prevents arbitrary-size retained integers.

    New keys at capacity are discarded and counted in rejected_series (rejected
    calls, not distinct keys); existing keys still accept updates. Counts and the
    three unlabelled diagnostic totals saturate at MAX_COUNTER. Once a series
    reaches MAX_COUNTER samples/events, further updates are discarded and counted
    in saturated_updates, preserving histogram count/bucket/sum consistency.

    Memory is O(max_series * (len(buckets_ns) + max_label_length)); there is no
    sample log, dynamic reason vocabulary, or per-rejected-label state. All
    updates and snapshots are serialized by a single lock. reset releases the
    series budget for the next interval, rather than reserving old identities.
    """

    def __init__(
        self,
        *,
        max_series: int = 1024,
        max_label_length: int = 128,
        buckets_ns: tuple[int, ...] | list[int] = DEFAULT_BUCKETS_NS,
    ) -> None:
        for name, value in (
            ("max_series", max_series),
            ("max_label_length", max_label_length),
        ):
            if type(value) is not int or not 1 <= value <= MAX_COUNTER:
                raise ValueError(f"{name} must be an integer in [1, MAX_COUNTER]")
        if (
            type(buckets_ns) not in (tuple, list)
            or not 1 <= len(buckets_ns) <= MAX_BUCKETS
        ):
            raise ValueError("buckets_ns must be a list/tuple of 1..MAX_BUCKETS bounds")
        bounds = tuple(buckets_ns)
        previous = 0
        previous_seconds = 0.0
        for bound in bounds:
            if type(bound) is not int or not previous < bound <= MAX_VALUE_NS:
                raise ValueError(
                    "bucket bounds must be strictly increasing integers "
                    "in [1, MAX_VALUE_NS]"
                )
            seconds = bound / _NS_PER_SECOND
            if not previous_seconds < seconds:
                raise ValueError(
                    "bucket bounds must remain strictly increasing in float seconds"
                )
            previous = bound
            previous_seconds = seconds

        self._max_series = max_series
        self._max_label_length = max_label_length
        self._bounds = bounds
        self._lock = Lock()
        self._histograms: dict[tuple[str, str, str], _Histogram] = {}
        self._counters: dict[tuple[str, str, str], int] = {}
        self._invalid_inputs = 0
        self._rejected_series = 0
        self._saturated_updates = 0

    def _valid_labels(self, input_id: str, output_id: str) -> bool:
        return (
            type(input_id) is str
            and type(output_id) is str
            and len(input_id) <= self._max_label_length
            and len(output_id) <= self._max_label_length
        )

    def _at_capacity(self) -> bool:
        return len(self._histograms) + len(self._counters) >= self._max_series

    def observe(
        self,
        metric: str,
        value_ns: int,
        *,
        input_id: str = "",
        output_id: str = "",
    ) -> None:
        """Record a nonnegative integer latency in nanoseconds, or count a drop.

        Only METRIC_NAMES are supported. A sample equal to a bound belongs to
        that bucket (and all higher buckets in the cumulative snapshot).
        """
        with self._lock:
            if (
                type(metric) is not str
                or metric not in METRIC_NAMES
                or type(value_ns) is not int
                or not 0 <= value_ns <= MAX_VALUE_NS
                or not self._valid_labels(input_id, output_id)
            ):
                self._invalid_inputs = min(MAX_COUNTER, self._invalid_inputs + 1)
                return
            key = (metric, input_id, output_id)
            histogram = self._histograms.get(key)
            if histogram is None:
                if self._at_capacity():
                    self._rejected_series = min(MAX_COUNTER, self._rejected_series + 1)
                    return
                histogram = _Histogram([0] * (len(self._bounds) + 1))
                self._histograms[key] = histogram
            if histogram.count == MAX_COUNTER:
                self._saturated_updates = min(MAX_COUNTER, self._saturated_updates + 1)
                return
            histogram.bins[bisect_left(self._bounds, value_ns)] += 1
            histogram.count += 1
            histogram.sum_ns += value_ns
            histogram.min_ns = min(histogram.min_ns, value_ns)
            histogram.max_ns = max(histogram.max_ns, value_ns)

    def increment(
        self, reason: str, *, input_id: str = "", output_id: str = ""
    ) -> None:
        """Increment forge_observability_events_total for a fixed EVENT_REASONS key."""
        with self._lock:
            if (
                type(reason) is not str
                or reason not in EVENT_REASONS
                or not self._valid_labels(input_id, output_id)
            ):
                self._invalid_inputs = min(MAX_COUNTER, self._invalid_inputs + 1)
                return
            key = (reason, input_id, output_id)
            count = self._counters.get(key)
            if count is None:
                if self._at_capacity():
                    self._rejected_series = min(MAX_COUNTER, self._rejected_series + 1)
                    return
                count = 0
            if count == MAX_COUNTER:
                self._saturated_updates = min(MAX_COUNTER, self._saturated_updates + 1)
                return
            self._counters[key] = count + 1

    def snapshot(self, reset: bool = False) -> dict[str, Any]:
        """Return a detached, atomic, JSON-encodable aggregate snapshot.

        The result has unit="seconds", histograms and counters lists, and the
        unlabelled integer totals invalid_inputs, rejected_series and
        saturated_updates. Each histogram includes metric, input_id, output_id,
        count, sum, min, max and buckets. sum/min/max and finite bucket le bounds
        are floating-point seconds; bucket counts are cumulative and inclusive.
        The final bucket has le=None (JSON null), meaning positive infinity, and
        its count always equals the histogram count. No NaN/Infinity is emitted.
        Each counter contains metric, reason, input_id, output_id and count.

        With reset=True the returned interval is atomically cleared, including
        diagnostics and label identities. A subsequent interval with no samples
        has empty lists, not stale quantiles or zero-valued historical series.
        No quantiles are calculated; consumers can derive them from the buckets.
        reset must be an actual bool; an invalid argument raises ValueError.
        """
        if type(reset) is not bool:
            raise ValueError("reset must be a bool")
        with self._lock:
            histograms = []
            for (metric, input_id, output_id), histogram in self._histograms.items():
                cumulative = 0
                buckets = []
                for bound, count in zip(self._bounds, histogram.bins):
                    cumulative += count
                    buckets.append({"le": bound / _NS_PER_SECOND, "count": cumulative})
                buckets.append({"le": None, "count": histogram.count})
                histograms.append(
                    {
                        "metric": metric,
                        "input_id": input_id,
                        "output_id": output_id,
                        "count": histogram.count,
                        "sum": histogram.sum_ns / _NS_PER_SECOND,
                        "min": histogram.min_ns / _NS_PER_SECOND,
                        "max": histogram.max_ns / _NS_PER_SECOND,
                        "buckets": buckets,
                    }
                )
            counters = [
                {
                    "metric": _EVENT_METRIC,
                    "reason": reason,
                    "input_id": input_id,
                    "output_id": output_id,
                    "count": count,
                }
                for (reason, input_id, output_id), count in self._counters.items()
            ]
            result = {
                "unit": "seconds",
                "histograms": histograms,
                "counters": counters,
                "invalid_inputs": self._invalid_inputs,
                "rejected_series": self._rejected_series,
                "saturated_updates": self._saturated_updates,
            }
            if reset:
                self._histograms.clear()
                self._counters.clear()
                self._invalid_inputs = 0
                self._rejected_series = 0
                self._saturated_updates = 0
            return result
