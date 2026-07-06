"""Claude Web API provider.

NOTE: Claude web API support is not yet fully implemented.
This skeleton provides the structure; actual API endpoints and
SSE formats need to be reverse-engineered from claude.ai.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

import aiohttp
from aiohttp import web

from .base import WebProvider, ModelRoute
from .identity import DefaultIdentity, IdentityStrategy
from .sse_parser import SseEvent, iter_sse

logger = logging.getLogger("cookie-web-proxy.claude")

MODEL_ROUTES = [
    ModelRoute(provider="claude", internal_name="claude-sonnet-4-6"),
    ModelRoute(provider="claude", internal_name="claude-opus-4-6"),
    ModelRoute(provider="claude", internal_name="claude-haiku-4-6"),
]


class ClaudeProvider(WebProvider):
    """Provider for the Claude web chat API."""

    def __init__(
        self,
        identity: IdentityStrategy | None = None,
    ):
        self._identity = identity or DefaultIdentity()
        self._creds = _load_creds()

    @property
    def name(self) -> str:
        return "claude"

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
        creds = self._creds.get("claude")
        if not creds:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "No Claude credentials"})
            )

        prompt = self._identity.build_prompt(messages)
        cookie = creds.get("cookie", "")
        session_key = creds.get("sessionKey", "")
        user_agent = creds.get("userAgent", "")

        headers = {
            "Cookie": f"sessionKey={session_key}; {cookie}" if session_key else cookie,
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        raise web.HTTPNotImplemented(
            text=json.dumps({"error": "Claude web API not yet implemented"})
        )

        # TODO: Implement Claude web API protocol
        # async with aiohttp.ClientSession() as session:
        #     async with session.post(...) as resp:
        #         async for event in iter_sse(resp):
        #             ...
        yield  # unreachable, placeholder for generator


def _load_creds() -> dict[str, dict]:
    from .deepseek import _load_creds as _ds_load
    return _ds_load()
