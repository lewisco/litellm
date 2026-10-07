import hashlib
from collections.abc import AsyncIterator
from typing import Final
from uuid import UUID

import httpx
import pytest
from pydantic import TypeAdapter

import litellm
from litellm.a2a_protocol.litellm_completion_bridge.handler import A2ACompletionBridgeHandler
from litellm.a2a_protocol.providers.n8n.config import N8nA2AConfig
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler

WEBHOOK_URL: Final = "https://n8n.test/webhook/published-agent/chat?deployment=current"
JSON_OBJECT: Final = TypeAdapter(dict[str, object])


class _Webhook:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: tuple[httpx.Request, ...] = ()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests = (*self.requests, request)
        return self.response


class _ChunkedBody(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _params(context_id: str | None = "conversation-1") -> dict[str, object]:
    return {
        "message": {
            "role": "user",
            "messageId": "user-message-1",
            "parts": [{"kind": "text", "text": "first line"}, {"kind": "text", "text": "second line"}],
            **({"contextId": context_id} if context_id is not None else {}),
        }
    }


def _result(response: dict[str, object]) -> dict[str, object]:
    return JSON_OBJECT.validate_python(response["result"])


def _uuid(value: object) -> str:
    assert isinstance(value, str)
    assert str(UUID(value)) == value
    return value


@pytest.mark.parametrize("response_field", ["output", "text", "response"])
async def test_non_streaming_translates_text_parts_at_the_full_webhook_url(response_field: str) -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={response_field: "agent answer"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        response: Final = await N8nA2AConfig(http_client=client).handle_non_streaming(
            "request-1", _params(), WEBHOOK_URL
        )

    result: Final = _result(response)
    assert response == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "result": {
            "kind": "message",
            "role": "agent",
            "messageId": _uuid(result["messageId"]),
            "contextId": "conversation-1",
            "parts": [{"kind": "text", "text": "agent answer"}],
        },
    }
    assert len(webhook.requests) == 1
    request: Final = webhook.requests[0]
    assert request.method == "POST"
    assert str(request.url) == WEBHOOK_URL
    assert JSON_OBJECT.validate_json(request.content) == {
        "action": "sendMessage",
        "chatInput": "first line\nsecond line",
        "sessionId": "conversation-1",
    }


async def test_generated_context_can_resume_the_same_scoped_session() -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))
    litellm_params: Final = {"litellm_a2a_user_api_key_hash": "caller-key-hash"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        config: Final = N8nA2AConfig(http_client=client)
        first: Final = await config.handle_non_streaming(
            "request-1", _params(None), WEBHOOK_URL, litellm_params=litellm_params
        )
        context_id: Final = _uuid(_result(first)["contextId"])
        second: Final = await config.handle_non_streaming(
            "request-2", _params(context_id), WEBHOOK_URL, litellm_params=litellm_params
        )

    prefix: Final = hashlib.sha256(b"caller-key-hash").hexdigest()[:16]
    assert _result(second)["contextId"] == context_id
    assert len(webhook.requests) == 2
    assert tuple(JSON_OBJECT.validate_json(request.content) for request in webhook.requests) == (
        {"action": "sendMessage", "chatInput": "first line\nsecond line", "sessionId": f"{prefix}-{context_id}"},
        {"action": "sendMessage", "chatInput": "first line\nsecond line", "sessionId": f"{prefix}-{context_id}"},
    )
    assert _result(first)["messageId"] != _result(second)["messageId"]


async def test_same_context_is_isolated_between_authenticated_callers() -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        config: Final = N8nA2AConfig(http_client=client)
        first: Final = await config.handle_non_streaming(
            "request-1",
            _params(),
            WEBHOOK_URL,
            litellm_params={"litellm_a2a_user_api_key_hash": "first-key-hash"},
        )
        second: Final = await config.handle_non_streaming(
            "request-2",
            _params(),
            WEBHOOK_URL,
            litellm_params={"litellm_a2a_user_api_key_hash": "second-key-hash"},
        )

    first_session: Final = JSON_OBJECT.validate_json(webhook.requests[0].content)["sessionId"]
    second_session: Final = JSON_OBJECT.validate_json(webhook.requests[1].content)["sessionId"]
    assert first_session == f"{hashlib.sha256(b'first-key-hash').hexdigest()[:16]}-conversation-1"
    assert second_session == f"{hashlib.sha256(b'second-key-hash').hexdigest()[:16]}-conversation-1"
    assert first_session != second_session
    assert _result(first)["contextId"] == _result(second)["contextId"] == "conversation-1"


@pytest.mark.parametrize("timeout", [None, 7.5])
async def test_webhook_request_receives_the_configured_timeout(timeout: float | None) -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))
    litellm_params: Final[dict[str, object]] = {} if timeout is None else {"timeout": timeout}

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        response: Final = await N8nA2AConfig(http_client=client).handle_non_streaming(
            "request-1", _params(), WEBHOOK_URL, litellm_params=litellm_params
        )

    expected_timeout: Final = 60.0 if timeout is None else timeout
    assert webhook.requests[0].extensions["timeout"] == {
        "connect": expected_timeout,
        "read": expected_timeout,
        "write": expected_timeout,
        "pool": expected_timeout,
    }
    assert _result(response)["parts"] == [{"kind": "text", "text": "answer"}]


