"""Transport-independent endpoint request dispatch and Action execution binding."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TypeAlias, cast

from .endpoint import (
    ActionToolEndpoint,
    QueryToolEndpoint,
    SessionToolEndpoint,
    ToolAccepted,
    ToolContext,
    ToolControlResponse,
    ToolEndpointDescriptor,
    ToolEndpointError,
    ToolError,
    ToolEvent,
    ToolExecutionKey,
    ToolExecutionStatus,
    ToolResult,
    ToolResultResponse,
    validate_execution_result,
)
from .wire import (
    TOOL_ENDPOINT_PROTOCOL,
    ToolEnvelope,
    ToolProtocolError,
    control_request_from_payload,
    invoke_request_from_envelope,
    make_control_response_envelope,
    make_error_response_envelope,
    make_event_envelope,
    make_invoke_response_envelope,
    make_result_response_envelope,
    make_status_response_envelope,
    validate_message_envelope,
)

ToolEventSink: TypeAlias = Callable[[ToolEnvelope], Awaitable[None]]

_REQUIRED_METHODS = {
    "query": ("query",),
    "action": ("start", "cancel", "status", "result"),
    "session": ("start", "stop", "status", "result"),
}
_REQUEST_MESSAGE_TYPES = frozenset(
    (
        "tool.invoke.request",
        "tool.status.request",
        "tool.result.request",
        "tool.control.request",
    )
)
_TERMINAL_PHASES = frozenset(
    ("completed", "failed", "cancelled", "stopped", "unknown")
)
_TERMINAL_EVENT_PHASES = {
    "executor_completed": "completed",
    "executor_failed": "failed",
    "cancelled": "cancelled",
    "stopped": "stopped",
}
_ALLOWED_PHASE_TRANSITIONS = {
    "accepted": frozenset(("accepted", "running", "stopping", *_TERMINAL_PHASES)),
    "running": frozenset(("running", "stopping", *_TERMINAL_PHASES)),
    "stopping": frozenset(("stopping", *_TERMINAL_PHASES)),
}


@dataclass
class _ExecutionRecord:
    operation: str
    context: ToolContext | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: ToolAccepted | ToolResult | ToolError | None = None
    status: ToolExecutionStatus | None = None
    result: ToolResult | None = None
    emitter: _ActionEventEmitter | None = None
    invoke_started: bool = False
    permit_held: bool = False


class _ActionEventEmitter:
    __slots__ = (
        "_accepted",
        "_buffer",
        "_closed",
        "_context",
        "_endpoint",
        "_handler",
        "_lock",
        "_max_early_events",
        "_sequence",
        "_sink",
        "_terminal_pending",
    )

    def __init__(
        self,
        handler: ToolEndpointHandler,
        record: _ExecutionRecord,
        endpoint: ActionToolEndpoint | SessionToolEndpoint,
        *,
        sink: ToolEventSink | None,
        max_early_events: int,
    ) -> None:
        self._handler = handler
        self._context = record.context
        self._endpoint = endpoint
        self._sink = sink
        self._max_early_events = max_early_events
        self._lock = asyncio.Lock()
        self._buffer: list[ToolEnvelope] = []
        self._sequence = 0
        self._accepted = False
        self._closed = False
        self._terminal_pending = False

    async def emit(self, event: ToolEvent) -> None:
        """Validate, sequence, and expose one event for the bound execution attempt."""
        if not isinstance(event, ToolEvent):
            raise TypeError("event must be a ToolEvent")

        terminal_phase = _TERMINAL_EVENT_PHASES.get(event.type)
        async with self._lock:
            if self._closed or self._terminal_pending:
                raise ToolProtocolError(
                    "FORGE_ENDPOINT_EVENT_AFTER_TERMINAL",
                    "an event was emitted after terminal event progression closed",
                )
            if terminal_phase is not None:
                self._terminal_pending = True

        if terminal_phase is not None:
            try:
                await self._handler._establish_terminal_from_event(
                    self._context.execution_key,
                    self._endpoint,
                    terminal_phase,
                )
            except BaseException:
                async with self._lock:
                    self._terminal_pending = False
                raise

        async with self._lock:
            if not self._accepted and len(self._buffer) >= self._max_early_events:
                self._terminal_pending = False
                raise ToolProtocolError(
                    "FORGE_ENDPOINT_EARLY_EVENT_BUFFER_OVERFLOW",
                    (
                        "Tool endpoint emitted more than "
                        f"{self._max_early_events} events before acceptance"
                    ),
                )
            envelope = make_event_envelope(
                event,
                self._context,
                endpoint_instance_id=self._handler.endpoint_instance_id,
                sequence=self._sequence,
            )
            self._sequence += 1
            if terminal_phase is not None:
                self._closed = True
                self._terminal_pending = False
            if not self._accepted:
                self._buffer.append(envelope)
                return
            if self._sink is None:
                raise RuntimeError(
                    "Tool endpoint emitted after acceptance without an asynchronous "
                    "event sink"
                )
            # Keep the emitter lock across the asynchronous sink so concurrent emitters
            # cannot physically publish a later sequence first.
            await self._sink(envelope)

    async def accept(self) -> tuple[ToolEnvelope, ...]:
        """Establish acceptance and return buffered events in strict sequence order."""
        async with self._lock:
            self._accepted = True
            events = tuple(self._buffer)
            self._buffer.clear()
            return events

    async def reject(self) -> None:
        """Close an emitter whose start call did not establish acceptance."""
        async with self._lock:
            self._closed = True
            self._buffer.clear()

    async def close_terminal(self) -> None:
        """Close progression after terminal status/result is established elsewhere."""
        async with self._lock:
            self._closed = True


def _validate_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.isspace():
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{field_name} must contain valid Unicode scalar values")
    return value


def _validate_implementation(
    operation_name: str,
    semantics: str,
    implementation: object,
) -> None:
    for method_name in _REQUIRED_METHODS[semantics]:
        if not callable(getattr(implementation, method_name, None)):
            raise TypeError(
                f"operation {operation_name!r} with {semantics} semantics requires "
                f"a callable {method_name}() method"
            )


class ToolEndpointHandler:
    """Bind endpoint implementations and dispatch Query/Action/Session execution messages.

    Action and Session transport state is deliberately module-private. Provider methods
    remain the authority for business status, result, and control decisions. Control
    dispatch is semantics-scoped: cancel is only admitted for Action operations and
    stop only for Session operations.
    """

    __slots__ = (
        "_active_by_operation",
        "_descriptor",
        "_endpoint_instance_id",
        "_executions",
        "_lock",
        "_max_early_events",
        "_max_retained_executions",
        "_implementations",
        "_operation_descriptors",
    )

    def __init__(
        self,
        descriptor: ToolEndpointDescriptor,
        *,
        endpoint_instance_id: str,
        operations: Mapping[
            str,
            QueryToolEndpoint | ActionToolEndpoint | SessionToolEndpoint,
        ],
        max_early_events: int = 32,
        max_retained_executions: int = 1_024,
    ) -> None:
        if not isinstance(descriptor, ToolEndpointDescriptor):
            raise TypeError("descriptor must be a ToolEndpointDescriptor")
        if descriptor.protocol_version != TOOL_ENDPOINT_PROTOCOL:
            raise ValueError(
                f"descriptor.protocol_version must equal {TOOL_ENDPOINT_PROTOCOL!r}"
            )
        if not isinstance(operations, Mapping):
            raise TypeError("operations must be a mapping")
        if (
            isinstance(max_early_events, bool)
            or not isinstance(max_early_events, int)
            or max_early_events <= 0
        ):
            raise ValueError("max_early_events must be a positive integer")
        if (
            isinstance(max_retained_executions, bool)
            or not isinstance(max_retained_executions, int)
            or max_retained_executions <= 0
        ):
            raise ValueError("max_retained_executions must be a positive integer")

        implementations: dict[str, object] = {}
        for operation_name, implementation in operations.items():
            if not isinstance(operation_name, str):
                raise TypeError("operation implementation names must be strings")
            implementations[operation_name] = implementation

        operation_descriptors = {
            operation.name: operation for operation in descriptor.operations
        }
        declared_names = set(operation_descriptors)
        implemented_names = set(implementations)
        missing = sorted(declared_names - implemented_names)
        unexpected = sorted(implemented_names - declared_names)
        if missing or unexpected:
            details: list[str] = []
            if missing:
                details.append(f"missing implementations: {', '.join(missing)}")
            if unexpected:
                details.append(f"undeclared implementations: {', '.join(unexpected)}")
            raise ValueError(
                "operation mapping does not match descriptor: " + "; ".join(details)
            )

        for operation_name, operation_descriptor in operation_descriptors.items():
            _validate_implementation(
                operation_name,
                operation_descriptor.semantics,
                implementations[operation_name],
            )

        self._descriptor = descriptor
        self._endpoint_instance_id = _validate_identifier(
            endpoint_instance_id,
            "endpoint_instance_id",
        )
        self._active_by_operation = {
            operation.name: 0 for operation in descriptor.operations
        }
        self._executions: OrderedDict[ToolExecutionKey, _ExecutionRecord] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_early_events = max_early_events
        self._max_retained_executions = max_retained_executions
        self._implementations = implementations
        self._operation_descriptors = operation_descriptors

    @property
    def descriptor(self) -> ToolEndpointDescriptor:
        """Return the immutable descriptor associated with this handler."""
        return self._descriptor

    @property
    def endpoint_instance_id(self) -> str:
        """Return the process-start identity accepted by this handler."""
        return self._endpoint_instance_id

    @property
    def operation_names(self) -> tuple[str, ...]:
        """Return bound operation names in descriptor order."""
        return tuple(operation.name for operation in self._descriptor.operations)

    def validate_request_route(self, request: ToolEnvelope) -> None:
        """Validate a trusted execution-request route before payload handling."""
        if not isinstance(request, ToolEnvelope):
            raise TypeError("request must be a ToolEnvelope")
        if request.message_type not in _REQUEST_MESSAGE_TYPES:
            raise ValueError("request envelope must be an execution request")

        if request.endpoint_id != self._descriptor.endpoint_id:
            raise ToolProtocolError(
                "FORGE_PROTOCOL_ROUTE_MISMATCH",
                (
                    f"request targets endpoint {request.endpoint_id!r}, expected "
                    f"{self._descriptor.endpoint_id!r}"
                ),
                path="endpoint_id",
            )
        if request.endpoint_instance_id != self._endpoint_instance_id:
            raise ToolProtocolError(
                "FORGE_PROTOCOL_ROUTE_MISMATCH",
                (
                    "request targets endpoint instance "
                    f"{request.endpoint_instance_id!r}, expected "
                    f"{self._endpoint_instance_id!r}"
                ),
                path="endpoint_instance_id",
            )

        operation_name = cast(str, request.operation)
        if operation_name not in self._operation_descriptors:
            raise ToolProtocolError(
                "FORGE_PROTOCOL_UNKNOWN_OPERATION",
                f"endpoint does not declare operation {operation_name!r}",
                path="operation",
            )

    def validate_invoke_route(self, request: ToolEnvelope) -> None:
        """Validate a trusted invoke route (backward-compatible Query API helper)."""
        if not isinstance(request, ToolEnvelope):
            raise TypeError("request must be a ToolEnvelope")
        if request.message_type != "tool.invoke.request":
            raise ValueError("request envelope must be tool.invoke.request")
        self.validate_request_route(request)

    @staticmethod
    def _protocol_error_response(
        error: ToolProtocolError,
        request: ToolEnvelope,
    ) -> ToolEnvelope:
        details = {"path": error.path} if error.path is not None else {}
        return make_error_response_envelope(
            ToolError(code=error.code, message=error.message, details=details),
            request,
        )

    @staticmethod
    def _endpoint_error_response(
        error: ToolEndpointError,
        request: ToolEnvelope,
    ) -> ToolEnvelope:
        return make_error_response_envelope(error.error, request)

    @staticmethod
    def _execution_key(request: ToolEnvelope) -> ToolExecutionKey:
        return ToolExecutionKey(
            invocation_id=cast(str, request.invocation_id),
            attempt_id=cast(str, request.attempt_id),
        )

    @staticmethod
    def _capacity_error(message: str) -> ToolError:
        return ToolError(
            code="FORGE_ENDPOINT_CAPACITY",
            message=message,
            retryable=True,
        )

    def _evict_one_locked(self) -> bool:
        for retained_key, retained in self._executions.items():
            if not retained.permit_held and (
                not retained.invoke_started or retained.ready.is_set()
            ):
                del self._executions[retained_key]
                return True
        return False

    def _make_room_locked(self) -> bool:
        while len(self._executions) >= self._max_retained_executions:
            if not self._evict_one_locked():
                return False
        return True

    def _release_permit_locked(self, record: _ExecutionRecord) -> None:
        if not record.permit_held:
            return
        record.permit_held = False
        active = self._active_by_operation[record.operation]
        if active <= 0:
            raise RuntimeError("Execution admission permit count became negative")
        self._active_by_operation[record.operation] = active - 1

    @staticmethod
    def _validate_phase_transition(
        previous: ToolExecutionStatus | None,
        current: ToolExecutionStatus,
    ) -> None:
        if previous is None:
            return
        if previous.phase in _TERMINAL_PHASES:
            if previous != current:
                raise ToolProtocolError(
                    "FORGE_ENDPOINT_TERMINAL_CHANGED",
                    "provider returned a different immutable terminal status",
                )
            return
        allowed = _ALLOWED_PHASE_TRANSITIONS[previous.phase]
        if current.phase not in allowed:
            raise ToolProtocolError(
                "FORGE_ENDPOINT_INVALID_TRANSITION",
                f"provider status changed from {previous.phase!r} to {current.phase!r}",
            )

    def _observation_record_locked(
        self,
        key: ToolExecutionKey,
        operation: str,
    ) -> _ExecutionRecord:
        record = self._executions.get(key)
        if record is not None:
            if record.operation != operation:
                raise ToolProtocolError(
                    "FORGE_PROTOCOL_EXECUTION_CONFLICT",
                    "execution key is already bound to a different operation",
                    path="operation",
                )
            self._executions.move_to_end(key)
            return record
        if not self._make_room_locked():
            raise ToolProtocolError(
                "FORGE_ENDPOINT_RETENTION_FULL",
                "execution retention is full of active records",
            )
        record = _ExecutionRecord(operation=operation)
        self._executions[key] = record
        return record

    async def _record_status(
        self,
        key: ToolExecutionKey,
        operation: str,
        status: ToolExecutionStatus,
    ) -> None:
        async with self._lock:
            record = self._observation_record_locked(key, operation)
            self._validate_phase_transition(record.status, status)
            record.status = status
            self._executions.move_to_end(key)

    async def _record_terminal(
        self,
        key: ToolExecutionKey,
        operation: str,
        status: ToolExecutionStatus,
        result: ToolResult,
        *,
        close_emitter: bool = True,
    ) -> None:
        try:
            validate_execution_result(status, result)
        except ValueError as error:
            raise ToolProtocolError(
                "FORGE_ENDPOINT_TERMINAL_MISMATCH",
                str(error),
            ) from error
        emitter: _ActionEventEmitter | None = None
        async with self._lock:
            record = self._observation_record_locked(key, operation)
            self._validate_phase_transition(record.status, status)
            if record.result is not None and record.result != result:
                raise ToolProtocolError(
                    "FORGE_ENDPOINT_TERMINAL_CHANGED",
                    "provider returned a different immutable terminal result",
                )
            record.status = status
            record.result = result
            if not record.invoke_started:
                record.outcome = result
                record.ready.set()
            self._release_permit_locked(record)
            self._executions.move_to_end(key)
            if close_emitter:
                emitter = record.emitter
        if emitter is not None:
            await emitter.close_terminal()

    async def _require_terminal_result(
        self,
        key: ToolExecutionKey,
        operation: str,
        endpoint: ActionToolEndpoint | SessionToolEndpoint,
        status: ToolExecutionStatus,
    ) -> ToolResult:
        response = await endpoint.result(key)
        if not isinstance(response, ToolResultResponse):
            raise TypeError("Tool endpoint result() must return a ToolResultResponse")
        if response.status != "available" or response.result is None:
            raise ToolProtocolError(
                "FORGE_ENDPOINT_TERMINAL_RESULT_UNAVAILABLE",
                "provider exposed a terminal phase before its authoritative result",
            )
        await self._record_terminal(key, operation, status, response.result)
        return response.result

    async def _establish_terminal_from_event(
        self,
        key: ToolExecutionKey,
        endpoint: ActionToolEndpoint | SessionToolEndpoint,
        phase: str,
    ) -> None:
        response = await endpoint.result(key)
        if not isinstance(response, ToolResultResponse):
            raise TypeError("Tool endpoint result() must return a ToolResultResponse")
        if response.status != "available" or response.result is None:
            raise ToolProtocolError(
                "FORGE_ENDPOINT_TERMINAL_RESULT_UNAVAILABLE",
                "provider emitted a terminal event before its authoritative result",
            )
        result = response.result
        status = ToolExecutionStatus(
            phase=phase,  # type: ignore[arg-type]
            error=result.error if phase in ("failed", "unknown") else None,
        )
        async with self._lock:
            record = self._executions.get(key)
            if record is None:
                raise RuntimeError("Tool event has no execution record")
            operation = record.operation
        await self._record_terminal(
            key,
            operation,
            status,
            result,
            close_emitter=False,
        )

    async def _handle_query_invoke(
        self,
        request: ToolEnvelope,
        operation_name: str,
    ) -> tuple[ToolEnvelope, ...]:
        endpoint_request, context = invoke_request_from_envelope(request)
        endpoint = cast(QueryToolEndpoint, self._implementations[operation_name])
        try:
            result = await endpoint.query(endpoint_request, context)
        except ToolEndpointError as error:
            return (make_invoke_response_envelope(error.error, request),)
        if not isinstance(result, ToolResult):
            raise TypeError("QueryToolEndpoint.query() must return a ToolResult")
        return (make_invoke_response_envelope(result, request),)

    async def _handle_start_invoke(
        self,
        request: ToolEnvelope,
        operation_name: str,
        event_sink: ToolEventSink | None,
    ) -> tuple[ToolEnvelope, ...]:
        endpoint_request, context = invoke_request_from_envelope(request)
        key = context.execution_key
        endpoint = cast(
            ActionToolEndpoint | SessionToolEndpoint,
            self._implementations[operation_name],
        )

        async with self._lock:
            record = self._executions.get(key)
            if record is not None and record.operation != operation_name:
                raise ToolProtocolError(
                    "FORGE_PROTOCOL_EXECUTION_CONFLICT",
                    "execution key is already bound to a different operation",
                    path="operation",
                )
            if record is not None:
                self._executions.move_to_end(key)
                duplicate = record.invoke_started or record.outcome is not None
                if not duplicate:
                    return (
                        make_invoke_response_envelope(
                            ToolError(
                                code="FORGE_ENDPOINT_EXECUTION_ALREADY_OBSERVED",
                                message=(
                                    "execution key has existing provider state but no "
                                    "replayable invoke outcome"
                                ),
                            ),
                            request,
                        ),
                    )
            else:
                operation_descriptor = self._operation_descriptors[operation_name]
                if (
                    self._active_by_operation[operation_name]
                    >= operation_descriptor.max_concurrency
                ):
                    return (
                        make_invoke_response_envelope(
                            self._capacity_error(
                                f"operation {operation_name!r} is at max_concurrency"
                            ),
                            request,
                        ),
                    )
                if not self._make_room_locked():
                    return (
                        make_invoke_response_envelope(
                            self._capacity_error(
                                "execution retention is full of active records"
                            ),
                            request,
                        ),
                    )
                record = _ExecutionRecord(
                    operation=operation_name,
                    context=context,
                    invoke_started=True,
                    permit_held=True,
                )
                self._executions[key] = record
                self._active_by_operation[operation_name] += 1
                duplicate = False

        if duplicate:
            await record.ready.wait()
            if record.outcome is None:
                raise RuntimeError("Execution record became ready without an outcome")
            return (make_invoke_response_envelope(record.outcome, request),)

        emitter = _ActionEventEmitter(
            self,
            record,
            endpoint,
            sink=event_sink,
            max_early_events=self._max_early_events,
        )
        record.emitter = emitter
        try:
            accepted = await endpoint.start(endpoint_request, context, emitter)
            if not isinstance(accepted, ToolAccepted):
                raise TypeError("Tool endpoint start() must return a ToolAccepted")
        except ToolEndpointError as error:
            await emitter.reject()
            async with self._lock:
                if record.result is None:
                    outcome = error.error
                    self._release_permit_locked(record)
                else:
                    outcome = record.result
                record.outcome = outcome
                record.ready.set()
            return (make_invoke_response_envelope(outcome, request),)
        except asyncio.CancelledError:
            await asyncio.shield(self._finish_unknown(record, emitter))
            raise
        except Exception:
            unknown = await self._finish_unknown(record, emitter)
            return (make_invoke_response_envelope(unknown, request),)

        async with self._lock:
            if record.result is not None:
                outcome: ToolAccepted | ToolResult = record.result
            else:
                if record.status is None:
                    record.status = ToolExecutionStatus(
                        phase="accepted",
                        details=accepted.details,
                    )
                outcome = accepted
            record.outcome = outcome
            record.ready.set()
        early_events = await emitter.accept()
        return (make_invoke_response_envelope(outcome, request), *early_events)

    async def _finish_unknown(
        self,
        record: _ExecutionRecord,
        emitter: _ActionEventEmitter,
    ) -> ToolResult:
        await emitter.reject()
        async with self._lock:
            if record.result is not None:
                record.outcome = record.result
                record.ready.set()
                return record.result
            unknown = ToolResult(
                status="unknown",
                error=ToolError(
                    code="FORGE_ENDPOINT_OUTCOME_UNKNOWN",
                    message=(
                        "Start failed after dispatch; the execution outcome "
                        "cannot be recovered"
                    ),
                    retryable=False,
                ),
            )
            record.outcome = unknown
            record.status = ToolExecutionStatus(
                phase="unknown",
                error=unknown.error,
            )
            record.result = unknown
            self._release_permit_locked(record)
            record.ready.set()
        return unknown

    async def handle_invoke(
        self,
        request: ToolEnvelope,
    ) -> ToolEnvelope:
        """Handle one legacy Query invoke and return its correlated response."""
        self.validate_invoke_route(request)
        operation_name = cast(str, request.operation)
        if self._operation_descriptors[operation_name].semantics != "query":
            raise NotImplementedError(
                "handle_invoke is Query-only; Action or Session operations must use "
                "dispatch"
            )
        messages = await self.dispatch(request)
        return messages[0]

    async def handle_status(self, request: ToolEnvelope) -> ToolEnvelope:
        """Dispatch one Action/Session status lookup to the authoritative provider."""
        messages = await self.dispatch(request)
        return messages[0]

    async def handle_result(self, request: ToolEnvelope) -> ToolEnvelope:
        """Dispatch one Action/Session result lookup to the authoritative provider."""
        messages = await self.dispatch(request)
        return messages[0]

    async def handle_control(self, request: ToolEnvelope) -> ToolEnvelope:
        """Dispatch one Action/Session control request to the authoritative provider."""
        messages = await self.dispatch(request)
        return messages[0]

    async def _handle_status(
        self,
        request: ToolEnvelope,
        endpoint: ActionToolEndpoint | SessionToolEndpoint,
        key: ToolExecutionKey,
    ) -> ToolEnvelope:
        record = self._executions.get(key)
        if (
            record is not None
            and record.status is not None
            and record.status.phase in _TERMINAL_PHASES
        ):
            return make_status_response_envelope(record.status, request)
        status = await endpoint.status(key)
        if not isinstance(status, ToolExecutionStatus):
            raise TypeError("Tool endpoint status() must return a ToolExecutionStatus")
        if status.phase in _TERMINAL_PHASES:
            await self._require_terminal_result(
                key,
                cast(str, request.operation),
                endpoint,
                status,
            )
        else:
            await self._record_status(key, cast(str, request.operation), status)
        return make_status_response_envelope(status, request)

    async def _handle_result(
        self,
        request: ToolEnvelope,
        endpoint: ActionToolEndpoint | SessionToolEndpoint,
        key: ToolExecutionKey,
    ) -> ToolEnvelope:
        record = self._executions.get(key)
        if record is not None and record.result is not None:
            return make_result_response_envelope(
                ToolResultResponse(status="available", result=record.result),
                request,
            )
        response = await endpoint.result(key)
        if not isinstance(response, ToolResultResponse):
            raise TypeError("Tool endpoint result() must return a ToolResultResponse")
        if response.status == "available":
            status = await endpoint.status(key)
            if not isinstance(status, ToolExecutionStatus):
                raise TypeError(
                    "Tool endpoint status() must return a ToolExecutionStatus"
                )
            await self._record_terminal(
                key,
                cast(str, request.operation),
                status,
                cast(ToolResult, response.result),
            )
        return make_result_response_envelope(response, request)

    async def _handle_control(
        self,
        request: ToolEnvelope,
        endpoint: ActionToolEndpoint | SessionToolEndpoint,
        key: ToolExecutionKey,
    ) -> ToolEnvelope:
        command, reason = control_request_from_payload(request.payload)
        semantics = self._operation_descriptors[cast(str, request.operation)].semantics
        if command == "cancel" and semantics == "action":
            response = await cast(ActionToolEndpoint, endpoint).cancel(key, reason)
        elif command == "stop" and semantics == "session":
            response = await cast(SessionToolEndpoint, endpoint).stop(key, reason)
        else:
            response = ToolControlResponse(command=command, status="unsupported")
            return make_control_response_envelope(response, request)
        if not isinstance(response, ToolControlResponse):
            raise TypeError(
                f"Tool endpoint {command}() must return a ToolControlResponse"
            )
        if response.command != command:
            raise ToolProtocolError(
                "FORGE_PROTOCOL_CORRELATION_MISMATCH",
                "provider control response command does not match request",
                path="payload.response.command",
            )
        return make_control_response_envelope(response, request)

    async def dispatch(
        self,
        request: ToolEnvelope,
        *,
        event_sink: ToolEventSink | None = None,
    ) -> tuple[ToolEnvelope, ...]:
        """Dispatch one execution request; response is first, then buffered events."""
        self.validate_request_route(request)

        operation_name = cast(str, request.operation)
        operation_descriptor = self._operation_descriptors[operation_name]

        try:
            validate_message_envelope(request)
        except ToolProtocolError as error:
            return (self._protocol_error_response(error, request),)

        if request.message_type == "tool.invoke.request":
            if operation_descriptor.semantics == "query":
                return await self._handle_query_invoke(request, operation_name)
            return await self._handle_start_invoke(
                request,
                operation_name,
                event_sink,
            )
        if operation_descriptor.semantics not in ("action", "session"):
            raise ToolProtocolError(
                "FORGE_PROTOCOL_UNSUPPORTED_OPERATION",
                "Query operations do not support status, result, or control requests",
                path="operation",
            )

        endpoint = cast(
            ActionToolEndpoint | SessionToolEndpoint,
            self._implementations[operation_name],
        )
        key = self._execution_key(request)
        try:
            if request.message_type == "tool.status.request":
                response = await self._handle_status(request, endpoint, key)
            elif request.message_type == "tool.result.request":
                response = await self._handle_result(request, endpoint, key)
            else:
                response = await self._handle_control(request, endpoint, key)
        except ToolEndpointError as error:
            return (self._endpoint_error_response(error, request),)
        except ToolProtocolError as error:
            return (self._protocol_error_response(error, request),)
        return (response,)
