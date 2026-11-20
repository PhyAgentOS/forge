"""Typed payload adapters for generic ToolEndpoint protocol messages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from ..endpoint import (
    EndpointRegistryResponse,
    EndpointStatus,
    ToolAccepted,
    ToolContext,
    ToolControlCommand,
    ToolControlResponse,
    ToolError,
    ToolEvent,
    ToolExecutionKey,
    ToolExecutionStatus,
    ToolRequest,
    ToolResult,
    ToolResultResponse,
)
from .descriptor import validate_registration_envelope
from .envelope import ToolEnvelope, _is_blank, _json_value
from .errors import ToolProtocolError

_T = TypeVar("_T")


def _error(message: str, path: str) -> ToolProtocolError:
    return ToolProtocolError(
        "FORGE_PROTOCOL_INVALID_PAYLOAD",
        message,
        path=path,
    )


def _object(value: Any, path: str) -> Mapping[str, Any]:
    normalized = _json_value(value, path)
    if not isinstance(normalized, Mapping):
        raise _error("must be an object", path)
    return normalized


def _fields(
    value: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    path: str,
) -> None:
    expected = required | optional
    unknown = sorted(set(value) - expected)
    missing = sorted(required - set(value))
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        raise _error("; ".join(details), path)


def _construct(factory: type[_T], path: str, **values: Any) -> _T:
    try:
        return factory(**values)
    except (TypeError, ValueError) as error:
        raise _error(str(error), path) from error


def tool_error_to_payload(error: ToolError) -> dict[str, Any]:
    """Convert a structured Tool error to its strict JSON payload object."""
    if not isinstance(error, ToolError):
        raise TypeError("error must be a ToolError")
    return {
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
        "details": dict(error.details),
    }


def tool_error_from_payload(value: Any, path: str = "payload.error") -> ToolError:
    """Decode a strict structured Tool error object."""
    item = _object(value, path)
    _fields(
        item,
        required=frozenset(("code", "message", "retryable", "details")),
        path=path,
    )
    return _construct(
        ToolError,
        path,
        code=item["code"],
        message=item["message"],
        retryable=item["retryable"],
        details=_object(item["details"], f"{path}.details"),
    )


def tool_result_to_payload(result: ToolResult) -> dict[str, Any]:
    """Convert a terminal Tool result to a JSON payload object."""
    if not isinstance(result, ToolResult):
        raise TypeError("result must be a ToolResult")
    value: dict[str, Any] = {
        "status": result.status,
        "outputs": dict(result.outputs),
    }
    if result.error is not None:
        value["error"] = tool_error_to_payload(result.error)
    return value


def tool_result_from_payload(value: Any, path: str = "payload.result") -> ToolResult:
    """Decode a strict terminal Tool result object."""
    item = _object(value, path)
    _fields(
        item,
        required=frozenset(("status", "outputs")),
        optional=frozenset(("error",)),
        path=path,
    )
    error = None
    if "error" in item:
        error = tool_error_from_payload(item["error"], f"{path}.error")
    return _construct(
        ToolResult,
        path,
        status=item["status"],
        outputs=_object(item["outputs"], f"{path}.outputs"),
        error=error,
    )


def tool_context_to_payload(context: ToolContext) -> dict[str, Any]:
    """Convert context fields not already represented by the Envelope."""
    if not isinstance(context, ToolContext):
        raise TypeError("context must be a ToolContext")
    value: dict[str, Any] = {
        "tool_id": context.tool_id,
        "implementation_id": context.implementation_id,
        "metadata": dict(context.metadata),
    }
    if context.caller_id is not None:
        value["caller_id"] = context.caller_id
    if context.deadline_ms is not None:
        value["deadline_ms"] = context.deadline_ms
    return value


def _context_from_envelope(value: Any, envelope: ToolEnvelope) -> ToolContext:
    path = "payload.context"
    item = _object(value, path)
    _fields(
        item,
        required=frozenset(("tool_id", "implementation_id", "metadata")),
        optional=frozenset(("caller_id", "deadline_ms")),
        path=path,
    )
    return _construct(
        ToolContext,
        path,
        execution_key=ToolExecutionKey(
            invocation_id=cast(str, envelope.invocation_id),
            attempt_id=cast(str, envelope.attempt_id),
        ),
        tool_id=item["tool_id"],
        implementation_id=item["implementation_id"],
        endpoint_id=cast(str, envelope.endpoint_id),
        operation=cast(str, envelope.operation),
        caller_id=item.get("caller_id"),
        deadline_ms=item.get("deadline_ms"),
        metadata=_object(item["metadata"], f"{path}.metadata"),
    )


def invoke_request_to_payload(
    request: ToolRequest,
    context: ToolContext,
) -> dict[str, Any]:
    """Encode a generic invoke request payload."""
    if not isinstance(request, ToolRequest):
        raise TypeError("request must be a ToolRequest")
    return {
        "arguments": dict(request.arguments),
        "context": tool_context_to_payload(context),
    }


def invoke_request_from_envelope(
    envelope: ToolEnvelope,
) -> tuple[ToolRequest, ToolContext]:
    """Decode a generic invoke request and combine Envelope identity with context."""
    if envelope.message_type != "tool.invoke.request":
        raise ValueError("envelope must be tool.invoke.request")
    payload = _object(envelope.payload, "payload")
    _fields(
        payload,
        required=frozenset(("arguments", "context")),
        path="payload",
    )
    request = _construct(
        ToolRequest,
        "payload.arguments",
        arguments=_object(payload["arguments"], "payload.arguments"),
    )
    return request, _context_from_envelope(payload["context"], envelope)


def invoke_response_to_payload(
    response: ToolResult | ToolAccepted | ToolError,
) -> dict[str, Any]:
    """Encode a completed, accepted, or rejected invoke response."""
    if isinstance(response, ToolResult):
        return {"outcome": "completed", "result": tool_result_to_payload(response)}
    if isinstance(response, ToolAccepted):
        return {
            "outcome": "accepted",
            "accepted": {"details": dict(response.details)},
        }
    if isinstance(response, ToolError):
        return {"outcome": "rejected", "error": tool_error_to_payload(response)}
    raise TypeError("response must be a ToolResult, ToolAccepted, or ToolError")


def invoke_response_from_payload(
    value: Any,
) -> ToolResult | ToolAccepted | ToolError:
    """Decode a completed, accepted, or rejected invoke response."""
    payload = _object(value, "payload")
    outcome = payload.get("outcome")
    if outcome == "completed":
        _fields(
            payload,
            required=frozenset(("outcome", "result")),
            path="payload",
        )
        return tool_result_from_payload(payload["result"])
    if outcome == "accepted":
        _fields(
            payload,
            required=frozenset(("outcome", "accepted")),
            path="payload",
        )
        accepted = _object(payload["accepted"], "payload.accepted")
        _fields(
            accepted,
            required=frozenset(("details",)),
            path="payload.accepted",
        )
        return _construct(
            ToolAccepted,
            "payload.accepted",
            details=_object(accepted["details"], "payload.accepted.details"),
        )
    if outcome == "rejected":
        _fields(
            payload,
            required=frozenset(("outcome", "error")),
            path="payload",
        )
        return tool_error_from_payload(payload["error"])
    raise _error(
        "outcome must be completed, accepted, or rejected",
        "payload.outcome",
    )


def execution_status_to_payload(status: ToolExecutionStatus) -> dict[str, Any]:
    """Convert an executor status snapshot to a JSON object."""
    if not isinstance(status, ToolExecutionStatus):
        raise TypeError("status must be a ToolExecutionStatus")
    value: dict[str, Any] = {
        "phase": status.phase,
        "details": dict(status.details),
    }
    if status.error is not None:
        value["error"] = tool_error_to_payload(status.error)
    return value


def execution_status_from_payload(
    value: Any,
    path: str = "payload.status",
) -> ToolExecutionStatus:
    """Decode a strict executor status snapshot."""
    item = _object(value, path)
    _fields(
        item,
        required=frozenset(("phase", "details")),
        optional=frozenset(("error",)),
        path=path,
    )
    error = None
    if "error" in item:
        error = tool_error_from_payload(item["error"], f"{path}.error")
    return _construct(
        ToolExecutionStatus,
        path,
        phase=item["phase"],
        error=error,
        details=_object(item["details"], f"{path}.details"),
    )


def status_response_to_payload(status: ToolExecutionStatus) -> dict[str, Any]:
    """Encode an executor status response payload."""
    return {"status": execution_status_to_payload(status)}


def status_response_from_payload(value: Any) -> ToolExecutionStatus:
    """Decode an executor status response payload."""
    payload = _object(value, "payload")
    _fields(payload, required=frozenset(("status",)), path="payload")
    return execution_status_from_payload(payload["status"])


def result_response_to_payload(response: ToolResultResponse) -> dict[str, Any]:
    """Encode a pending, available, or not-found result lookup response."""
    if not isinstance(response, ToolResultResponse):
        raise TypeError("response must be a ToolResultResponse")
    value: dict[str, Any] = {"status": response.status}
    if response.result is not None:
        value["result"] = tool_result_to_payload(response.result)
    return value


def result_response_from_payload(value: Any) -> ToolResultResponse:
    """Decode a pending, available, or not-found result lookup response."""
    payload = _object(value, "payload")
    _fields(
        payload,
        required=frozenset(("status",)),
        optional=frozenset(("result",)),
        path="payload",
    )
    result = None
    if "result" in payload:
        result = tool_result_from_payload(payload["result"])
    return _construct(
        ToolResultResponse,
        "payload",
        status=payload["status"],
        result=result,
    )


def error_to_payload(error: ToolError) -> dict[str, Any]:
    """Encode a protocol or transport-level Tool error payload."""
    return {"error": tool_error_to_payload(error)}


def error_from_payload(value: Any) -> ToolError:
    """Decode a protocol or transport-level Tool error payload."""
    payload = _object(value, "payload")
    _fields(payload, required=frozenset(("error",)), path="payload")
    return tool_error_from_payload(payload["error"])


def control_request_to_payload(
    command: ToolControlCommand,
    reason: str | None = None,
) -> dict[str, Any]:
    """Encode a cancel or stop request."""
    if command not in ("cancel", "stop"):
        raise ValueError(f"unsupported tool control command: {command!r}")
    value: dict[str, Any] = {"command": command}
    if reason is not None:
        if not isinstance(reason, str) or _is_blank(reason):
            raise ValueError("reason must be a non-empty string when provided")
        value["reason"] = reason
    return dict(_object(value, "payload"))


def control_request_from_payload(
    value: Any,
) -> tuple[ToolControlCommand, str | None]:
    """Decode a strict cancel or stop request."""
    payload = _object(value, "payload")
    _fields(
        payload,
        required=frozenset(("command",)),
        optional=frozenset(("reason",)),
        path="payload",
    )
    command = payload["command"]
    if command not in ("cancel", "stop"):
        raise _error("command must be cancel or stop", "payload.command")
    reason = payload.get("reason")
    if reason is not None and (not isinstance(reason, str) or _is_blank(reason)):
        raise _error("must be a non-empty string", "payload.reason")
    return cast(ToolControlCommand, command), cast(str | None, reason)


def control_response_to_payload(response: ToolControlResponse) -> dict[str, Any]:
    """Encode an immediate control response."""
    if not isinstance(response, ToolControlResponse):
        raise TypeError("response must be a ToolControlResponse")
    item: dict[str, Any] = {
        "command": response.command,
        "status": response.status,
        "details": dict(response.details),
    }
    if response.error is not None:
        item["error"] = tool_error_to_payload(response.error)
    return {"response": item}


def control_response_from_payload(value: Any) -> ToolControlResponse:
    """Decode a strict immediate control response."""
    payload = _object(value, "payload")
    _fields(
        payload,
        required=frozenset(("response",)),
        path="payload",
    )
    item = _object(payload["response"], "payload.response")
    _fields(
        item,
        required=frozenset(("command", "status", "details")),
        optional=frozenset(("error",)),
        path="payload.response",
    )
    error = None
    if "error" in item:
        error = tool_error_from_payload(item["error"], "payload.response.error")
    return _construct(
        ToolControlResponse,
        "payload.response",
        command=item["command"],
        status=item["status"],
        error=error,
        details=_object(item["details"], "payload.response.details"),
    )


def event_to_payload(event: ToolEvent) -> dict[str, Any]:
    """Encode an executor event payload; sequence remains in the Envelope."""
    if not isinstance(event, ToolEvent):
        raise TypeError("event must be a ToolEvent")
    return {"type": event.type, "data": dict(event.data)}


def event_from_payload(value: Any) -> ToolEvent:
    """Decode a strict executor event payload."""
    payload = _object(value, "payload")
    _fields(
        payload,
        required=frozenset(("type", "data")),
        path="payload",
    )
    return _construct(
        ToolEvent,
        "payload",
        type=payload["type"],
        data=_object(payload["data"], "payload.data"),
    )


def endpoint_registry_response_to_payload(
    response: EndpointRegistryResponse,
) -> dict[str, Any]:
    """Encode a Registry decision without duplicating endpoint identity."""
    if not isinstance(response, EndpointRegistryResponse):
        raise TypeError("response must be an EndpointRegistryResponse")
    value: dict[str, Any] = {
        "operation": response.operation,
        "status": response.status,
        "registry_revision": response.registry_revision,
    }
    if response.lease_ttl_ms is not None:
        value["lease_ttl_ms"] = response.lease_ttl_ms
    if response.error is not None:
        value["error"] = tool_error_to_payload(response.error)
    return value


def endpoint_registry_response_from_payload(value: Any) -> EndpointRegistryResponse:
    """Decode a strict endpoint Registry response payload."""
    payload = _object(value, "payload")
    _fields(
        payload,
        required=frozenset(("operation", "status", "registry_revision")),
        optional=frozenset(("lease_ttl_ms", "error")),
        path="payload",
    )
    lease_ttl_ms = None
    if "lease_ttl_ms" in payload:
        if payload["lease_ttl_ms"] is None:
            raise _error("must be omitted rather than null", "payload.lease_ttl_ms")
        lease_ttl_ms = payload["lease_ttl_ms"]
    error = None
    if "error" in payload:
        if payload["error"] is None:
            raise _error("must be omitted rather than null", "payload.error")
        error = tool_error_from_payload(payload["error"], "payload.error")
    return _construct(
        EndpointRegistryResponse,
        "payload",
        operation=payload["operation"],
        status=payload["status"],
        registry_revision=payload["registry_revision"],
        lease_ttl_ms=lease_ttl_ms,
        error=error,
    )


def endpoint_status_to_payload(status: EndpointStatus) -> dict[str, Any]:
    """Encode an endpoint health snapshot without duplicating endpoint identity."""
    if not isinstance(status, EndpointStatus):
        raise TypeError("status must be an EndpointStatus")
    return {
        "status": {
            "state": status.state,
            "active_invocations": status.active_invocations,
            "details": dict(status.details),
        }
    }


def endpoint_status_from_envelope(envelope: ToolEnvelope) -> EndpointStatus:
    """Decode endpoint health and combine it with Envelope endpoint identity."""
    if envelope.message_type != "endpoint.status":
        raise ValueError("envelope must be endpoint.status")
    payload = _object(envelope.payload, "payload")
    _fields(payload, required=frozenset(("status",)), path="payload")
    item = _object(payload["status"], "payload.status")
    _fields(
        item,
        required=frozenset(("state", "active_invocations", "details")),
        path="payload.status",
    )
    return _construct(
        EndpointStatus,
        "payload.status",
        endpoint_id=cast(str, envelope.endpoint_id),
        state=item["state"],
        active_invocations=item["active_invocations"],
        details=_object(item["details"], "payload.status.details"),
    )


def validate_message_envelope(envelope: ToolEnvelope) -> None:
    """Strictly validate the payload schema selected by ``message_type``."""
    message_type = envelope.message_type
    if message_type == "endpoint.register":
        validate_registration_envelope(envelope)
    elif message_type == "endpoint.unregister":
        payload = _object(envelope.payload, "payload")
        _fields(payload, required=frozenset(), path="payload")
    elif message_type == "endpoint.registry.response":
        endpoint_registry_response_from_payload(envelope.payload)
    elif message_type == "endpoint.status":
        endpoint_status_from_envelope(envelope)
    elif message_type == "tool.invoke.request":
        invoke_request_from_envelope(envelope)
    elif message_type == "tool.invoke.response":
        invoke_response_from_payload(envelope.payload)
    elif message_type in ("tool.status.request", "tool.result.request"):
        payload = _object(envelope.payload, "payload")
        _fields(payload, required=frozenset(), path="payload")
    elif message_type == "tool.status.response":
        status_response_from_payload(envelope.payload)
    elif message_type == "tool.result.response":
        result_response_from_payload(envelope.payload)
    elif message_type == "tool.control.request":
        control_request_from_payload(envelope.payload)
    elif message_type == "tool.control.response":
        control_response_from_payload(envelope.payload)
    elif message_type == "tool.event":
        event_from_payload(envelope.payload)
    elif message_type == "tool.error":
        error_from_payload(envelope.payload)