@pytest.mark.parametrize(
    ("litellm_params", "agent_headers", "expected_headers"),
    [
        ({"api_key": "webhook-api-key"}, {}, {"authorization": "Bearer webhook-api-key"}),
        (
            {"api_key": "webhook-api-key"},
            {"Authorization": "Basic caller-credential"},
            {"authorization": "Basic caller-credential"},
        ),
        (
            {
                "api_key": "webhook-api-key",
                "extra_headers": {"Authorization": "Basic configured-credential", "X-Workflow-Token": "configured"},
            },
            {"authorization": "Bearer caller-key", "X-Caller-Trace": "trace-1", "x-workflow-token": "caller-value"},
            {
                "authorization": "Basic configured-credential",
                "x-workflow-token": "configured",
                "x-caller-trace": "trace-1",
            },
        ),
    ],
)
async def test_configured_credentials_take_precedence_over_caller_headers(
    litellm_params: dict[str, object], agent_headers: dict[str, str], expected_headers: dict[str, str]
) -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        response: Final = await N8nA2AConfig(http_client=client).handle_non_streaming(
            "request-1",
            _params(),
            WEBHOOK_URL,
            litellm_params=litellm_params,
            agent_extra_headers=agent_headers,
        )

    request: Final = webhook.requests[0]
    assert {header: request.headers[header] for header in expected_headers} == expected_headers
    assert request.headers["content-type"] == "application/json"
    assert _result(response)["parts"] == [{"kind": "text", "text": "answer"}]


@pytest.mark.parametrize("api_base", [None, "", "n8n.test/webhook/chat", "ftp://n8n.test/webhook/chat"])
async def test_invalid_webhook_url_is_rejected_before_dispatch(api_base: str | None) -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        with pytest.raises(ValueError, match="api_base"):
            await N8nA2AConfig(http_client=client).handle_non_streaming("request-1", _params(), api_base)

    assert webhook.requests == ()


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"message": {"parts": []}},
        {"message": {"parts": [{"kind": "text", "text": ""}]}},
        {"message": {"parts": [{"kind": "text", "text": "   "}]}},
        {"message": {"parts": [{"kind": "file", "file": {"uri": "https://files.test/image.png"}}]}},
        {"message": {"parts": [{"kind": "text", "text": "prompt"}, {"kind": "data", "data": {"value": 1}}]}},
    ],
)
async def test_missing_or_unsupported_prompt_parts_are_rejected_before_dispatch(params: dict[str, object]) -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        with pytest.raises(ValueError, match=r"InputMessage|text"):
            await N8nA2AConfig(http_client=client).handle_non_streaming("request-1", params, WEBHOOK_URL)

    assert webhook.requests == ()


