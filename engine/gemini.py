"""Gemini Free API provider.

Translates OpenAI-format chat completions to the Gemini REST API
(``generateContent`` / ``streamGenerateContent``).

Gemini provides native function calling, system instructions, and
usage metadata — no PoW, no cookies, no identity injection needed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import aiohttp
from aiohttp import web

from .base import WebProvider, ModelRoute

logger = logging.getLogger("cookie-web-proxy.gemini")

# Default retry-after seconds for 429 when no Retry-After header.
_DEFAULT_RETRY_AFTER = 60


class _KeyPool:
    """Round-robin pool of API keys with 429-aware backoff.

    Keeps per-key ``available_at`` timestamps.  When a key gets 429'd
    it is taken out of rotation for *retry_after* seconds (or the
    default 60 s).  ``get()`` always returns the earliest-available key.
    """

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("_KeyPool requires at least one key")
        self._keys = list(keys)
        self._available_at: dict[str, float] = {k: 0.0 for k in keys}
        self._lock = asyncio.Lock()

    async def get(self) -> str | None:
        """Return the earliest-available key, or ``None`` if all are
        rate-limited."""
        async with self._lock:
            now = time.monotonic()
            best_key: str | None = None
            best_time: float = float("inf")
            for k in self._keys:
                at = self._available_at.get(k, 0.0)
                if at > best_time:
                    continue
                # Prioritise a key that is *already* available.
                if best_key is None or at <= now:
                    best_key = k
                    best_time = at
            # If the best key is still rate-limited, None signals the
            # caller that all keys are unavailable.
            if best_key and self._available_at.get(best_key, 0.0) > now:
                return None
            return best_key

    async def mark_success(self, key: str) -> None:
        async with self._lock:
            self._available_at[key] = 0.0

    async def mark_rate_limited(self, key: str, retry_after: float = _DEFAULT_RETRY_AFTER) -> None:
        async with self._lock:
            self._available_at[key] = time.monotonic() + retry_after

    @property
    def total_keys(self) -> int:
        return len(self._keys)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

MODEL_ROUTES = [
    ModelRoute(provider="gemini", internal_name="gemini-2.5-flash"),
    ModelRoute(provider="gemini", internal_name="gemini-2.5-pro"),
    ModelRoute(provider="gemini", internal_name="gemini-2.0-flash"),
    ModelRoute(provider="gemini", internal_name="gemini-2.0-flash-lite"),
    ModelRoute(provider="gemini", internal_name="gemini-2.5-pro-exp-03-25"),
    ModelRoute(provider="gemini", internal_name="gemini-2.5-flash-preview-04-17"),
]

_OPENAI_TO_GEMINI_MODEL = {
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.0-flash": "gemini-2.0-flash",
    "gemini-2.0-flash-lite": "gemini-2.0-flash-lite",
    "gemini-2.5-pro-exp-03-25": "gemini-2.5-pro-exp-03-25",
    "gemini-2.5-flash-preview-04-17": "gemini-2.5-flash-preview-04-17",
}


class GeminiProvider(WebProvider):
    """Provider for the Google Gemini free API."""

    def __init__(self) -> None:
        self._creds = _load_creds()
        gemini_creds = self._creds.get("gemini", {})
        keys = gemini_creds.get("api_keys") or (
            [gemini_creds["api_key"]] if gemini_creds.get("api_key") else []
        )
        if keys:
            self._pool = _KeyPool(keys)
            logger.info("Gemini key pool ready: %d keys", self._pool.total_keys)
        else:
            self._pool = None

    # ── WebProvider interface ──────────────────────────────────────────

    @property
    def name(self) -> str:
        return "gemini"

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
        """Yield OpenAI-format chunk dicts from Gemini API."""
        if not self._pool:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "No Gemini API keys configured"})
            )

        gemini_model = _resolve_gemini_model(model)
        request_body = _build_request_body(messages, tools)

        errors: list[str] = []
        for attempt in range(self._pool.total_keys + 1):
            api_key = await self._pool.get()
            if api_key is None:
                if errors:
                    logger.warning("all %d keys rate-limited: %s",
                                   self._pool.total_keys, errors[-1])
                raise web.HTTPTooManyRequests(
                    text=json.dumps({
                        "error": "All Gemini API keys rate-limited",
                        "detail": errors,
                    })
                )

            if stream:
                endpoint = (
                    f"{GEMINI_API_BASE}/models/{gemini_model}:streamGenerateContent"
                    f"?alt=sse&key={api_key}"
                )
            else:
                endpoint = (
                    f"{GEMINI_API_BASE}/models/{gemini_model}:generateContent"
                    f"?key={api_key}"
                )

            try:
                if stream:
                    async for chunk in _stream_chat(endpoint, request_body, model):
                        yield chunk
                else:
                    yield await _single_chat(endpoint, request_body, model)
                await self._pool.mark_success(api_key)
                return
            except web.HTTPTooManyRequests as exc:
                errors.append(str(exc))
                await self._pool.mark_rate_limited(api_key)
                logger.warning("key rate-limited, trying next... (%d/%d)",
                               attempt + 1, self._pool.total_keys + 1)
                continue

        raise web.HTTPTooManyRequests(
            text=json.dumps({
                "error": "All Gemini API keys exhausted",
                "detail": errors,
            })
        )


# ── Request building ──────────────────────────────────────────────────


def _resolve_gemini_model(openai_model: str) -> str:
    if openai_model in _OPENAI_TO_GEMINI_MODEL:
        return _OPENAI_TO_GEMINI_MODEL[openai_model]
    if openai_model.startswith("gemini-"):
        return openai_model
    return "gemini-2.5-flash"


def _build_request_body(
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict:
    contents: list[dict] = []
    system_instruction: str | None = None

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            system_instruction = content
            continue

        if role == "tool":
            tc_id = msg.get("tool_call_id", "")
            content_text = content if content else ""
            contents.append({
                "role": "function",
                "parts": [{
                    "functionResponse": {
                        "name": msg.get("name", tc_id),
                        "response": {"result": content_text},
                    }
                }],
            })
            continue

        parts: list[dict] = []
        if content:
            parts.append({"text": content})

        tc = msg.get("tool_calls")
        if tc:
            for call in tc:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                if name:
                    parts.append({"functionCall": {"name": name, "args": args}})

        gemini_role = "model" if role in ("assistant", "model") else "user"
        contents.append({"role": gemini_role, "parts": parts})

    body: dict[str, Any] = {"contents": contents}
    if system_instruction:
        body["system_instruction"] = {"parts": [{"text": system_instruction}]}

    if tools:
        body["tools"] = [_to_gemini_tools(tools)]

    return body


def _to_gemini_tools(tools: list[dict]) -> dict:
    """Convert OpenAI tool format to Gemini tool format."""
    declarations = []
    for t in tools:
        fn = t.get("function", t)
        decl: dict[str, Any] = {
            "name": fn.get("name", ""),
        }
        if fn.get("description"):
            decl["description"] = fn["description"]
        if fn.get("parameters"):
            decl["parameters"] = fn["parameters"]
        declarations.append(decl)
    return {"functionDeclarations": declarations}


# ── Streaming ─────────────────────────────────────────────────────────


async def _stream_chat(
    endpoint: str,
    body: dict,
    model: str,
) -> AsyncGenerator[dict, None]:
    chat_id = f"chatcmpl-{int(time.time() * 1000)}"
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=body) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                if resp.status == 429:
                    raise web.HTTPTooManyRequests(
                        text=json.dumps({"error": f"Gemini rate limited: {error_text[:200]}"})
                    )
                raise RuntimeError(
                    f"Gemini API error: {resp.status} - {error_text[:500]}"
                )

            async for raw_line in _read_lines(resp):
                if not raw_line.startswith("data: "):
                    continue
                payload = raw_line[6:].strip()
                if not payload or payload == "[DONE]":
                    continue

                try:
                    gemini_chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                candidates = gemini_chunk.get("candidates", [])
                usage = gemini_chunk.get("usageMetadata")

                if not candidates and usage:
                    yield _make_usage_chunk(chat_id, model, usage)
                    continue

                for cand in candidates:
                    content = cand.get("content", {})
                    parts = content.get("parts", [])
                    finish = cand.get("finishReason", "")
                    idx = cand.get("index", 0)

                    had_function_call = False
                    for part in parts:
                        if "text" in part and part["text"]:
                            yield _make_chunk(chat_id, model, part["text"])

                        if "functionCall" in part:
                            had_function_call = True
                            fc = part["functionCall"]
                            name = fc.get("name", "")
                            args = fc.get("args", {})
                            args_str = json.dumps(args, ensure_ascii=False)

                            call_id = f"call_{int(time.time() * 1000)}_{idx}"

                            yield _make_tool_name_chunk(
                                chat_id, model, call_id, name, idx
                            )
                            yield _make_tool_args_chunk(
                                chat_id, model, args_str, idx
                            )

                    if finish or had_function_call:
                        finish_reason = "tool_calls" if had_function_call else _map_finish_reason(finish)
                        yield _make_chunk(chat_id, model, "", finish=finish_reason)

                if usage:
                    yield _make_usage_chunk(chat_id, model, usage)


# ── Non-streaming ─────────────────────────────────────────────────────


async def _single_chat(
    endpoint: str,
    body: dict,
    model: str,
) -> dict:
    chat_id = f"chatcmpl-{int(time.time() * 1000)}"
    async with aiohttp.ClientSession() as session:
        async with session.post(endpoint, json=body) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                if resp.status == 429:
                    raise web.HTTPTooManyRequests(
                        text=json.dumps({"error": f"Gemini rate limited: {error_text[:200]}"})
                    )
                raise RuntimeError(
                    f"Gemini API error: {resp.status} - {error_text[:500]}"
                )
            data = await resp.json()

    candidates = data.get("candidates", [])
    usage = data.get("usageMetadata", {})

    text_parts: list[str] = []
    tool_calls: list[dict] | None = None

    for cand in candidates:
        content = cand.get("content", {})
        parts = content.get("parts", [])
        finish = cand.get("finishReason", "")

        for part in parts:
            if "text" in part and part["text"]:
                text_parts.append(part["text"])
            if "functionCall" in part:
                fc = part["functionCall"]
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": f"call_{int(time.time() * 1000)}_{len(tool_calls)}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                    },
                })

    text = "".join(text_parts)
    message: dict = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    if tool_calls:
        finish_reason = "tool_calls"
    elif candidates:
        mapped = _map_finish_reason(candidates[0].get("finishReason", ""))
        finish_reason = mapped or "stop"
    else:
        finish_reason = "stop"

    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


# ── Helpers ───────────────────────────────────────────────────────────


def _map_finish_reason(gemini_reason: str) -> str:
    mapping = {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "OTHER": "stop",
        "TOOL_CALL": "tool_calls",
    }
    return mapping.get(gemini_reason, "stop")


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
        "choices": [{
            "index": 0,
            "delta": {"content": content} if content else {},
            "finish_reason": finish,
        }],
    }


def _make_tool_name_chunk(
    chat_id: str,
    model: str,
    call_id: str,
    name: str,
    index: int,
) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {
                "tool_calls": [{
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": ""},
                }],
            },
            "finish_reason": None,
        }],
    }


def _make_tool_args_chunk(
    chat_id: str,
    model: str,
    args_str: str,
    index: int,
) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {
                "tool_calls": [{
                    "index": index,
                    "function": {"arguments": args_str},
                }],
            },
            "finish_reason": None,
        }],
    }


def _make_usage_chunk(
    chat_id: str,
    model: str,
    usage: dict,
) -> dict:
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


async def _read_lines(resp: aiohttp.ClientResponse) -> AsyncGenerator[str, None]:
    while True:
        raw = await resp.content.readline()
        if not raw:
            break
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            continue
        yield line


def _load_creds() -> dict[str, dict]:
    """Load credentials via CredentialStore."""
    from importlib import import_module, util as import_util

    store_mod = None

    for imp_path in (
        "plugins.model_providers.cookie_web.storage.store",
        f"{__package__}.storage.store",
    ):
        try:
            store_mod = import_module(imp_path)
            break
        except (ImportError, TypeError, AttributeError):
            continue

    if store_mod is None:
        store_path = Path(__file__).parent.parent / "storage" / "store.py"
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
