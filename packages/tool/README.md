# forge-tool

`forge-tool` is the dependency-free Python implementation of the provider-side Forge
ToolEndpoint contracts and logical Wire protocol
`forge.tool.endpoint/v1alpha1`.

It provides:

- validated endpoint descriptors, requests, results, status, control, events, and errors;
- async Query, Action, and Session ToolEndpoint SPI;
- strict logical envelopes, typed payload codecs, complete factories, and correlation
  validators;
- a transport-independent Query/Action/Session `ToolEndpointHandler`; and
- an optional in-memory Arrow binding for embedding the handler in a Dora Operator or
  Adapter node.

It does **not** provide the caller-facing Tool Runtime, a Gateway, a Dora `Node`, or a
persistent execution service.

## Capability status

| Capability | Query | Action | Session |
| --- | --- | --- | --- |
| Models, descriptor, and SPI | Implemented | Implemented | Implemented |
| Logical Wire messages | Implemented | Implemented | Implemented |
| `ToolEndpointHandler` execution | Implemented | Implemented | Implemented |
| Optional Arrow/Dora binding | Implemented | Implemented | Implemented |

Action and Session operations share the handler's bounded execution lifecycle. Their
control semantics remain distinct: Action admits `cancel`, while Session admits `stop`.

The sibling Gateway integration is currently Query-only. Provider-side Action and Session
handling therefore does not imply a complete caller-to-provider Action or Session system.

## Installation

Python 3.11 or newer is required. Install the current Tool release from PyPI:

```bash
pip install 'forge-tool>=1.0.1,<2'
```

Install the optional Arrow carrier integration when embedding an endpoint in a
Dora-capable Operator or Adapter node:

```bash
pip install 'forge-tool[dora]>=1.0.1,<2'
```

The complete Forge source workspace still requires Python 3.12 or newer. From a
source checkout, run code through that workspace:

```bash
uv run python your_endpoint.py
```

For Python 3.11, install the standalone Tool distribution into a separate environment;
the workspace also includes packages that require Python 3.12.

The base package has no runtime dependencies. `import forge_tool` does not import
`forge_msgs` or PyArrow. The optional extra adds the carrier stack, not `dora-rs`; the
concrete node package remains responsible for its Dora dependency.

## Query quickstart

A Query returns one authoritative terminal `ToolResult`.

```python
import asyncio

from forge_tool import (
    TOOL_ENDPOINT_PROTOCOL,
    ToolContext,
    ToolEndpointDescriptor,
    ToolEndpointHandler,
    ToolExecutionKey,
    ToolOperationDescriptor,
    ToolRequest,
    ToolResult,
    invoke_response_from_payload,
    make_invoke_request_envelope,
)


class DetectQuery:
    async def query(
        self,
        request: ToolRequest,
        context: ToolContext,
    ) -> ToolResult:
        return ToolResult(
            status="succeeded",
            outputs={"requested_class": request.arguments.get("class")},
        )


async def main() -> None:
    descriptor = ToolEndpointDescriptor(
        protocol_version=TOOL_ENDPOINT_PROTOCOL,
        endpoint_id="vision.detector",
        operations=(
            ToolOperationDescriptor(name="detect", semantics="query"),
        ),
    )
    handler = ToolEndpointHandler(
        descriptor,
        endpoint_instance_id="detector-process-1",
        operations={"detect": DetectQuery()},
    )

    context = ToolContext(
        execution_key=ToolExecutionKey("invocation-1", "attempt-1"),
        tool_id="vision.detect",
        implementation_id="detector.primary",
        endpoint_id="vision.detector",
        operation="detect",
    )
    request = make_invoke_request_envelope(
        ToolRequest(arguments={"class": "cube"}),
        context,
        request_id="request-1",
        endpoint_instance_id="detector-process-1",
    )

    response = await handler.handle_invoke(request)
    result = invoke_response_from_payload(response.payload)
    assert isinstance(result, ToolResult)
    print(result.outputs)


asyncio.run(main())
```