@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_upstream_error_status_is_preserved_without_retrying_agent_execution(status_code: int) -> None:
    webhook: Final = _Webhook(httpx.Response(status_code, json={"message": "upstream rejected request"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            await N8nA2AConfig(http_client=client).handle_non_streaming("request-1", _params(), WEBHOOK_URL)

    assert error.value.response.status_code == status_code
    assert error.value.response.json() == {"message": "upstream rejected request"}
    assert len(webhook.requests) == 1


@pytest.mark.parametrize("body", [{}, {"output": 42}, {"output": {"text": "nested"}}])
async def test_unsupported_upstream_response_cannot_become_an_empty_success(body: dict[str, object]) -> None:
    webhook: Final = _Webhook(httpx.Response(200, json=body))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        with pytest.raises(ValueError, match=r"Reply|response"):
            await N8nA2AConfig(http_client=client).handle_non_streaming("request-1", _params(), WEBHOOK_URL)

    assert len(webhook.requests) == 1


async def test_array_response_preserves_all_agent_text_in_order() -> None:
    webhook: Final = _Webhook(httpx.Response(200, json=[{"output": "first"}, {"text": "second"}]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        response: Final = await N8nA2AConfig(http_client=client).handle_non_streaming("request-1", _params(), WEBHOOK_URL)

    assert _result(response)["parts"] == [{"kind": "text", "text": "first\nsecond"}]


def _frame(event_type: str, content: str | None = None, node: str = "agent-node") -> bytes:
    frame: Final = {
        "type": event_type,
        "metadata": {"nodeId": node, "nodeName": node, "runIndex": 0, "itemIndex": 0, "timestamp": 1},
        **({"content": content} if content is not None else {}),
    }
    return JSON_OBJECT.dump_json(frame) + b"\n"


async def test_non_streaming_aggregates_chat_trigger_ndjson_across_node_runs() -> None:
    body: Final = _ChunkedBody(
        (
            _frame("begin")[:11],
            _frame("begin")[11:] + _frame("item", "first ") + b"\n",
            _frame("item", "answer") + _frame("end"),
            _frame("begin", node="second-node") + _frame("item", " again", node="second-node"),
            _frame("end", node="second-node"),
        )
    )
    webhook: Final = _Webhook(httpx.Response(200, headers={"content-type": "application/json"}, stream=body))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        response: Final = await N8nA2AConfig(http_client=client).handle_non_streaming("request-1", _params(), WEBHOOK_URL)

    assert _result(response)["parts"] == [{"kind": "text", "text": "first answer again"}]
    assert body.closed


def _artifact_update(
    task_id: str, artifact_id: str, text: str, last_chunk: bool = False, append: bool = True
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": "request-1",
        "result": {
            "kind": "artifact-update",
            "contextId": "conversation-1",
            "taskId": task_id,
            "artifact": {
                "artifactId": artifact_id,
                "name": "response",
                "parts": [{"kind": "text", "text": text}],
            },
            "append": append,
            "lastChunk": last_chunk,
        },
    }


async def test_streaming_emits_fragmented_deltas_before_eof_and_keeps_one_artifact_across_node_runs() -> None:
    begin: Final = _frame("begin")
    first_item: Final = _frame("item", "first ")
    body: Final = _ChunkedBody(
        (
            begin[:13],
            begin[13:] + first_item[:20],
            first_item[20:],
            _frame("item", "answer") + _frame("end"),
            _frame("begin", node="second-node"),
            _frame("item", " again", node="second-node"),
            _frame("end", node="second-node"),
        )
    )
    webhook: Final = _Webhook(httpx.Response(200, headers={"content-type": "application/json"}, stream=body))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        stream: Final = N8nA2AConfig(http_client=client).handle_streaming("request-1", _params(), WEBHOOK_URL)
        submitted: Final = await anext(stream)
        working: Final = await anext(stream)
        first_delta: Final = await anext(stream)
        assert body.consumed == 3
        assert not body.closed
        remaining: Final = tuple([event async for event in stream])

    task_id: Final = _uuid(_result(submitted)["id"])
    artifact_id: Final = _uuid(JSON_OBJECT.validate_python(_result(first_delta)["artifact"])["artifactId"])
    assert submitted == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "result": {
            "kind": "task",
            "id": task_id,
            "contextId": "conversation-1",
            "status": {"state": "submitted"},
            "history": [_params()["message"]],
        },
    }
    assert working == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "result": {
            "kind": "status-update",
            "taskId": task_id,
            "contextId": "conversation-1",
            "status": {"state": "working"},
            "final": False,
        },
    }
    assert first_delta == _artifact_update(task_id, artifact_id, "first ", append=False)
    assert remaining == (
        _artifact_update(task_id, artifact_id, "answer"),
        _artifact_update(task_id, artifact_id, " again"),
        _artifact_update(task_id, artifact_id, "", last_chunk=True),
        {
            "jsonrpc": "2.0",
            "id": "request-1",
            "result": {
                "kind": "status-update",
                "taskId": task_id,
                "contextId": "conversation-1",
                "status": {"state": "completed"},
                "final": True,
            },
        },
    )
    assert body.closed
    assert JSON_OBJECT.validate_json(webhook.requests[0].content) == {
        "action": "sendMessage",
        "chatInput": "first line\nsecond line",
        "sessionId": "conversation-1",
    }


@pytest.mark.parametrize(
    ("tail", "expected_error"),
    [
        (_frame("error", "tool execution failed"), "tool execution failed"),
        (b"", "ended before all messages completed"),
        (b"{broken-frame}\n", "Invalid JSON"),
    ],
)
async def test_stream_error_or_truncation_fails_instead_of_completing(tail: bytes, expected_error: str) -> None:
    body: Final = _ChunkedBody((_frame("begin"), _frame("item", "partial answer"), tail))
    webhook: Final = _Webhook(httpx.Response(200, headers={"content-type": "application/json"}, stream=body))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        events: Final = tuple(
            [event async for event in N8nA2AConfig(http_client=client).handle_streaming("request-1", _params(), WEBHOOK_URL)]
        )

    assert len(events) == 4
    failure: Final = _result(events[-1])
    status: Final = JSON_OBJECT.validate_python(failure["status"])
    message: Final = JSON_OBJECT.validate_python(status["message"])
    parts: Final = TypeAdapter(tuple[dict[str, object], ...]).validate_python(message["parts"])
    error_text: Final = parts[0]["text"]
    assert isinstance(error_text, str)
    assert expected_error in error_text
    assert events[-1] == {
        "jsonrpc": "2.0",
        "id": "request-1",
        "result": {
            "kind": "status-update",
            "taskId": _result(events[0])["id"],
            "contextId": "conversation-1",
            "status": {
                "state": "failed",
                "message": {
                    "kind": "message",
                    "messageId": _uuid(message["messageId"]),
                    "role": "agent",
                    "parts": [{"kind": "text", "text": error_text}],
                },
            },
            "final": True,
        },
    }
    assert body.closed


async def test_cancelling_the_stream_closes_the_upstream_response() -> None:
    body: Final = _ChunkedBody((_frame("begin"), _frame("item", "partial"), _frame("end")))
    webhook: Final = _Webhook(httpx.Response(200, headers={"content-type": "application/json"}, stream=body))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        stream: Final = N8nA2AConfig(http_client=client).handle_streaming("request-1", _params(), WEBHOOK_URL)
        submitted: Final = await anext(stream)
        working: Final = await anext(stream)
        partial: Final = await anext(stream)
        assert not body.closed
        await stream.aclose()
        assert not client.is_closed

    assert _result(submitted)["kind"] == "task"
    assert JSON_OBJECT.validate_python(_result(working)["status"]) == {"state": "working"}
    assert JSON_OBJECT.validate_python(_result(partial)["artifact"])["parts"] == [{"kind": "text", "text": "partial"}]
    assert body.closed
    assert body.consumed == 2


@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_streaming_preserves_upstream_status_before_emitting_any_events(status_code: int) -> None:
    webhook: Final = _Webhook(httpx.Response(status_code, text="upstream error"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        stream: Final = N8nA2AConfig(http_client=client).handle_streaming("request-1", _params(), WEBHOOK_URL)
        with pytest.raises(httpx.HTTPStatusError) as error:
            await anext(stream)

    assert error.value.response.status_code == status_code
    assert error.value.response.is_closed
    assert len(webhook.requests) == 1


async def test_content_type_cannot_be_overridden_by_case_variant_headers() -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        response: Final = await N8nA2AConfig(http_client=client).handle_non_streaming(
            "request-1",
            _params(),
            WEBHOOK_URL,
            litellm_params={"extra_headers": {"content-type": "text/plain"}},
            agent_extra_headers={"Content-Type": "application/octet-stream"},
        )

    assert webhook.requests[0].headers.get_list("content-type") == ["application/json"]
    assert _result(response)["parts"] == [{"kind": "text", "text": "answer"}]


async def test_duplicate_begin_cannot_hide_an_incomplete_stream() -> None:
    body: Final = _ChunkedBody((_frame("begin"), _frame("begin"), _frame("item", "answer"), _frame("end")))
    webhook: Final = _Webhook(httpx.Response(200, headers={"content-type": "application/json"}, stream=body))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        events: Final = tuple(
            [event async for event in N8nA2AConfig(http_client=client).handle_streaming("request-1", _params(), WEBHOOK_URL)]
        )

    failure: Final = _result(events[-1])
    assert JSON_OBJECT.validate_python(failure["status"])["state"] == "failed"
    assert failure["final"] is True
    assert body.closed


async def test_empty_response_array_cannot_become_an_empty_success() -> None:
    webhook: Final = _Webhook(httpx.Response(200, json=[]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        with pytest.raises(ValueError, match="response"):
            await N8nA2AConfig(http_client=client).handle_non_streaming("request-1", _params(), WEBHOOK_URL)

    assert len(webhook.requests) == 1


async def test_agent_gateway_dispatches_n8n_to_the_chat_trigger_transport() -> None:
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "gateway answer"}))
    handler: Final = AsyncHTTPHandler(transport=httpx.MockTransport(webhook))
    litellm.in_memory_llm_clients_cache.set_cache(key="async_httpx_clientn8n", value=handler)

    try:
        response: Final = await A2ACompletionBridgeHandler.handle_non_streaming(
            "request-1",
            _params(),
            {"custom_llm_provider": "n8n", "api_key": "configured-webhook-key"},
            WEBHOOK_URL,
            agent_extra_headers={"X-Trace": "gateway-request"},
        )
    finally:
        await handler.close()

    assert _result(response)["parts"] == [{"kind": "text", "text": "gateway answer"}]
    assert _result(response)["contextId"] == "conversation-1"
    assert len(webhook.requests) == 1
    request: Final = webhook.requests[0]
    assert str(request.url) == WEBHOOK_URL
    assert JSON_OBJECT.validate_json(request.content) == {
        "action": "sendMessage",
        "chatInput": "first line\nsecond line",
        "sessionId": "conversation-1",
    }
    assert request.headers["authorization"] == "Bearer configured-webhook-key"
    assert request.headers["x-trace"] == "gateway-request"


async def test_long_context_retains_public_context_and_gets_a_bounded_scoped_session() -> None:
    context_id: Final = "long-conversation-" + "a" * 150
    key_hash: Final = "caller-key-hash"
    scoped_context: Final = f"{hashlib.sha256(key_hash.encode()).hexdigest()[:16]}-{context_id}"
    webhook: Final = _Webhook(httpx.Response(200, json={"output": "answer"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook)) as client:
        response: Final = await N8nA2AConfig(http_client=client).handle_non_streaming(
            "request-1",
            _params(context_id),
            WEBHOOK_URL,
            litellm_params={"litellm_a2a_user_api_key_hash": key_hash},
        )

    assert _result(response)["contextId"] == context_id
    assert JSON_OBJECT.validate_json(webhook.requests[0].content)["sessionId"] == hashlib.sha256(
        scoped_context.encode()
    ).hexdigest()


async def test_redirect_cannot_forward_credentials_or_repeat_agent_execution() -> None:
    webhook: Final = _Webhook(httpx.Response(307, headers={"location": "https://redirect.test/other-agent"}))

    async with httpx.AsyncClient(transport=httpx.MockTransport(webhook), follow_redirects=True) as client:
        with pytest.raises(httpx.HTTPStatusError) as error:
            await N8nA2AConfig(http_client=client).handle_non_streaming(
                "request-1", _params(), WEBHOOK_URL, litellm_params={"api_key": "configured-key"}
            )

    assert error.value.response.status_code == 307
    assert tuple(str(request.url) for request in webhook.requests) == (WEBHOOK_URL,)
