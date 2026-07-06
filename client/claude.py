"""Claude web API client."""

import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Claude web API client."""

    def __init__(self, session_key: str, cookie: str, user_agent: str):
        self.session_key = session_key
        self.cookie = cookie
        self.user_agent = user_agent
        self.base_url = "https://claude.ai"

    def _get_headers(self) -> dict:
        """Get request headers."""
        headers = {
            "Cookie": self.cookie,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "anthropic-client-platform": "web_claude_ai",
        }

        if self.session_key:
            headers["Cookie"] = f"sessionKey={self.session_key}; {self.cookie}"

        return headers

    async def chat_completions(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        """Call Claude chat completions API."""
        # Convert messages to Claude format
        claude_messages = []
        for msg in messages:
            if msg["role"] == "system":
                continue  # Claude doesn't have system messages in the same way
            claude_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        payload = {
            "model": model,
            "messages": claude_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        url = f"{self.base_url}/api/v1/chat"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._get_headers(), json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"Claude API error: {resp.status} - {error_text}"
                    )
                return await resp.json()

    async def chat_completions_stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """Call Claude chat completions API with streaming."""
        # Convert messages to Claude format
        claude_messages = []
        for msg in messages:
            if msg["role"] == "system":
                continue
            claude_messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        payload = {
            "model": model,
            "messages": claude_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        url = f"{self.base_url}/api/v1/chat"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._get_headers(), json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"Claude API error: {resp.status} - {error_text}"
                    )

                async for line in resp.content:
                    if line:
                        yield line.decode("utf-8")