`handle_invoke()` is intentionally Query-only. It validates the concrete provider route,
decodes the typed request, calls `query()`, and builds a correlated
`tool.invoke.response`.

Use `ToolEndpointError(ToolError(...))` only to reject a request **before** execution is
accepted or side effects begin. Once execution has begun, represent a Query failure as a
terminal `ToolResult(status="failed", error=...)`.

## Action and Session endpoints

An Action or Session normally admits work with `ToolAccepted`, then exposes authoritative
status, result, semantics-scoped control, and low-rate events. Action uses optional
cancellation; Session uses optional stopping. Either may instead return an initial terminal
`ToolResult` when completion was established during `start()`, including terminal `unknown`
when dispatch may have begun but acceptance cannot be established.

```python
from forge_tool import (
    ToolAccepted,
    ToolContext,
    ToolControlResponse,
    ToolEvent,
    ToolEventEmitter,
    ToolExecutionKey,
    ToolExecutionStatus,
    ToolRequest,
    ToolResultResponse,
)


class MoveAction:
    async def start(
        self,
        request: ToolRequest,
        context: ToolContext,
        events: ToolEventEmitter,
    ) -> ToolAccepted:
        await events.emit(ToolEvent(type="progress", data={"fraction": 0.0}))
        # Start the business executor here and retain its private handle by
        # context.execution_key.
        return ToolAccepted(details={"executor": "arm"})

    async def cancel(
        self,
        key: ToolExecutionKey,
        reason: str | None = None,
    ) -> ToolControlResponse:
        return ToolControlResponse(command="cancel", status="accepted")

    async def status(self, key: ToolExecutionKey) -> ToolExecutionStatus:
        return ToolExecutionStatus(phase="running")

    async def result(self, key: ToolExecutionKey) -> ToolResultResponse:
        return ToolResultResponse(status="pending")
```

Declare it with Action semantics and call it from an async transport loop. This fragment
assumes the `MoveAction` class above and receives an already constructed request:

```python
from forge_tool import (
    TOOL_ENDPOINT_PROTOCOL,
    ToolEndpointDescriptor,
    ToolEndpointHandler,
    ToolEnvelope,
    ToolOperationDescriptor,
)

operation = ToolOperationDescriptor(
    name="move",
    semantics="action",
    cancellable=True,
    status_supported=True,
    max_concurrency=4,
)
descriptor = ToolEndpointDescriptor(
    protocol_version=TOOL_ENDPOINT_PROTOCOL,
    endpoint_id="motion.arm",
    operations=(operation,),
)
handler = ToolEndpointHandler(
    descriptor,
    endpoint_instance_id="arm-process-1",
    operations={"move": MoveAction()},
)

published_events: list[ToolEnvelope] = []


async def collect_event(event: ToolEnvelope) -> None:
    # Suitable for in-memory inspection. A transport publisher needs the
    # response-publication gate described below.
    published_events.append(event)


async def dispatch_action(request: ToolEnvelope) -> tuple[ToolEnvelope, ...]:
    messages = await handler.dispatch(request, event_sink=collect_event)
    # messages[0] is the correlated response; events emitted during start()
    # follow it in strict sequence order.
    return messages
```

The returned tuple establishes logical response-first ordering for events buffered during
`start()`. Once acceptance is established, the asynchronous event sink may run before a
direct transport has physically published `messages[0]`. A transport integration must gate
that sink until response publication succeeds; `DoraToolEndpointBinding` provides this
acknowledged publication barrier.

The provider remains authoritative for business status, result, and control decisions.

### Action and Session handler guarantees

The current Python handler provides provider-side lifecycle and logical-ordering guarantees:

- descriptor operation names and implementation mapping must match exactly;
- `max_concurrency` is enforced per Action or Session operation;
- the invoke response is first in the returned tuple, before events buffered during
  `start()`;
