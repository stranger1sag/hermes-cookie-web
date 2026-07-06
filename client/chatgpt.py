"""ChatGPT web API client."""

import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class ChatGPTClient:
    """ChatGPT web API client."""

    def __init__(self, session_token: str, cookie: str, user_agent: str):
        self.session_token = session_token
        self.cookie = cookie
        self.user_agent = user_agent
        self.base_url = "https://chatgpt.com"

    def _get_headers(self) -> dict:
        """Get request headers."""
        headers = {
            "Cookie": self.cookie,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        if self.session_token:
            headers["Authorization"] = f"Bearer {self.session_token}"

        return headers

    async def _get_access_token(self) -> Optional[str]:
        """Get access token from session."""
        try:
            url = f"{self.base_url}/api/auth/session"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._get_headers()) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("accessToken")
        except Exception as e:
            logger.debug(f"Failed to get access token: {e}")
        return None

    async def chat_completions(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        """Call ChatGPT chat completions API."""
        # Get access token
        access_token = await self._get_access_token()
        if not access_token:
            raise RuntimeError("Failed to get ChatGPT access token")

        headers = self._get_headers()
        headers["Authorization"] = f"Bearer {access_token}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        url = f"{self.base_url}/backend-api/conversation"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"ChatGPT API error: {resp.status} - {error_text}"
                    )
                return await resp.json()

    async def chat_completions_stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """Call ChatGPT chat completions API with streaming."""
        # Get access token
        access_token = await self._get_access_token()
        if not access_token:
            raise RuntimeError("Failed to get ChatGPT access token")

        headers = self._get_headers()
        headers["Authorization"] = f"Bearer {access_token}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        url = f"{self.base_url}/backend-api/conversation"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"ChatGPT API error: {resp.status} - {error_text}"
                    )

                async for line in resp.content:
                    if line:
                        yield line.decode("utf-8")
