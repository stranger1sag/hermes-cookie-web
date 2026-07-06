"""DeepSeek Web API provider.

Handles:
  - PoW challenge/solve (Node.js WASM)
  - Chat session lifecycle
  - SSE stream parsing (3 data formats)
  - Identity injection via IdentityStrategy
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import aiohttp
from aiohttp import web

from .base import WebProvider, ModelRoute
from .identity import DefaultIdentity, IdentityStrategy
from .sse_parser import SseEvent, iter_sse
from .tool_parser import split_text_and_tool_calls

logger = logging.getLogger("cookie-web-proxy.deepseek")

POW_SOLVER = str(Path(__file__).parent.parent / "browser" / "pow_solver.mjs")

MODEL_ROUTES = [
    ModelRoute(provider="deepseek", internal_name="deepseek-chat"),
    ModelRoute(provider="deepseek", internal_name="deepseek-reasoner"),
    ModelRoute(provider="deepseek", internal_name="deepseek-chat-search"),
    ModelRoute(provider="deepseek", internal_name="deepseek-reasoner-search"),
]

# Default headers mimicking a real browser session.
_DS_HEADERS_TEMPLATE = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Referer": "https://chat.deepseek.com/",
    "Origin": "https://chat.deepseek.com",
    "x-client-platform": "web",
    "x-client-version": "1.7.0",
    "x-app-version": "20241129.1",
}


class DeepSeekProvider(WebProvider):
    """Provider for the DeepSeek web chat API."""

    def __init__(
        self,
        identity: IdentityStrategy | None = None,
    ):
        self._identity = identity or DefaultIdentity()
        self._creds = _load_creds()

    # ── WebProvider interface ──────────────────────────────────────────

    @property
    def name(self) -> str:
        return "deepseek"

    @property
    def routes(self) -> list[ModelRoute]:
        return MODEL_ROUTES

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str = "",
        stream: bool = True,
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield OpenAI-format chunk dicts from DeepSeek web API."""
        creds = self._creds.get("deepseek")
        if not creds:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "No DeepSeek credentials"})
            )

        cookie = creds.get("cookie", "")
        bearer = creds.get("bearer", creds.get("token", ""))
        user_agent = creds.get("userAgent", "")
        prompt = self._identity.build_prompt(messages, tools=tools)
        search_enabled = "search" in model or "search" in prompt
        thinking_enabled = "reasoner" in model or "reasoner" in prompt

        headers = self._build_headers(cookie, bearer, user_agent)

        connector = aiohttp.TCPConnector(limit=1)
        async with aiohttp.ClientSession(connector=connector) as session:
            pow_headers = await self._do_pow(session, headers)
            session_id = await self._create_session(session, pow_headers)

            async for chunk in self._do_completion(
                session, pow_headers, session_id, prompt,
                search_enabled, thinking_enabled, model,
            ):
                yield chunk

    # ── Internal helpers ───────────────────────────────────────────────

    def _build_headers(
        self, cookie: str, bearer: str, user_agent: str
    ) -> dict[str, str]:
        headers = dict(_DS_HEADERS_TEMPLATE)
        headers["Cookie"] = cookie
        headers["User-Agent"] = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36"
        )
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        return headers

    @staticmethod
    def _openai_id() -> str:
        return f"chatcmpl-{int(time.time() * 1000)}"

    # ── PoW ────────────────────────────────────────────────────────────

    async def _do_pow(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
    ) -> dict[str, str]:
        """Run PoW challenge → solve → return headers with pow-response."""
        async with session.post(
            "https://chat.deepseek.com/api/v0/chat/create_pow_challenge",
            headers=headers,
            json={"target_path": "/api/v0/chat/completion"},
        ) as resp:
            data = await resp.json()
            challenge = (
                data.get("data", {})
                .get("biz_data", {})
                .get("challenge", {})
            )

        answer = await _solve_pow_async(challenge)
        pow_b64 = base64.b64encode(
            json.dumps({**challenge, "answer": answer}).encode()
        ).decode()
        return {**headers, "x-ds-pow-response": pow_b64}

    # ── Session ────────────────────────────────────────────────────────

    async def _create_session(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
    ) -> str:
        async with session.post(
            "https://chat.deepseek.com/api/v0/chat_session/create",
            headers=headers,
            json={},
        ) as resp:
            data = await resp.json()
            session_id = (
                data.get("data", {})
                .get("biz_data", {})
                .get("id", "")
            )
            if not session_id:
                raise RuntimeError(f"Failed to create session: {data}")
            return session_id

    # ── Completion + SSE parsing ───────────────────────────────────────

    async def _do_completion(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        session_id: str,
        prompt: str,
        search_enabled: bool,
        thinking_enabled: bool,
        model: str,
    ) -> AsyncGenerator[dict, None]:
        chat_id = self._openai_id()

        async with session.post(
            "https://chat.deepseek.com/api/v0/chat/completion",
            headers=headers,
            json={
                "chat_session_id": session_id,
                "parent_message_id": None,
                "prompt": prompt,
                "ref_file_ids": [],
                "thinking_enabled": thinking_enabled,
                "search_enabled": search_enabled,
                "preempt": False,
            },
        ) as cresp:
            if cresp.status != 200:
                error_text = await cresp.text()
                raise RuntimeError(
                    f"DeepSeek API error: {cresp.status} - {error_text[:500]}"
                )

            stream_buffer = _ToolCallBuffer()
            had_tool_calls = False
            async for event in iter_sse(cresp):
                fragment = _extract_text(event)
                if fragment:
                    async for chunk in stream_buffer.feed(fragment, chat_id, model):
                        if chunk.get("choices", [{}])[0].get("delta", {}).get("tool_calls"):
                            had_tool_calls = True
                        yield chunk

            async for chunk in stream_buffer.flush(chat_id, model):
                yield chunk

            finish = "tool_calls" if had_tool_calls else "stop"
            yield _make_chunk(chat_id, model, "", finish=finish)