- an asynchronous event sink preserves sequence order, while its transport integration
  remains responsible for gating physical publication until the response succeeds;
- events are assigned sequence numbers from `0` in emission order;
- a terminal phase/event is not exposed until a matching authoritative result is
  available;
- terminal status and result are retained and immutable while the record is retained;
- an unrecoverable failure after dispatch may have started a side effect converges to
  terminal `unknown` and is not retried;
- a duplicate `ToolExecutionKey` does not restart the provider while its record remains
  in the bounded ledger.

These are **not** persistent idempotency or exactly-once guarantees. The ledger is
process-local and bounded; eviction or restart ends duplicate protection. Duplicate
requests are keyed by `invocation_id + attempt_id` and are not compared by arguments or
context fingerprint.

The handler enforces descriptor `max_concurrency` for Action and Session admission.
It validates but does not enforce `ToolContext.deadline_ms`; timeout ownership
belongs to the provider or future Runtime/Gateway policy.

## Handler entry points

| API | Supported behavior |
| --- | --- |
| `handle_invoke(request)` | Legacy single-response Query invoke only. Rejects Action or Session without starting it. |
| `dispatch(request, event_sink=...)` | Query invoke plus Action/Session invoke, status, result, and control. Response is first in the returned tuple. |
| `handle_status(request)` | Convenience wrapper for Action/Session status dispatch. |
| `handle_result(request)` | Convenience wrapper for Action/Session result dispatch. |
| `handle_control(request)` | Convenience wrapper for Action/Session control dispatch. |

Action admits `cancel`, and Session admits `stop`; a control command that does not match
the operation semantics returns `unsupported`.

## Optional Arrow/Dora binding

Import the optional module explicitly. The following embedding fragment assumes the
provider Operator/Adapter supplies an existing `handler` and async
`publish_arrow_value` function:

```python
from forge_tool.dora import DoraToolEndpointBinding

binding = DoraToolEndpointBinding(
    handler,
    max_message_bytes=1_048_576,
    max_carrier_bytes=1_114_112,
    event_sink=publish_arrow_value,
)
```

The binding accepts one exact `forge_msgs.ToolMessage` in-memory Arrow carrier:

- a one-row `pyarrow.RecordBatch`;
- a single-batch `pyarrow.Table`; or
- a `pyarrow.StructArray` accepted by `forge_msgs.ToolMessage`.

It rejects raw Arrow IPC bytes. Framing, compression, decompression, and bounded IPC
decoding belong to the upstream transport.

### Query

```python
async def handle_query_input(input_arrow_value):
    return await binding.handle_input(input_arrow_value)
```

`handle_input()` preserves the Query-only single-response API and returns one
`RecordBatch`. It rejects an Action or Session before calling `start()`.

### Action and Session

```python
async def handle_stateful_input(input_arrow_value) -> None:
    await binding.dispatch_input(input_arrow_value)
```

For an Action or Session invoke, the configured async `event_sink` is the acknowledged publisher
for **both** the invoke response and subsequent events. The binding waits for physical
response publication before opening the event gate, serializes events in sequence order,
and returns `()` after publication.

Other request types use the response-first returned-tuple form. A late-event encoding or
publication failure is raised from `ToolEventEmitter.emit()`. A buffered early-event
failure is raised from `dispatch_input()` after the accepted response may already have
been published. Neither path fabricates a second request-correlated error.

The binding does not:

- create or import a Dora `Node`;
- choose input/output IDs;
- own the event loop or call `asyncio.run()`;
- inspect or rewrite Dora event metadata;
- perform registration, route authorization, or Gateway resolution.

## Validation layers

A common source of integration bugs is assuming one layer validates everything.

