from collections.abc import AsyncIterator, Mapping
from itertools import count
from typing import Final
from uuid import uuid4

import httpx

from litellm.a2a_protocol.providers.base import BaseA2AProviderConfig
from litellm.a2a_protocol.providers.n8n.transformation import (
    N8nFailure,
    N8nSettings,
    iter_reply_text,
    prepare_request,
    raise_public,
)
from litellm.interactions.agents.utils import merge_agent_headers
from litellm.llms.custom_httpx.http_handler import (
    get_async_httpx_client,  # pyright: ignore[reportUnknownVariableType]  # Shared factory has legacy untyped optional parameters
)


def _headers(settings: N8nSettings, agent_extra_headers: Mapping[str, str] | None) -> Mapping[str, str]:
    credentials: Final = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    dynamic: Final = merge_agent_headers(dynamic_headers=credentials, static_headers=agent_extra_headers)
    merged: Final = merge_agent_headers(dynamic_headers=dynamic, static_headers=settings.extra_headers)
    return merge_agent_headers(dynamic_headers=merged, static_headers={"Content-Type": "application/json"}) or {}


def _url(api_base: str | None) -> str | N8nFailure:
    if not api_base:
        return N8nFailure("api_base is required for n8n agents and must be the production webhook URL")
    try:
        parsed: Final = httpx.URL(api_base)
    except httpx.InvalidURL:
        return N8nFailure("api_base must be a valid HTTP(S) webhook URL")
    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
        return N8nFailure("api_base must be an HTTP(S) webhook URL without embedded credentials")
    return api_base


def _rpc(request_id: str, result: Mapping[str, object]) -> Mapping[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _status(
    request_id: str,
    context_id: str,
    task_id: str,
    state: str,
    *,
    final: bool = False,
    error: str | None = None,
) -> Mapping[str, object]:
    message: Final = (
        {
            "message": {
                "kind": "message",
                "messageId": str(uuid4()),
                "role": "agent",
                "parts": [{"kind": "text", "text": error}],
            }
        }
        if error
        else {}
    )
    return _rpc(
        request_id,
        {
            "kind": "status-update",
            "taskId": task_id,
            "contextId": context_id,
            "status": {"state": state, **message},
            "final": final,
        },
    )


def _artifact(
    request_id: str,
    context_id: str,
    task_id: str,
    artifact_id: str,
    text: str,
    *,
    append: bool = True,
    last_chunk: bool = False,
) -> Mapping[str, object]:
    return _rpc(
        request_id,
        {
            "kind": "artifact-update",
            "taskId": task_id,
            "contextId": context_id,
            "artifact": {"artifactId": artifact_id, "name": "response", "parts": [{"kind": "text", "text": text}]},
            "append": append,
            "lastChunk": last_chunk,
        },
    )


class N8nA2AConfig(BaseA2AProviderConfig):
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    def _client(self) -> httpx.AsyncClient:
        return self._http_client if self._http_client is not None else get_async_httpx_client("n8n").client

    async def handle_non_streaming(
        self,
        request_id: str,
        params: Mapping[str, object],
        api_base: str | None = None,
        *,
        litellm_params: Mapping[str, object] | None = None,
        agent_extra_headers: Mapping[str, str] | None = None,
        **kwargs: object,  # kwargs-ok: Required by the existing A2A provider interface
    ) -> dict[str, object]:  # mutable-ok: Required by the existing A2A provider return contract
        url: Final = _url(api_base)
        if isinstance(url, N8nFailure):
            raise_public(url)
        settings: Final = N8nSettings.model_validate(litellm_params or {})
        request: Final = prepare_request(params, settings)
        if isinstance(request, N8nFailure):
            raise_public(request)
        async with self._client().stream(
            "POST",
            url,
            json=request.body(),
            headers=_headers(settings, agent_extra_headers),
            timeout=settings.timeout,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            chunks: Final = tuple([chunk async for chunk in iter_reply_text(response)])
            failure: Final = next((chunk for chunk in chunks if isinstance(chunk, N8nFailure)), None)
            if failure is not None:
                raise_public(failure)
            text: Final = "".join(chunk for chunk in chunks if isinstance(chunk, str))
        return dict(
            _rpc(
                request_id,
                {
                    "kind": "message",
                    "messageId": str(uuid4()),
                    "role": "agent",
                    "parts": [{"kind": "text", "text": text}],
                    "contextId": request.context_id,
                },
            )
        )

    async def handle_streaming(
        self,
        request_id: str,
        params: Mapping[str, object],
        api_base: str | None = None,
        *,
        litellm_params: Mapping[str, object] | None = None,
        agent_extra_headers: Mapping[str, str] | None = None,
        **kwargs: object,  # kwargs-ok: Required by the existing A2A provider interface
    ) -> AsyncIterator[dict[str, object]]:  # mutable-ok: Required by the existing A2A provider return contract
        url: Final = _url(api_base)
        if isinstance(url, N8nFailure):
            raise_public(url)
        settings: Final = N8nSettings.model_validate(litellm_params or {})
        request: Final = prepare_request(params, settings)
        if isinstance(request, N8nFailure):
            raise_public(request)
        task_id: Final = str(uuid4())
        artifact_id: Final = str(uuid4())
        chunk_indices: Final = count()
        async with self._client().stream(
            "POST",
            url,
            json=request.body(),
            headers=_headers(settings, agent_extra_headers),
            timeout=settings.timeout,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            yield dict(
                _rpc(
                    request_id,
                    {
                        "kind": "task",
                        "id": task_id,
                        "contextId": request.context_id,
                        "status": {"state": "submitted"},
                        "history": [request.message.model_dump(mode="json", exclude_none=True)],
                    },
                )
            )
            yield dict(_status(request_id, request.context_id, task_id, "working"))
            try:
                async for text in iter_reply_text(response):
                    if isinstance(text, N8nFailure):
                        yield dict(
                            _status(request_id, request.context_id, task_id, "failed", final=True, error=text.message)
                        )
                        return
                    yield dict(
                        _artifact(
                            request_id, request.context_id, task_id, artifact_id, text, append=next(chunk_indices) > 0
                        )
                    )
            except (httpx.HTTPError, ValueError) as exc:
                yield dict(_status(request_id, request.context_id, task_id, "failed", final=True, error=str(exc)))
                return
        yield dict(_artifact(request_id, request.context_id, task_id, artifact_id, "", last_chunk=True))
        yield dict(_status(request_id, request.context_id, task_id, "completed", final=True))
