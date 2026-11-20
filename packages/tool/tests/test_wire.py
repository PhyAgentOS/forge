from __future__ import annotations

from typing import get_args

import pytest

from forge_tool import (
    TOOL_ENDPOINT_PROTOCOL,
    TOOL_MESSAGE_TYPES,
    ToolEnvelope,
    ToolMessageType,
    ToolProtocolError,
    decode_envelope,
    encode_envelope,
)

_MANAGEMENT_TYPES = {
    "endpoint.register",
    "endpoint.unregister",
    "endpoint.registry.response",
    "endpoint.status",
}
_MESSAGES_REQUIRING_REQUEST = TOOL_MESSAGE_TYPES - {"endpoint.status", "tool.event"}
_REMOVED_MESSAGE_TYPES = (
    "tool.query.request",
    "tool.query.response",
    "tool.action.start",
    "tool.action.accepted",
    "tool.action.cancel",
    "tool.action.status.request",
    "tool.action.status.response",
    "tool.session.start",
    "tool.session.accepted",
    "tool.session.stop",
    "tool.session.status.request",
    "tool.session.status.response",
    "tool.command.ack",
    "endpoint.heartbeat",
)


def _tool_envelope(**overrides: object) -> ToolEnvelope:
    values: dict[str, object] = {
        "protocol": TOOL_ENDPOINT_PROTOCOL,
        "message_type": "tool.invoke.request",
        "request_id": "request-1",
        "invocation_id": "invocation-1",
        "attempt_id": "attempt-1",
        "endpoint_id": "vision.yolo",
        "endpoint_instance_id": "endpoint-instance-1",
        "operation": "detect",
        "payload": {
            "arguments": {"class": "方块"},
            "context": {
                "tool_id": "forge.tool.detect",
                "implementation_id": "yolo",
                "metadata": {},
            },
        },
    }
    values.update(overrides)
    return ToolEnvelope(**values)  # type: ignore[arg-type]


def test_message_type_literal_matches_runtime_registry() -> None:
    assert set(get_args(ToolMessageType)) == TOOL_MESSAGE_TYPES


def test_generic_invoke_envelope_round_trips_as_deterministic_utf8_json() -> None:
    envelope = _tool_envelope()

    encoded = encode_envelope(envelope)

    assert encoded == (
        b'{"attempt_id":"attempt-1","endpoint_id":"vision.yolo",'
        b'"endpoint_instance_id":"endpoint-instance-1",'
        b'"invocation_id":"invocation-1",'
        b'"message_type":"tool.invoke.request","operation":"detect",'
        b'"payload":{"arguments":{"class":"\xe6\x96\xb9\xe5\x9d\x97"},'
        b'"context":{"implementation_id":"yolo","metadata":{},'
        b'"tool_id":"forge.tool.detect"}},'
        b'"protocol":"forge.tool.endpoint/v1alpha1","request_id":"request-1"}'
    )
    assert decode_envelope(encoded) == envelope


def test_tool_envelopes_may_omit_instance_before_provider_routing() -> None:
    invoke = _tool_envelope(endpoint_instance_id=None)

    encoded = encode_envelope(invoke)

    assert b"endpoint_instance_id" not in encoded
    assert decode_envelope(encoded) == invoke

    for message_type in TOOL_MESSAGE_TYPES - _MANAGEMENT_TYPES:
        envelope = _tool_envelope(
            message_type=message_type,
            request_id=None if message_type == "tool.event" else "request-1",
            endpoint_instance_id=None,
            sequence=0 if message_type == "tool.event" else None,
        )
        assert "endpoint_instance_id" not in envelope.to_dict()


def test_management_envelope_requires_instance_but_forbids_execution_fields() -> None:
    unregister = ToolEnvelope(
        protocol=TOOL_ENDPOINT_PROTOCOL,
        message_type="endpoint.unregister",
        request_id="unregister-1",
        endpoint_id="vision.yolo",
        endpoint_instance_id="endpoint-instance-1",
    )
    assert decode_envelope(encode_envelope(unregister)) == unregister

    for field_name, field_value in (
        ("invocation_id", "invocation-1"),
        ("attempt_id", "attempt-1"),
        ("operation", "detect"),
        ("sequence", 0),
    ):
        with pytest.raises(ToolProtocolError, match="must be omitted"):
            ToolEnvelope(
                protocol=TOOL_ENDPOINT_PROTOCOL,
                message_type="endpoint.unregister",
                request_id="unregister-1",
                endpoint_id="vision.yolo",
                endpoint_instance_id="endpoint-instance-1",
                **{field_name: field_value},
            )