| Layer | Responsibility |
| --- | --- |
| `forge_msgs.ToolMessage` | Exact Arrow schema, one row, generic identity/nullability rules, strict object-valued `payload_json`. |
| `ToolEnvelope` | Protocol/message type and generic logical header matrix; JSON-compatible payload object. |
| `validate_message_envelope`, codec, and factories | Message-specific closed payload schema and complete logical construction. |
| `ToolEndpointHandler` | Concrete provider route, descriptor operation semantics, SPI dispatch, and Action/Session lifecycle. |
| `DoraToolEndpointBinding` | Carrier/raw payload/logical size boundaries and bounded correlated error conversion after route trust. |

Outbound conversion validates the typed payload. Inbound carrier conversion first
establishes the generic envelope; after route trust, handler validation can return a
correlated `tool.error` for an invalid typed payload.

Failures before route trust—wrong carrier, oversized raw carrier, invalid generic
headers, or route mismatch—may be raised without a response. Once the route is trusted,
the binding attempts to return a bounded correlated error for invalid requests,
provider exceptions, and invalid or oversized responses.

## Current Python defaults

These are implementation defaults, not cross-language Wire constants.

| Resource | Default |
| --- | ---: |
| Logical encoded message | 1 MiB (`1_048_576` bytes) |
| Accepted binding request | logical maximum minus 512 bytes of correlated-error headroom |
| In-memory Arrow carrier | logical maximum plus 64 KiB of framing overhead |
| Early Action/Session events | 32 events |
| Retained Action/Session execution records | 1,024 records |
| JSON nesting | 64 levels |
| Interoperable JSON integer | `±(2^53-1)` |

`max_message_bytes` for `DoraToolEndpointBinding` must be at least 1 KiB, and
`max_carrier_bytes` must be at least `max_message_bytes`.

## Public API map

### Endpoint contracts and models

- SPI: `QueryToolEndpoint`, `ActionToolEndpoint`, `SessionToolEndpoint`,
  `ToolEventEmitter`
- descriptors: `ToolEndpointDescriptor`, `ToolOperationDescriptor`
- identity/context: `ToolExecutionKey`, `ToolContext`, `ToolRequest`
- outcomes: `ToolAccepted`, `ToolResult`, `ToolResultResponse`
- observation/control: `ToolExecutionStatus`, `ToolControlResponse`, `ToolEvent`
- errors/management: `ToolError`, `ToolEndpointError`, `EndpointStatus`,
  `EndpointRegistryResponse`

### Logical Wire

- `ToolEnvelope`, `ToolMessageType`, `TOOL_ENDPOINT_PROTOCOL`
- `encode_envelope`, `decode_envelope`
- `make_*_envelope` factories
- typed `*_to_payload` / `*_from_payload` adapters
- `validate_message_envelope`, `validate_response_correlation`,
  `validate_management_response_correlation`

Prefer complete `make_*_envelope` factories over manually assembling an envelope.
Response factories take the originating request and copy its correlation identities.

### Optional carrier API

Explicitly import from `forge_tool.dora`:

- `DoraToolEndpointBinding`
- `tool_message_to_envelope`
- `tool_envelope_to_message`

## Protocol documentation

- [Tool documentation index](https://github.com/PhyAgentOS/forge/blob/forge-tool-v1.0.1/interfaces/forge_tool/README.md)
- [Architecture](https://github.com/PhyAgentOS/forge/blob/forge-tool-v1.0.1/interfaces/forge_tool/ARCHITECTURE.md)
- [ToolEndpoint Wire protocol](https://github.com/PhyAgentOS/forge/blob/forge-tool-v1.0.1/interfaces/forge_tool/PROTOCOL.md)
- [Canonical `ToolMessage` Arrow schema](https://github.com/PhyAgentOS/forge/blob/forge-msgs-v1.3.1/interfaces/forge_msgs/tool.v1.yaml)

## Development

From the Forge repository root:

```bash
uv run pytest packages/tool/tests
```

## License

Apache-2.0. See the release [LICENSE](https://github.com/PhyAgentOS/forge/blob/forge-tool-v1.0.1/LICENSE).