# ── Tool-call streaming buffer ─────────────────────────────────────────


class _ToolCallBuffer:
    """Accumulates streaming text and extracts ``<tool_call>`` markers.

    DeepSeek streams character-by-character, so individual fragments rarely
    contain a complete ``<tool_call>`` or ``</tool_call>``.  This buffer
    accumulates all text and only emits deltas when a *complete* pattern
    is detected.
    """

    _TOOL_START = "<tool_call>"
    _TOOL_END = "</tool_call>"

    def __init__(self) -> None:
        self._buf = ""
        self._tool_index = 0

    async def feed(
        self,
        fragment: str,
        chat_id: str,
        model: str,
    ) -> AsyncGenerator[dict, None]:
        """Append *fragment* to the buffer, yielding deltas for any
        complete tool-call patterns found."""
        self._buf += fragment
        async for chunk in self._scan(chat_id, model):
            yield chunk

    async def flush(
        self,
        chat_id: str,
        model: str,
    ) -> AsyncGenerator[dict, None]:
        """Yield remaining buffered text as a single content chunk."""
        if self._buf:
            yield _make_chunk(chat_id, model, self._buf)
            self._buf = ""

    async def _scan(
        self,
        chat_id: str,
        model: str,
    ) -> AsyncGenerator[dict, None]:
        """Scan the buffer for complete ``<tool_call>...</tool_call>``
        patterns and emit them."""
        while True:
            start = self._buf.find(self._TOOL_START)
            if start == -1:
                return
            end = self._buf.find(self._TOOL_END, start + len(self._TOOL_START))
            if end == -1:
                return

            # Emit text before the tool call
            if start > 0:
                yield _make_chunk(chat_id, model, self._buf[:start])

            # Parse and emit the tool call
            inner = self._buf[start + len(self._TOOL_START):end].strip()
            consumed = end + len(self._TOOL_END)
            self._buf = self._buf[consumed:]

            if inner:
                try:
                    parsed = json.loads(inner)
                except json.JSONDecodeError:
                    yield _make_chunk(chat_id, model, f"<tool_call>{inner}</tool_call>")
                    continue

                name = parsed.get("name", "") or parsed.get("function", "")
                args = parsed.get("args", parsed.get("arguments", {}))
                args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, dict) else str(args)

                idx = self._tool_index
                self._tool_index += 1
                call_id = f"call_{int(time.time() * 1000)}_{idx}"

                yield {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": idx,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": ""},
                            }],
                        },
                        "finish_reason": None,
                    }],
                }

                yield {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": idx,
                                "function": {"arguments": args_str},
                            }],
                        },
                        "finish_reason": None,
                    }],
                }


# ── Helper functions (module-level for testability) ────────────────────


def _extract_text(event: SseEvent) -> str:
    """Extract text content from a DeepSeek SSE event.

    DeepSeek sends text in three formats:
      1. {"v": {"response": {"fragments": [{"type": "RESPONSE", "content": "..."}]}}}
      2. {"v": "直接文本"}
      3. {"p": "...", "o": "APPEND", "v": "文本"}
    """
    data = event.data
    if not data:
        return ""

    # Format 1: fragment-based
    v = data.get("v")
    if isinstance(v, dict) and "response" in v:
        for frag in v["response"].get("fragments", []):
            if frag.get("type") == "RESPONSE":
                return frag.get("content", "")

    # Format 2 & 3: string v (with operation filter)
    if isinstance(v, str):
        op = data.get("o", "")
        if op in ("", "APPEND"):
            return v

    return ""


def _make_chunk(
    chat_id: str,
    model: str,
    content: str,
    finish: str | None = None,
) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish,
            }
        ],
    }


async def _solve_pow_async(challenge: dict) -> int:
    """Run Node.js PoW solver in executor to avoid blocking."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _solve_pow_sync, challenge)


def _solve_pow_sync(challenge: dict) -> int:
    result = subprocess.run(
        ["node", POW_SOLVER, json.dumps(challenge)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"PoW solver failed: {result.stderr or result.stdout}"
        )
    output = json.loads(result.stdout)
    if not output.get("success"):
        raise RuntimeError(f"PoW solver error: {output.get('error')}")
    return int(output["answer"])


def _load_creds() -> dict[str, dict]:
    """Load credentials via CredentialStore (same logic as before)."""
    from importlib import import_module, util as import_util

    store_mod = None

    # Try package import first
    for imp_path in (
        "plugins.model_providers.cookie_web.storage.store",
        f"{__package__}.storage.store",
    ):
        try:
            store_mod = import_module(imp_path)
            break
        except (ImportError, TypeError, AttributeError):
            continue

    # Last resort: direct file load
    if store_mod is None:
        store_path = (
            Path(__file__).parent.parent / "storage" / "store.py"
        )
        spec = import_util.spec_from_file_location(
            "_cred_store",
            str(store_path),
            submodule_search_locations=[str(store_path.parent)],
        )
        if spec and spec.loader:
            store_mod = import_util.module_from_spec(spec)
            spec.loader.exec_module(store_mod)

    if store_mod is None:
        logger.error("Cannot load CredentialStore module")
        return {}

    return store_mod.CredentialStore().load()
