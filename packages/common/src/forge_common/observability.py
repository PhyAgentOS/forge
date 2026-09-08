"""Application publish/receive timing without importing Dora or a metrics SDK.

Use one Observer per local timing domain. Payloads and business metadata remain
caller-owned; an explicit parent selects provenance, not business metadata.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol

__all__ = [
    "Clock",
    "MetricsSink",
    "ObservationMetadata",
    "Observer",
    "ReceiveContext",
    "Sender",
    "SystemClock",
    "parse_metadata",
]

VERSION_KEY = "forge_obs_version"
PUBLISH_KEY = "forge_publish_time_ns"
ORIGIN_KEY = "forge_origin_time_ns"
ORIGIN_ID_KEY = "forge_origin_id"
VERSION = 1
MAX_TIME_NS = 2**63 - 1
MAX_ORIGIN_ID_BYTES = 256
_KEYS = (VERSION_KEY, PUBLISH_KEY, ORIGIN_KEY, ORIGIN_ID_KEY)
_PARSER_ISSUES = frozenset(
    {
        "missing_context",
        "invalid_metadata",
        "invalid_version",
        "unknown_version",
        "invalid_publish_time",
        "missing_origin",
        "invalid_origin_time",
        "conflicting_origin",
        "invalid_origin_id",
    }
)


class Clock(Protocol):
    """Unix and local monotonic clocks; both return nonnegative int64 nanoseconds."""

    def time_ns(self) -> int: ...

    def monotonic_ns(self) -> int: ...


class SystemClock:
    def time_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


class MetricsSink(Protocol):
    """Bounded, nonblocking-in-practice local aggregation; never network export."""

    def observe(
        self, metric: str, value_ns: int, *, input_id: str = "", output_id: str = ""
    ) -> None: ...

    def increment(
        self, reason: str, *, input_id: str = "", output_id: str = ""
    ) -> None: ...


class Sender(Protocol):
    """Structural Dora binding: the underlying send is called exactly once."""

    def send_output(
        self, output_id: str, data: Any, metadata: dict[str, Any], /
    ) -> None: ...


def _valid_time(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_TIME_NS


def _valid_origin_id(value: object) -> bool:
    if type(value) is not str or not 1 <= len(value) <= MAX_ORIGIN_ID_BYTES:
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_ORIGIN_ID_BYTES
    except UnicodeError:
        return False


def _unknown_version(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_TIME_NS and value != VERSION


@dataclass(frozen=True, slots=True)
class ObservationMetadata:
    """Validated independent timing fields; no mutable input mapping is retained."""

    version: int | None = None
    publish_time_ns: int | None = None
    origin_time_ns: int | None = None
    origin_id: str | None = None
    issues: tuple[str, ...] = ()

    @property
    def unknown_version(self) -> bool:
        return _unknown_version(self.version)


def parse_metadata(metadata: object) -> ObservationMetadata:
    """Parse v1 without exceptions for ordinary malformed wire values.

    A bad origin does not discard valid hop timing. An optional bad ID does not
    discard valid timing. Unknown versions are not decoded. Zero is a valid
    timestamp, not a missing-value sentinel. This function performs no I/O.
    """
    if metadata is None:
        return ObservationMetadata(issues=("missing_context",))
    if not isinstance(metadata, Mapping):
        return ObservationMetadata(issues=("invalid_metadata",))
    if not any(key in metadata for key in _KEYS):
        return ObservationMetadata(issues=("missing_context",))
    version = metadata.get(VERSION_KEY)
    if _unknown_version(version):
        return ObservationMetadata(version=version, issues=("unknown_version",))
    if type(version) is not int or version != VERSION:
        return ObservationMetadata(issues=("invalid_version",))

    issues: list[str] = []
    published = metadata.get(PUBLISH_KEY)
    if not _valid_time(published):
        issues.append("invalid_publish_time")
        published = None
    origin = metadata.get(ORIGIN_KEY)
    if ORIGIN_KEY not in metadata:
        issues.append("missing_origin")
        origin = None
    elif not _valid_time(origin):
        issues.append("invalid_origin_time")
        origin = None
    if origin is not None and published is not None and origin > published:
        issues.append("conflicting_origin")
        origin = None
    origin_id = metadata.get(ORIGIN_ID_KEY)
    if ORIGIN_ID_KEY in metadata and (
        origin is None or not _valid_origin_id(origin_id)
    ):
        issues.append("invalid_origin_id")
        origin_id = None
    return ObservationMetadata(VERSION, published, origin, origin_id, tuple(issues))


@dataclass(frozen=True, slots=True)
class ReceiveContext:
    """Keep alongside the actual payload through caching and asynchronous work.

    Obtain this value from observe_receive, not by constructing or transporting
    local clock fields. Another Observer may inherit its origin, but cannot use
    its monotonic timestamp for turnaround. No payload or business metadata is
    retained. The domain token also prevents reuse after serialization.
    """

    metadata: ObservationMetadata
    received_time_ns: int | None
    received_monotonic_ns: int | None
    input_id: str
    _domain: object = field(repr=False, compare=False)
    _pid: int = field(repr=False)


def _valid_parent(parent: ReceiveContext) -> bool:
    metadata = parent.metadata
    if type(metadata) is not ObservationMetadata:
        return False
    if type(metadata.issues) is not tuple or any(
        type(issue) is not str or issue not in _PARSER_ISSUES
        for issue in metadata.issues
    ):
        return False
    if metadata.version is None or _unknown_version(metadata.version):
        if any(
            value is not None
            for value in (
                metadata.publish_time_ns,
                metadata.origin_time_ns,
                metadata.origin_id,
            )
        ):
            return False
    elif type(metadata.version) is int and metadata.version == VERSION:
        if metadata.publish_time_ns is not None and not _valid_time(
            metadata.publish_time_ns
        ):
            return False
        if metadata.origin_time_ns is not None and not _valid_time(
            metadata.origin_time_ns
        ):
            return False
        if (
            metadata.publish_time_ns is not None
            and metadata.origin_time_ns is not None
            and metadata.origin_time_ns > metadata.publish_time_ns
        ):
            return False
        if metadata.origin_id is not None and (
            metadata.origin_time_ns is None or not _valid_origin_id(metadata.origin_id)
        ):
            return False
    else:
        return False
    return (
        (parent.received_time_ns is None or _valid_time(parent.received_time_ns))
        and (
            parent.received_monotonic_ns is None
            or _valid_time(parent.received_monotonic_ns)
        )
        and type(parent.input_id) is str
        and type(parent._domain) is object
        and type(parent._pid) is int
        and parent._pid >= 0
    )


class Observer:
    """Explicit provenance and best-effort metrics around application boundaries.

    No sink means propagation-only, not disabled propagation. Sink exceptions are
    isolated and counted in sink_errors; clocks that fail or return invalid
    values make only the affected measurements unavailable. Programmer errors
    (invalid arguments, a non-INPUT event, incompatible origin options) raise.
    Underlying send failures always propagate unchanged, without retries.
    """

    def __init__(
        self, *, clock: Clock | None = None, sink: MetricsSink | None = None
    ) -> None:
        self._clock = clock if clock is not None else SystemClock()
        self._sink = sink
        self._domain = object()
        self._sink_errors = 0
        self._error_lock = Lock()

    @property
    def sink_errors(self) -> int:
        """Saturating local diagnostic even when the sink itself is broken."""
        with self._error_lock:
            return self._sink_errors

    def _emit(self, method: str, *args: Any, **kwargs: Any) -> None:
        if self._sink is None:
            return
        try:
            getattr(self._sink, method)(*args, **kwargs)
        except Exception:  # noqa: BLE001 - third-party telemetry must not break sends.
            with self._error_lock:
                self._sink_errors = min(MAX_TIME_NS, self._sink_errors + 1)
            # Do not recursively report to a failing sink or log on the hot path.

    def _count(self, reason: str, *, input_id: str = "", output_id: str = "") -> None:
        self._emit("increment", reason, input_id=input_id, output_id=output_id)

    def _clocks(self) -> tuple[int | None, int | None]:
        values: list[int | None] = []
        failed = False
        for method in ("time_ns", "monotonic_ns"):
            try:
                value = getattr(self._clock, method)()
                if not _valid_time(value):
                    value = None
                    failed = True
            except Exception:  # noqa: BLE001 - injected clock failures disable timing only.
                value = None
                failed = True
            values.append(value)
        if failed:
            self._count("clock_error")
        return values[0], values[1]

    def _duration(
        self,
        metric: str,
        negative_reason: str,
        end: int | None,
        start: int | None,
        *,
        input_id: str = "",
        output_id: str = "",
    ) -> None:
        if end is None or start is None:
            return
        if end < start:
            self._count(negative_reason, input_id=input_id, output_id=output_id)
            return
        self._emit(
            "observe", metric, end - start, input_id=input_id, output_id=output_id
        )

    def observe_receive(self, event: Mapping[str, Any]) -> ReceiveContext:
        """Call immediately after obtaining a Dora INPUT, before business work."""
        if event.get("type") != "INPUT" or event.get("kind") not in (None, "dora"):
            raise ValueError("observe_receive requires a Dora INPUT event")
        wall, monotonic = self._clocks()
        input_id = event.get("id", "")
        if type(input_id) is not str:
            input_id = ""
        metadata = parse_metadata(event.get("metadata"))
        ctx = ReceiveContext(
            metadata, wall, monotonic, input_id, self._domain, os.getpid()
        )
        self._count("received", input_id=input_id)
        for issue in metadata.issues:
            self._count(issue, input_id=input_id)
        self._duration(
            "forge_hop_latency_seconds",
            "negative_hop",
            wall,
            metadata.publish_time_ns,
            input_id=input_id,
        )
        self._duration(
            "forge_e2e_latency_seconds",
            "negative_e2e",
            wall,
            metadata.origin_time_ns,
            input_id=input_id,
        )
        return ctx

    def publish(
        self,
        node: Sender,
        output_id: str,
        payload: Any,
        *,
        parent: ReceiveContext | None = None,
        new_origin: bool = False,
        origin_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Send once; provenance is explicit and business metadata is opt-in.

        Known v1 timing in the supplied metadata is replaced, never implicitly
        inherited. Without parent/new_origin only hop timing is generated.
        Unknown versions supplied in metadata are forwarded opaquely (except the
        Dora receive-layer timestamp). An unknown-version parent without opaque
        metadata is sent uninstrumented, not relabelled as v1.
        """
        if type(new_origin) is not bool:
            raise TypeError("new_origin must be bool")
        if parent is not None and not isinstance(parent, ReceiveContext):
            raise TypeError("parent must be a ReceiveContext")
        if parent is not None and not _valid_parent(parent):
            raise ValueError("parent contains invalid receive context state")
        if new_origin and parent is not None:
            raise ValueError("parent and new_origin are mutually exclusive")
        if origin_id is not None and (
            not new_origin or not _valid_origin_id(origin_id)
        ):
            raise ValueError("origin_id requires new_origin and 1..256 UTF-8 bytes")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping or None")
        outgoing = dict(metadata) if metadata is not None else {}
        opaque = _unknown_version(outgoing.get(VERSION_KEY))
        unknown_parent = parent is not None and parent.metadata.unknown_version
        if new_origin and opaque:
            raise ValueError("cannot create a v1 origin in unknown-version metadata")
        outgoing.pop("timestamp", None)
        if not opaque:
            for key in _KEYS:
                outgoing.pop(key, None)
        wall, monotonic = self._clocks()
        if not opaque and not unknown_parent and wall is not None:
            outgoing[VERSION_KEY] = VERSION
            outgoing[PUBLISH_KEY] = wall
            if new_origin:
                outgoing[ORIGIN_KEY] = wall
                if origin_id is not None:
                    outgoing[ORIGIN_ID_KEY] = origin_id
            elif parent is not None and parent.metadata.origin_time_ns is not None:
                origin = parent.metadata.origin_time_ns
                if origin <= wall:
                    outgoing[ORIGIN_KEY] = origin
                    if parent.metadata.origin_id is not None:
                        outgoing[ORIGIN_ID_KEY] = parent.metadata.origin_id
                else:
                    self._count("conflicting_origin", output_id=output_id)
        if opaque or unknown_parent:
            self._count("unknown_version", output_id=output_id)
        try:
            node.send_output(output_id, payload, outgoing)
        except Exception:
            self._count("publish_failed", output_id=output_id)
            raise
        self._count("published", output_id=output_id)
        if parent is not None:
            if parent._domain is not self._domain or parent._pid != os.getpid():
                self._count("invalid_parent", output_id=output_id)
            else:
                self._duration(
                    "forge_node_turnaround_seconds",
                    "negative_turnaround",
                    monotonic,
                    parent.received_monotonic_ns,
                    input_id=parent.input_id,
                    output_id=output_id,
                )
