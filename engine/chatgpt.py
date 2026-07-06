"""ChatGPT Web API provider.

NOTE: ChatGPT web API support is not yet fully implemented.
This skeleton provides the structure; actual API endpoints and
SSE formats need to be reverse-engineered from chatgpt.com.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from aiohttp import web

from .base import WebProvider, ModelRoute
from .identity import DefaultIdentity, IdentityStrategy
from .sse_parser import SseEvent, iter_sse

logger = logging.getLogger("cookie-web-proxy.chatgpt")

MODEL_ROUTES = [
    ModelRoute(provider="chatgpt", internal_name="gpt-4"),
    ModelRoute(provider="chatgpt", internal_name="gpt-4-turbo"),
    ModelRoute(provider="chatgpt", internal_name="gpt-4o"),
]


class ChatGPTProvider(WebProvider):
    """Provider for the ChatGPT web chat API."""

    def __init__(
        self,
        identity: IdentityStrategy | None = None,
    ):
        self._identity = identity or DefaultIdentity()
        self._creds = _load_creds()

    @property
    def name(self) -> str:
        return "chatgpt"

    @property
    def routes(self) -> list[ModelRoute]:
        return MODEL_ROUTES

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str = "",
        stream: bool = True,
    ) -> AsyncGenerator[dict, None]:
        creds = self._creds.get("chatgpt")
        if not creds:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "No ChatGPT credentials"})
            )

        prompt = self._identity.build_prompt(messages)
        cookie = creds.get("cookie", "")
        session_token = creds.get("sessionToken", "")
        user_agent = creds.get("userAgent", "")

        raise web.HTTPNotImplemented(
            text=json.dumps({"error": "ChatGPT web API not yet implemented"})
        )

        # TODO: Implement ChatGPT web API protocol
        # async with aiohttp.ClientSession() as session:
        #     async with session.post(...) as resp:
        #         async for event in iter_sse(resp):
        #             ...
        yield  # unreachable, placeholder for generator


def _load_creds() -> dict[str, dict]:
    from .deepseek import _load_creds as _ds_load
    return _ds_load()