def test_tool_event_requires_sequence_but_not_request_id() -> None:
    event = ToolEnvelope(
        protocol=TOOL_ENDPOINT_PROTOCOL,
        message_type="tool.event",
        invocation_id="invocation-1",
        attempt_id="attempt-1",
        endpoint_id="policy.lerobot",
        endpoint_instance_id="endpoint-instance-1",
        operation="execute",
        sequence=0,
        payload={"type": "heartbeat", "data": {}},
    )

    assert event.request_id is None
    assert event.sequence == 0

    with pytest.raises(ToolProtocolError, match="must be omitted") as captured:
        _tool_envelope(message_type="tool.event", sequence=0)
    assert captured.value.path == "request_id"

    with pytest.raises(ToolProtocolError) as captured:
        _tool_envelope(message_type="tool.event", request_id=None, sequence=None)
    assert captured.value.path == "sequence"


@pytest.mark.parametrize("message_type", sorted(_MESSAGES_REQUIRING_REQUEST))
def test_exchange_messages_require_request_id(message_type: str) -> None:
    if message_type in _MANAGEMENT_TYPES:
        values: dict[str, object] = {
            "protocol": TOOL_ENDPOINT_PROTOCOL,
            "message_type": message_type,
            "endpoint_id": "vision.yolo",
            "endpoint_instance_id": "endpoint-instance-1",
        }
        with pytest.raises(ToolProtocolError) as captured:
            ToolEnvelope(**values)  # type: ignore[arg-type]
    else:
        with pytest.raises(ToolProtocolError) as captured:
            _tool_envelope(message_type=message_type, request_id=None)
    assert captured.value.path == "request_id"


def test_unsolicited_endpoint_status_must_omit_request_id() -> None:
    status = ToolEnvelope(
        protocol=TOOL_ENDPOINT_PROTOCOL,
        message_type="endpoint.status",
        endpoint_id="vision.yolo",
        endpoint_instance_id="endpoint-instance-1",
    )

    assert status.request_id is None
    with pytest.raises(
        ToolProtocolError, match="unsolicited endpoint.status"
    ) as captured:
        ToolEnvelope(
            protocol=TOOL_ENDPOINT_PROTOCOL,
            message_type="endpoint.status",
            request_id="status-1",
            endpoint_id="vision.yolo",
            endpoint_instance_id="endpoint-instance-1",
        )
    assert captured.value.path == "request_id"


@pytest.mark.parametrize("message_type", _REMOVED_MESSAGE_TYPES)
def test_removed_semantics_specific_message_types_are_rejected(
    message_type: str,
) -> None:
    with pytest.raises(ToolProtocolError) as captured:
        _tool_envelope(message_type=message_type)
    assert captured.value.code == "FORGE_PROTOCOL_UNKNOWN_MESSAGE_TYPE"


@pytest.mark.parametrize(
    ("overrides", "path"),
    [
        ({"protocol": "forge.tool.endpoint/v2"}, "protocol"),
        ({"message_type": "tool.unknown"}, "message_type"),
        ({"request_id": None}, "request_id"),
        ({"endpoint_id": "\u00a0"}, "endpoint_id"),
        ({"sequence": 0}, "sequence"),
    ],
)
def test_envelope_rejects_invalid_headers(
    overrides: dict[str, object], path: str
) -> None:
    with pytest.raises(ToolProtocolError) as captured:
        _tool_envelope(**overrides)
    assert captured.value.path == path


def test_endpoint_messages_require_endpoint_instance_id() -> None:
    for message_type in _MANAGEMENT_TYPES:
        with pytest.raises(ToolProtocolError) as captured:
            ToolEnvelope(
                protocol=TOOL_ENDPOINT_PROTOCOL,
                message_type=message_type,  # type: ignore[arg-type]
                request_id=None if message_type == "endpoint.status" else "request-1",
                endpoint_id="vision.yolo",
                endpoint_instance_id=None,
            )
        assert captured.value.path == "endpoint_instance_id"


def test_event_sequence_uses_interoperable_non_negative_integer() -> None:
    for sequence in (-1, 9_007_199_254_740_992, True):
        with pytest.raises(ToolProtocolError) as captured:
            _tool_envelope(
                message_type="tool.event",
                request_id=None,
                sequence=sequence,
            )
        assert captured.value.path == "sequence"


def test_payload_is_json_normalized_and_rejects_non_json_values() -> None:
    envelope = _tool_envelope(payload={"arguments": {"classes": ("cube",)}})
    assert envelope.payload == {"arguments": {"classes": ["cube"]}}

    with pytest.raises(ToolProtocolError, match="unsupported JSON value"):
        _tool_envelope(payload={"arguments": {"opaque": object()}})
    with pytest.raises(ToolProtocolError, match="finite"):
        _tool_envelope(payload={"arguments": {"threshold": float("nan")}})
    with pytest.raises(ToolProtocolError, match="interoperable range"):
        _tool_envelope(payload={"arguments": {"value": 9_007_199_254_740_992}})


