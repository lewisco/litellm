import hashlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal, NoReturn
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from litellm.a2a_protocol.utils import scope_session_to_principal


class TextPart(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    kind: Literal["text"] = "text"
    text: str


class InputMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    role: Literal["user"] = "user"
    parts: tuple[TextPart, ...] = Field(min_length=1)
    contextId: str | None = None
    messageId: str = Field(default_factory=lambda: str(uuid4()))


class N8nSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    api_key: str | None = None
    timeout: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    extra_headers: Mapping[str, str] | None = None
    principal: str | None = Field(default=None, alias="litellm_a2a_user_api_key_hash")


class Reply(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    output: str | None = None
    text: str | None = None
    response: str | None = None


class StreamMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    nodeId: str = ""
    runIndex: int = 0
    itemIndex: int = 0


class StreamEvent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    type: Literal["begin", "item", "end", "error"]
    content: str | None = None
    metadata: StreamMetadata = Field(default_factory=StreamMetadata)


_REPLY_ADAPTER: Final[TypeAdapter[Reply | tuple[Reply, ...]]] = TypeAdapter(Reply | tuple[Reply, ...])


@dataclass(frozen=True, slots=True)
class N8nFailure:
    message: str


def raise_public(failure: N8nFailure) -> NoReturn:
    raise ValueError(failure.message)


@dataclass(frozen=True, slots=True)
class N8nRequest:
    message: InputMessage
    context_id: str
    session_id: str
    chat_input: str

    def body(self) -> Mapping[str, str]:
        return {"action": "sendMessage", "sessionId": self.session_id, "chatInput": self.chat_input}


def prepare_request(params: Mapping[str, object], settings: N8nSettings) -> N8nRequest | N8nFailure:
    message: Final = InputMessage.model_validate(params.get("message"))
    text: Final = "\n".join(part.text for part in message.parts)
    if not text.strip():
        return N8nFailure("n8n agents require a non-empty text message")
    context_id: Final = message.contextId or str(uuid4())
    scoped_session: Final = scope_session_to_principal(context_id, settings.principal)
    session_id: Final = (
        scoped_session if len(scoped_session) <= 74 else hashlib.sha256(scoped_session.encode("utf-8")).hexdigest()
    )
    return N8nRequest(message=message, context_id=context_id, session_id=session_id, chat_input=text)


def reply_text(reply: Reply) -> str | N8nFailure:
    for value in (reply.output, reply.text, reply.response):
        if value is not None:
            return value
    return N8nFailure("n8n response must contain an output, text, or response string")


class StreamState:
    def __init__(self) -> None:
        self.active: frozenset[tuple[str, int, int]] = frozenset()
        self.started = False

    def apply(self, event: StreamEvent) -> str | N8nFailure | None:
        key: Final = (event.metadata.nodeId, event.metadata.runIndex, event.metadata.itemIndex)
        match event.type:
            case "begin":
                if key in self.active:
                    return N8nFailure("Invalid n8n stream begin")
                self.started = True
                self.active = self.active | {key}
            case "item":
                if key not in self.active or event.content is None:
                    return N8nFailure("Invalid n8n stream item")
                return event.content
            case "end":
                if key not in self.active:
                    return N8nFailure("Invalid n8n stream end")
                self.active = self.active - {key}
            case "error":
                return N8nFailure(f"n8n stream error: {event.content or 'Workflow execution failed'}")
        return None

    def finish(self) -> N8nFailure | None:
        if not self.started or self.active:
            return N8nFailure("n8n stream ended before all messages completed")
        return None


async def first_nonempty_line(lines: AsyncIterator[str]) -> str | None:
    async for line in lines:
        if line.strip():
            return line
    return None


async def iter_reply_text(response: httpx.Response) -> AsyncIterator[str | N8nFailure]:
    lines: Final = response.aiter_lines()
    first: Final = await first_nonempty_line(lines)
    if first is None:
        yield N8nFailure("Empty n8n response")
        return
    try:
        initial_event: Final = StreamEvent.model_validate_json(first)
    except ValidationError:
        remainder: Final = "\n".join([line async for line in lines])
        try:
            reply: Final = _REPLY_ADAPTER.validate_json(f"{first}\n{remainder}")
        except ValidationError as exc:
            yield N8nFailure(str(exc))
            return
        if isinstance(reply, tuple) and not reply:
            yield N8nFailure("Empty n8n response")
            return
        texts: Final = tuple(reply_text(item) for item in reply) if isinstance(reply, tuple) else (reply_text(reply),)
        failure: Final = next((text for text in texts if isinstance(text, N8nFailure)), None)
        yield failure if failure is not None else "\n".join(text for text in texts if isinstance(text, str))
        return
    state: Final = StreamState()
    initial_text: Final = state.apply(initial_event)
    if isinstance(initial_text, N8nFailure):
        yield initial_text
        return
    async for line in lines:
        if not line.strip():
            continue
        event = StreamEvent.model_validate_json(line)
        text = state.apply(event)
        if text:
            yield text
        if isinstance(text, N8nFailure):
            return
    completion: Final = state.finish()
    if completion is not None:
        yield completion
