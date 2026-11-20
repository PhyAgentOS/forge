"""Validated values shared by Forge ToolEndpoint implementations and bindings."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias


ToolSemantics: TypeAlias = Literal["query", "action", "session"]
EndpointState: TypeAlias = Literal["ready", "busy", "degraded", "unavailable"]
ExecutionPhase: TypeAlias = Literal[
    "accepted",
    "running",
    "stopping",
    "completed",
    "failed",
    "cancelled",
    "stopped",
    "unknown",
]
ToolEventType: TypeAlias = Literal[
    "progress",
    "heartbeat",
    "executor_completed",
    "executor_failed",
    "cancelled",
    "stopped",
]
ToolResultStatus: TypeAlias = Literal[
    "succeeded",
    "failed",
    "cancelled",
    "stopped",
    "unknown",
]
ToolResultResponseStatus: TypeAlias = Literal["pending", "available", "not_found"]
ToolControlCommand: TypeAlias = Literal["cancel", "stop"]
EndpointRegistryOperation: TypeAlias = Literal["register", "unregister"]
EndpointRegistryResponseStatus: TypeAlias = Literal["accepted", "rejected"]
ToolControlStatus: TypeAlias = Literal[
    "accepted",
    "rejected",
    "terminal",
    "unsupported",
]

_TOOL_SEMANTICS = frozenset(("query", "action", "session"))
_ENDPOINT_STATES = frozenset(("ready", "busy", "degraded", "unavailable"))
_EXECUTION_PHASES = frozenset(
    (
        "accepted",
        "running",
        "stopping",
        "completed",
        "failed",
        "cancelled",
        "stopped",
        "unknown",
    )
)
_TOOL_EVENT_TYPES = frozenset(
    (
        "progress",
        "heartbeat",
        "executor_completed",
        "executor_failed",
        "cancelled",
        "stopped",
    )
)
_TOOL_RESULT_STATUSES = frozenset(
    ("succeeded", "failed", "cancelled", "stopped", "unknown")
)
_TOOL_RESULT_RESPONSE_STATUSES = frozenset(("pending", "available", "not_found"))
_TOOL_CONTROL_COMMANDS = frozenset(("cancel", "stop"))
_ENDPOINT_REGISTRY_OPERATIONS = frozenset(("register", "unregister"))
_ENDPOINT_REGISTRY_RESPONSE_STATUSES = frozenset(("accepted", "rejected"))
_TOOL_CONTROL_STATUSES = frozenset(("accepted", "rejected", "terminal", "unsupported"))
_TERMINAL_PHASE_RESULTS = {
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "stopped": "stopped",
    "unknown": "unknown",
}
_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991


def _is_unicode_whitespace(character: str) -> bool:
    code_point = ord(character)
    return (
        0x09 <= code_point <= 0x0D
        or code_point
        in {0x20, 0x85, 0xA0, 0x1680, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000}
        or 0x2000 <= code_point <= 0x200A
    )


def _is_blank(value: str) -> bool:
    return not value or all(_is_unicode_whitespace(character) for character in value)


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _is_blank(value):
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{field_name} must contain valid Unicode scalar values")


def _json_copy(value: Any, path: str, depth: int = 0) -> Any:
    if depth > 64:
        raise ValueError(f"{path} JSON nesting exceeds 64")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError(f"{path} must contain valid Unicode scalar values")
        return value
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_JSON_INTEGER:
            raise ValueError(
                f"{path} integer exceeds interoperable range ±{_MAX_SAFE_JSON_INTEGER}"
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} floating-point values must be finite")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            if any(0xD800 <= ord(character) <= 0xDFFF for character in key):
                raise ValueError(
                    f"{path} keys must contain valid Unicode scalar values"
                )
            result[key] = _json_copy(nested, f"{path}.{key}", depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [
            _json_copy(nested, f"{path}[{index}]", depth + 1)
            for index, nested in enumerate(value)
        ]
    raise TypeError(f"{path} contains unsupported JSON value: {type(value).__name__}")


def _mapping_copy(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return _json_copy(value, field_name)


@dataclass(frozen=True)
class ToolOperationDescriptor:
    """One operation exposed by a logical ToolEndpoint."""

    name: str
    semantics: ToolSemantics
    cancellable: bool = False
    stoppable: bool = False
    status_supported: bool = False
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "name")
        if not isinstance(self.semantics, str) or self.semantics not in _TOOL_SEMANTICS:
            raise ValueError(f"unsupported tool semantics: {self.semantics!r}")
        for field_name in ("cancellable", "stoppable", "status_supported"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a bool")
        if self.semantics == "query" and (
            self.cancellable or self.stoppable or self.status_supported
        ):
            raise ValueError("query operations do not support control or status")
        if self.semantics == "action":
            if self.stoppable:
                raise ValueError("action operations must not be stoppable")
            if not self.status_supported:
                raise ValueError("action operations must support status")
        if self.semantics == "session":
            if self.cancellable:
                raise ValueError("session operations must not be cancellable")
            if not self.status_supported:
                raise ValueError("session operations must support status")
        if (
            isinstance(self.max_concurrency, bool)
            or not isinstance(self.max_concurrency, int)
            or not 1 <= self.max_concurrency <= _MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError(
                f"max_concurrency must be in [1, {_MAX_SAFE_JSON_INTEGER}]"
            )


@dataclass(frozen=True)
class ToolEndpointDescriptor:
    """Operations registered under one stable logical endpoint ID."""

    protocol_version: str
    endpoint_id: str
    operations: tuple[ToolOperationDescriptor, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.protocol_version, "protocol_version")
        _require_nonempty(self.endpoint_id, "endpoint_id")
        operations = tuple(self.operations)
        if not operations:
            raise ValueError("operations must not be empty")
        if not all(
            isinstance(operation, ToolOperationDescriptor) for operation in operations
        ):
            raise TypeError("operations must contain ToolOperationDescriptor values")
        operation_names = [operation.name for operation in operations]
        if len(operation_names) != len(set(operation_names)):
            raise ValueError("operation names must be unique within an endpoint")
        object.__setattr__(self, "operations", operations)


@dataclass(frozen=True)
class ToolExecutionKey:
    """Runtime identity for one implementation attempt within an invocation."""

    invocation_id: str
    attempt_id: str

    def __post_init__(self) -> None:
        _require_nonempty(self.invocation_id, "invocation_id")
        _require_nonempty(self.attempt_id, "attempt_id")


@dataclass(frozen=True)
class ToolContext:
    """Runtime context for one Tool execution attempt.

    ``deadline_ms`` is an absolute Unix epoch timestamp in milliseconds, not a
    duration relative to request receipt. It is execution data, not an
    observation timestamp.
    """

    execution_key: ToolExecutionKey
    tool_id: str
    implementation_id: str
    endpoint_id: str
    operation: str
    caller_id: str | None = None
    deadline_ms: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.execution_key, ToolExecutionKey):
            raise TypeError("execution_key must be a ToolExecutionKey")
        for field_name in ("tool_id", "implementation_id", "endpoint_id", "operation"):
            _require_nonempty(getattr(self, field_name), field_name)
        if self.caller_id is not None:
            _require_nonempty(self.caller_id, "caller_id")
        if self.deadline_ms is not None and (
            isinstance(self.deadline_ms, bool)
            or not isinstance(self.deadline_ms, int)
            or not 0 <= self.deadline_ms <= _MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError(
                "deadline_ms must be a non-negative Unix epoch millisecond "
                f"not greater than {_MAX_SAFE_JSON_INTEGER}"
            )
        object.__setattr__(self, "metadata", _mapping_copy(self.metadata, "metadata"))

    @property
    def invocation_id(self) -> str:
        """Return the invocation component of ``execution_key``."""
        return self.execution_key.invocation_id

    @property
    def attempt_id(self) -> str:
        """Return the attempt component of ``execution_key``."""
        return self.execution_key.attempt_id


@dataclass(frozen=True)
class ToolRequest:
    """JSON-like arguments supplied to a ToolEndpoint operation."""

    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "arguments", _mapping_copy(self.arguments, "arguments")
        )


@dataclass(frozen=True)
class ToolError:
    """Structured error returned by an endpoint execution."""

    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.code, "code")
        _require_nonempty(self.message, "message")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a bool")
        object.__setattr__(self, "details", _mapping_copy(self.details, "details"))


@dataclass(frozen=True)
class EndpointRegistryResponse:
    """Registry decision for one endpoint-management request.

    ``lease_ttl_ms`` is a lease duration in milliseconds, not an observation
    timestamp or absolute time.
    """

    operation: EndpointRegistryOperation
    status: EndpointRegistryResponseStatus
    registry_revision: int
    lease_ttl_ms: int | None = None
    error: ToolError | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.operation, str)
            or self.operation not in _ENDPOINT_REGISTRY_OPERATIONS
        ):
            raise ValueError(
                f"unsupported endpoint registry operation: {self.operation!r}"
            )
        if (
            not isinstance(self.status, str)
            or self.status not in _ENDPOINT_REGISTRY_RESPONSE_STATUSES
        ):
            raise ValueError(
                f"unsupported endpoint registry response status: {self.status!r}"
            )
        if (
            isinstance(self.registry_revision, bool)
            or not isinstance(self.registry_revision, int)
            or not 0 <= self.registry_revision <= _MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError(
                f"registry_revision must be in [0, {_MAX_SAFE_JSON_INTEGER}]"
            )
        if self.lease_ttl_ms is not None and (
            isinstance(self.lease_ttl_ms, bool)
            or not isinstance(self.lease_ttl_ms, int)
            or not 1 <= self.lease_ttl_ms <= _MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError(
                f"lease_ttl_ms must be in [1, {_MAX_SAFE_JSON_INTEGER}] when provided"
            )
        if self.error is not None and not isinstance(self.error, ToolError):
            raise TypeError("error must be a ToolError or None")

        if self.status == "rejected":
            if self.lease_ttl_ms is not None:
                raise ValueError(
                    "a rejected registry response must not contain lease_ttl_ms"
                )
            if self.error is None:
                raise ValueError("a rejected registry response must contain an error")
        else:
            if self.error is not None:
                raise ValueError(
                    "an accepted registry response must not contain an error"
                )
            if self.operation == "register":
                if self.lease_ttl_ms is None:
                    raise ValueError(
                        "an accepted register response must contain lease_ttl_ms"
                    )
            elif self.lease_ttl_ms is not None:
                raise ValueError(
                    "an accepted unregister response must not contain lease_ttl_ms"
                )


@dataclass(frozen=True)
class ToolResult:
    """Authoritative terminal result payload for a query or accepted execution.

    ``unknown`` means that the final execution outcome cannot be recovered. It
    does not mean that a result lookup is merely pending or unavailable.
    """

    status: ToolResultStatus
    outputs: Mapping[str, Any] = field(default_factory=dict)
    error: ToolError | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or self.status not in _TOOL_RESULT_STATUSES:
            raise ValueError(f"unsupported tool result status: {self.status!r}")
        requires_error = self.status in ("failed", "unknown")
        if requires_error and self.error is None:
            raise ValueError(f"a {self.status} tool result must contain an error")
        if not requires_error and self.error is not None:
            raise ValueError(f"a {self.status} tool result must not contain an error")
        if self.error is not None and not isinstance(self.error, ToolError):
            raise TypeError("error must be a ToolError or None")
        object.__setattr__(self, "outputs", _mapping_copy(self.outputs, "outputs"))


@dataclass(frozen=True)
class ToolResultResponse:
    """Result lookup response for an active, terminal, or unknown execution key."""

    status: ToolResultResponseStatus
    result: ToolResult | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status, str)
            or self.status not in _TOOL_RESULT_RESPONSE_STATUSES
        ):
            raise ValueError(f"unsupported result response status: {self.status!r}")
        if self.status == "available" and self.result is None:
            raise ValueError("an available result response must contain a result")
        if self.status != "available" and self.result is not None:
            raise ValueError("only an available result response may contain a result")
        if self.result is not None and not isinstance(self.result, ToolResult):
            raise TypeError("result must be a ToolResult or None")


@dataclass(frozen=True)
class ToolAccepted:
    """Acknowledgement that an action or session was admitted for execution."""

    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _mapping_copy(self.details, "details"))


@dataclass(frozen=True)
class ToolControlResponse:
    """Immediate response to a cancel or stop request.

    ``accepted`` means only that the control request was admitted. The caller
    must observe status or result to determine whether execution has terminated.
    ``terminal`` means execution was already terminal before the control request.
    """

    command: ToolControlCommand
    status: ToolControlStatus
    error: ToolError | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.command, str)
            or self.command not in _TOOL_CONTROL_COMMANDS
        ):
            raise ValueError(f"unsupported tool control command: {self.command!r}")
        if (
            not isinstance(self.status, str)
            or self.status not in _TOOL_CONTROL_STATUSES
        ):
            raise ValueError(f"unsupported tool control status: {self.status!r}")
        if self.status == "rejected" and self.error is None:
            raise ValueError("a rejected control response must contain an error")
        if self.status != "rejected" and self.error is not None:
            raise ValueError("only a rejected control response may contain an error")
        if self.error is not None and not isinstance(self.error, ToolError):
            raise TypeError("error must be a ToolError or None")
        object.__setattr__(self, "details", _mapping_copy(self.details, "details"))


@dataclass(frozen=True)
class ToolEvent:
    """Low-rate event payload emitted through an execution-bound emitter."""

    type: ToolEventType
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or self.type not in _TOOL_EVENT_TYPES:
            raise ValueError(f"unsupported tool event type: {self.type!r}")
        object.__setattr__(self, "data", _mapping_copy(self.data, "data"))


@dataclass(frozen=True)
class EndpointStatus:
    """Current health and aggregate concurrency usage of an endpoint."""

    endpoint_id: str
    state: EndpointState
    active_invocations: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.endpoint_id, "endpoint_id")
        if not isinstance(self.state, str) or self.state not in _ENDPOINT_STATES:
            raise ValueError(f"unsupported endpoint state: {self.state!r}")
        if (
            isinstance(self.active_invocations, bool)
            or not isinstance(self.active_invocations, int)
            or not 0 <= self.active_invocations <= _MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError(
                f"active_invocations must be in [0, {_MAX_SAFE_JSON_INTEGER}]"
            )
        object.__setattr__(self, "details", _mapping_copy(self.details, "details"))


@dataclass(frozen=True)
class ToolExecutionStatus:
    """Executor-side status payload for one accepted attempt.

    ``completed`` means that the endpoint executor considers its work complete;
    the Tool Runtime still owns final CompletionSpec evaluation.
    """

    phase: ExecutionPhase
    error: ToolError | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.phase, str) or self.phase not in _EXECUTION_PHASES:
            raise ValueError(f"unsupported execution phase: {self.phase!r}")
        requires_error = self.phase in ("failed", "unknown")
        if requires_error and self.error is None:
            raise ValueError(f"a {self.phase} execution status must contain an error")
        if not requires_error and self.error is not None:
            raise ValueError(
                "only a failed or unknown execution status may contain an error"
            )
        if self.error is not None and not isinstance(self.error, ToolError):
            raise TypeError("error must be a ToolError or None")
        object.__setattr__(self, "details", _mapping_copy(self.details, "details"))


def validate_execution_result(
    status: ToolExecutionStatus,
    result: ToolResult,
) -> None:
    """Validate one terminal executor status/result snapshot pair."""
    if not isinstance(status, ToolExecutionStatus):
        raise TypeError("status must be a ToolExecutionStatus")
    if not isinstance(result, ToolResult):
        raise TypeError("result must be a ToolResult")
    expected = _TERMINAL_PHASE_RESULTS.get(status.phase)
    if expected is None:
        raise ValueError(f"execution phase {status.phase!r} is not terminal")
    if result.status != expected:
        raise ValueError(
            f"execution phase {status.phase!r} requires result status {expected!r}"
        )