def test_encoder_revalidates_payload_modified_after_construction() -> None:
    envelope = _tool_envelope(payload={"arguments": {"value": 1}})
    payload = envelope.payload
    assert isinstance(payload, dict)
    arguments = payload["arguments"]
    assert isinstance(arguments, dict)
    arguments["value"] = 9_007_199_254_740_992

    with pytest.raises(ToolProtocolError, match="interoperable range"):
        encode_envelope(envelope)


def test_decoder_rejects_unknown_missing_duplicate_and_explicit_null_fields() -> None:
    with pytest.raises(ToolProtocolError, match="unknown envelope fields"):
        decode_envelope(
            '{"protocol":"forge.tool.endpoint/v1alpha1",'
            '"message_type":"endpoint.unregister",'
            '"request_id":"unregister-1",'
            '"endpoint_id":"vision.yolo",'
            '"endpoint_instance_id":"instance-1",'
            '"payload":{},"timestamp_ms":1}'
        )
    with pytest.raises(ToolProtocolError, match="missing envelope fields"):
        decode_envelope(
            '{"protocol":"forge.tool.endpoint/v1alpha1",'
            '"message_type":"endpoint.unregister"}'
        )
    with pytest.raises(ToolProtocolError) as captured:
        decode_envelope('{"protocol":"one","protocol":"two"}')
    assert captured.value.code == "FORGE_PROTOCOL_DUPLICATE_KEY"

    with pytest.raises(ToolProtocolError, match="omitted rather than null"):
        decode_envelope(
            '{"protocol":"forge.tool.endpoint/v1alpha1",'
            '"message_type":"endpoint.unregister",'
            '"request_id":"unregister-1",'
            '"endpoint_id":"vision.yolo",'
            '"endpoint_instance_id":"instance-1",'
            '"invocation_id":null,"payload":{}}'
        )

    with pytest.raises(ToolProtocolError, match="omitted rather than null"):
        decode_envelope(
            '{"protocol":"forge.tool.endpoint/v1alpha1",'
            '"message_type":"tool.invoke.request",'
            '"request_id":"request-1",'
            '"invocation_id":"invocation-1",'
            '"attempt_id":"attempt-1",'
            '"endpoint_id":"vision.yolo",'
            '"endpoint_instance_id":null,'
            '"operation":"detect",'
            '"payload":{"arguments":{},"context":{"tool_id":"tool-1",'
            '"implementation_id":"impl-1","metadata":{}}}}'
        )


def test_decoder_rejects_invalid_utf8_non_finite_and_surrogate_json() -> None:
    with pytest.raises(ToolProtocolError, match="UTF-8"):
        decode_envelope(b"\xff")
    with pytest.raises(ToolProtocolError, match="non-finite"):
        decode_envelope(
            '{"protocol":"forge.tool.endpoint/v1alpha1",'
            '"message_type":"endpoint.unregister",'
            '"request_id":"unregister-1",'
            '"endpoint_id":"vision.yolo",'
            '"endpoint_instance_id":"instance-1",'
            '"payload":{"value":NaN}}'
        )
    with pytest.raises(ToolProtocolError, match="Unicode scalar"):
        decode_envelope(
            '{"protocol":"forge.tool.endpoint/v1alpha1",'
            '"message_type":"endpoint.unregister",'
            '"request_id":"unregister-1",'
            '"endpoint_id":"vision.yolo",'
            '"endpoint_instance_id":"instance-1",'
            '"payload":{"value":"\\ud800"}}'
        )


def test_decoder_normalizes_parser_resource_errors() -> None:
    with pytest.raises(ToolProtocolError):
        decode_envelope("[" * 2_000 + "]" * 2_000)
    with pytest.raises(ToolProtocolError, match="integer"):
        decode_envelope(
            '{"protocol":"forge.tool.endpoint/v1alpha1",'
            '"message_type":"endpoint.unregister",'
            '"request_id":"unregister-1",'
            '"endpoint_id":"vision.yolo",'
            '"endpoint_instance_id":"instance-1",'
            f'"payload":{{"value":{"9" * 5_000}}}}}'
        )


def test_codec_enforces_configurable_message_size_limit() -> None:
    envelope = _tool_envelope(
        payload={
            "arguments": {"value": "x" * 100},
            "context": {
                "tool_id": "forge.tool.detect",
                "implementation_id": "yolo",
                "metadata": {},
            },
        }
    )

    with pytest.raises(ToolProtocolError) as encoded_error:
        encode_envelope(envelope, max_message_bytes=32)
    assert encoded_error.value.code == "FORGE_PROTOCOL_MESSAGE_TOO_LARGE"

    encoded = encode_envelope(envelope)
    with pytest.raises(ToolProtocolError) as decoded_error:
        decode_envelope(encoded, max_message_bytes=32)
    assert decoded_error.value.code == "FORGE_PROTOCOL_MESSAGE_TOO_LARGE"
