"""Language-neutral Forge ToolEndpoint protocol envelope."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from .errors import ToolProtocolError

TOOL_ENDPOINT_PROTOCOL = "forge.tool.endpoint/v1alpha1"
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_JSON_DEPTH = 64

ToolMessageType: TypeAlias = Literal[
    "endpoint.register",
    "endpoint.unregister",
    "endpoint.registry.response",
    "endpoint.status",
    "tool.invoke.request",
    "tool.invoke.response",
    "tool.status.request",
    "tool.status.response",
    "tool.result.request",
    "tool.result.response",
    "tool.control.request",
    "tool.control.response",
    "tool.event",
    "tool.error",
]

TOOL_MESSAGE_TYPES: frozenset[str] = frozenset(
    (
        "endpoint.register",
        "endpoint.unregister",
        "endpoint.registry.response",
        "endpoint.status",
        "tool.invoke.request",
        "tool.invoke.response",
        "tool.status.request",
        "tool.status.response",
        "tool.result.request",
        "tool.result.response",
        "tool.control.request",
        "tool.control.response",
        "tool.event",
        "tool.error",
    )
)
_MANAGEMENT_MESSAGES = frozenset(
    (
        "endpoint.register",
        "endpoint.unregister",
        "endpoint.registry.response",
        "endpoint.status",
    )
)
_MANAGEMENT_MESSAGES_REQUIRING_REQUEST_ID = frozenset(
    (
        "endpoint.register",
        "endpoint.unregister",
        "endpoint.registry.response",
    )
)
_TOOL_MESSAGES = TOOL_MESSAGE_TYPES - _MANAGEMENT_MESSAGES


def _error(code: str, message: str, *, path: str | None = None) -> ToolProtocolError:
    return ToolProtocolError(code, message, path=path)


def _validate_unicode(value: str, path: str, *, payload: bool = False) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise _error(
            "FORGE_PROTOCOL_INVALID_PAYLOAD"
            if payload
            else "FORGE_PROTOCOL_INVALID_MESSAGE",
            "must contain only valid Unicode scalar values",
            path=path,
        )


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


def _require_nonempty(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or _is_blank(value):
        raise _error(
            "FORGE_PROTOCOL_INVALID_MESSAGE",
            "must be a non-empty string",
            path=field_name,
        )
    _validate_unicode(value, field_name)
    return value


def _json_value(value: Any, path: str, depth: int = 0) -> Any:
    if depth > MAX_JSON_DEPTH:
        raise _error(
            "FORGE_PROTOCOL_INVALID_PAYLOAD",
            f"JSON nesting exceeds {MAX_JSON_DEPTH}",
            path=path,
        )
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        _validate_unicode(value, path, payload=True)
        return value
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise _error(
                "FORGE_PROTOCOL_INVALID_PAYLOAD",
                f"integer exceeds interoperable range ±{MAX_SAFE_JSON_INTEGER}",
                path=path,
            )
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _error(
                "FORGE_PROTOCOL_INVALID_PAYLOAD",
                "floating-point values must be finite",
                path=path,
            )
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise _error(
                    "FORGE_PROTOCOL_INVALID_PAYLOAD",
                    "object keys must be strings",
                    path=path,
                )
            _validate_unicode(key, path, payload=True)
            result[key] = _json_value(nested, f"{path}.{key}", depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [
            _json_value(nested, f"{path}[{index}]", depth + 1)
            for index, nested in enumerate(value)
        ]
    raise _error(
        "FORGE_PROTOCOL_INVALID_PAYLOAD",
        f"unsupported JSON value: {type(value).__name__}",
        path=path,
    )


@dataclass(frozen=True)
class ToolEnvelope:
    """Validated JSON-compatible ToolEndpoint protocol envelope."""

    protocol: str
    message_type: ToolMessageType
    payload: Mapping[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    invocation_id: str | None = None
    attempt_id: str | None = None
    endpoint_id: str | None = None
    endpoint_instance_id: str | None = None
    operation: str | None = None
    sequence: int | None = None

    def __post_init__(self) -> None:
        if self.protocol != TOOL_ENDPOINT_PROTOCOL:
            raise _error(
                "FORGE_PROTOCOL_UNSUPPORTED_VERSION",
                f"expected {TOOL_ENDPOINT_PROTOCOL!r}, found {self.protocol!r}",
                path="protocol",
            )
        if (
            not isinstance(self.message_type, str)
            or self.message_type not in TOOL_MESSAGE_TYPES
        ):
            raise _error(
                "FORGE_PROTOCOL_UNKNOWN_MESSAGE_TYPE",
                f"unsupported message type: {self.message_type!r}",
                path="message_type",
            )
        if not isinstance(self.payload, Mapping):
            raise _error(
                "FORGE_PROTOCOL_INVALID_PAYLOAD",
                "must be a JSON object",
                path="payload",
            )

        _require_nonempty(self.endpoint_id, "endpoint_id")
        if self.endpoint_instance_id is None:
            if self.message_type in _MANAGEMENT_MESSAGES:
                raise _error(
                    "FORGE_PROTOCOL_INVALID_MESSAGE",
                    "must be present for endpoint management messages",
                    path="endpoint_instance_id",
                )
        else:
            _require_nonempty(self.endpoint_instance_id, "endpoint_instance_id")

        if self.message_type in _TOOL_MESSAGES:
            _require_nonempty(self.invocation_id, "invocation_id")
            _require_nonempty(self.attempt_id, "attempt_id")
            _require_nonempty(self.operation, "operation")
            if self.message_type == "tool.event":
                if self.request_id is not None:
                    raise _error(
                        "FORGE_PROTOCOL_INVALID_MESSAGE",
                        "must be omitted for tool.event",
                        path="request_id",
                    )
            else:
                _require_nonempty(self.request_id, "request_id")
        else:
            if self.message_type in _MANAGEMENT_MESSAGES_REQUIRING_REQUEST_ID:
                _require_nonempty(self.request_id, "request_id")
            elif self.message_type == "endpoint.status" and self.request_id is not None:
                raise _error(
                    "FORGE_PROTOCOL_INVALID_MESSAGE",
                    "must be omitted for unsolicited endpoint.status",
                    path="request_id",
                )
            for field_name in ("invocation_id", "attempt_id", "operation", "sequence"):
                if getattr(self, field_name) is not None:
                    raise _error(
                        "FORGE_PROTOCOL_INVALID_MESSAGE",
                        "must be omitted for endpoint management messages",
                        path=field_name,
                    )

        if self.message_type == "tool.event":
            if (
                isinstance(self.sequence, bool)
                or not isinstance(self.sequence, int)
                or not 0 <= self.sequence <= MAX_SAFE_JSON_INTEGER
            ):
                raise _error(
                    "FORGE_PROTOCOL_INVALID_MESSAGE",
                    f"must be an integer in [0, {MAX_SAFE_JSON_INTEGER}]",
                    path="sequence",
                )
        elif self.sequence is not None:
            raise _error(
                "FORGE_PROTOCOL_INVALID_MESSAGE",
                "must be omitted for non-event messages",
                path="sequence",
            )

        if self.request_id is not None:
            _require_nonempty(self.request_id, "request_id")
        object.__setattr__(self, "payload", _json_value(self.payload, "payload"))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible object with absent optional fields omitted."""
        value: dict[str, Any] = {
            "protocol": self.protocol,
            "message_type": self.message_type,
            "endpoint_id": self.endpoint_id,
            "payload": dict(self.payload),
        }
        for field_name in (
            "request_id",
            "endpoint_instance_id",
            "invocation_id",
            "attempt_id",
            "operation",
            "sequence",
        ):
            field_value = getattr(self, field_name)
            if field_value is not None:
                value[field_name] = field_value
        return value
