"""OpenAI-compatible proxy for cookie-web providers.

Runs on 127.0.0.1:PROXY_PORT (default 13000).
Dispatches /v1/chat/completions to the appropriate WebProvider
(deepseek / claude / chatgpt) and translates the result back
to OpenAI SSE or JSON format.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from aiohttp import web

# ── Flexible import of engine submodules ──────────────────────────────
# Works both when imported via Hermes plugin system (__package__ set)
# and when run directly or via spec_from_file_location (no parent package).
_ENGINE_MOD = None
for _try_path in (
    f"{__package__}.engine" if __package__ else None,
    "plugins.model_providers.cookie_web.engine",
):
    if _try_path:
        try:
            _ENGINE_MOD = importlib.import_module(_try_path)
            break
        except (ImportError, ValueError):
            continue

if _ENGINE_MOD is None:
    _engine_dir = Path(__file__).parent / "engine"
    sys.path.insert(0, str(_engine_dir.parent))
    _ENGINE_MOD = importlib.import_module("engine")
    sys.path.pop(0)

if _ENGINE_MOD is None:
    raise ImportError("Cannot locate cookie-web engine module")

PROVIDER_MAP: dict[str, type] = _ENGINE_MOD.PROVIDER_MAP
WebProvider: type = _ENGINE_MOD.WebProvider

# ── Flexible import of CredentialStore ─────────────────────────────────
_STORE_MOD = None
for _try_store in (
    f"{__package__}.storage.store" if __package__ else None,
    "plugins.model_providers.cookie_web.storage.store",
):
    if _try_store:
        try:
            _STORE_MOD = importlib.import_module(_try_store)
            break
        except (ImportError, ValueError):
            continue

if _STORE_MOD is None:
    _store_path = Path(__file__).parent / "storage" / "store.py"
    import importlib.util as _util
    _spec = _util.spec_from_file_location(
        "_cred_store", str(_store_path),
        submodule_search_locations=[str(_store_path.parent)],
    )
    if _spec and _spec.loader:
        _STORE_MOD = _util.module_from_spec(_spec)
        _spec.loader.exec_module(_STORE_MOD)

if _STORE_MOD is None:
    raise ImportError("Cannot locate cookie-web storage module")

CredentialStore = _STORE_MOD.CredentialStore

logger = logging.getLogger("cookie-web-proxy")

PROXY_PORT = int(os.environ.get("COOKIE_WEB_PROXY_PORT", "13000"))

# Build model → provider lookup from all registered providers.
_MODEL_TO_PROVIDER: dict[str, str] = {}
_PROVIDER_INSTANCES: dict[str, WebProvider] = {}

for prov_name, prov_cls in PROVIDER_MAP.items():
    inst = prov_cls()
    _PROVIDER_INSTANCES[prov_name] = inst
    for route in inst.routes:
        _MODEL_TO_PROVIDER[route.internal_name] = prov_name


# ── Helpers ────────────────────────────────────────────────────────────

def _openai_id() -> str:
    return f"chatcmpl-{int(time.time() * 1000)}"


def _openai_json_to_sse_chunks(result: dict) -> list[dict]:
    """Convert a complete OpenAI chat.completion JSON (from Kimi) into
    a sequence of SSE chunks suitable for streaming to Hermes."""
    chunks: list[dict] = []
    chat_id = result.get("id", _openai_id())
    model = result.get("model", "")
    created = result.get("created", int(time.time()))
    usage = result.get("usage")

    for choice in result.get("choices", []):
        msg = choice.get("message", {})
        finish = choice.get("finish_reason")
        tool_calls = msg.get("tool_calls")
        content = msg.get("content")

        if tool_calls:
            # Emit tool_call name chunk first
            tc_deltas = []
            for idx, tc in enumerate(tool_calls):
                tc_deltas.append({
                    "index": idx,
                    "id": tc.get("id", f"call_{idx}"),
                    "type": "function",
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": "",
                    },
                })
            chunks.append({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": choice.get("index", 0),
                    "delta": {"tool_calls": tc_deltas},
                    "finish_reason": None,
                }],
            })
            # Emit arguments chunks (one per tool_call, streaming style)
            for idx, tc in enumerate(tool_calls):
                args = tc.get("function", {}).get("arguments", "")
                if isinstance(args, dict):
                    import json as _json
                    args = _json.dumps(args, ensure_ascii=False)
                chunks.append({
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": choice.get("index", 0),
                        "delta": {"tool_calls": [{
                            "index": idx,
                            "function": {"arguments": args},
                        }]},
                        "finish_reason": None,
                    }],
                })
            # Emit finish
            chunks.append({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": choice.get("index", 0),
                    "delta": {},
                    "finish_reason": "tool_calls",
                }],
            })
        elif content:
            # Emit content in streaming style
            chunks.append({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": choice.get("index", 0),
                    "delta": {"content": content},
                    "finish_reason": None,
                }],
            })
            # Emit finish
            fr = finish or "stop"
            chunks.append({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": choice.get("index", 0),
                    "delta": {},
                    "finish_reason": fr,
                }],
            })
        else:
            # Empty response — just finish
            chunks.append({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": choice.get("index", 0),
                    "delta": {},
                    "finish_reason": finish or "stop",
                }],
            })

    if usage:
        # Attach usage to the last chunk
        chunks[-1]["usage"] = usage

    return chunks


def _is_kimi_json_chunk(chunk: dict) -> bool:
    """Check if a chunk contains a Kimi-generated OpenAI JSON result."""
    for choice in chunk.get("choices", []):
        if "_openai_json" in choice.get("delta", {}):
            return True
    return False


async def _nonstream_to_json(
    provider: WebProvider,
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
) -> dict:
    """Aggregate a streaming provider response into a single JSON body.

    For Kimi (JsonApiStrategy): detects ``_openai_json`` chunks and converts
    them to a standard ``chat.completion`` response.
    For other providers: falls back to the original delta-aggregation logic.
    """
    chat_id = _openai_id()
    text_parts: list[str] = []
    tool_calls: list[dict] | None = None
    usage: dict | None = None
    finish_reason: str = "stop"
    openai_json: dict | None = None

    async for chunk in provider.chat(messages, model=model, stream=True, tools=tools):
        # Check if this is a Kimi JSON result chunk
        if _is_kimi_json_chunk(chunk):
            for choice in chunk.get("choices", []):
                oj = choice.get("delta", {}).get("_openai_json")
                if oj:
                    openai_json = oj
            continue

        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices", []):
            delta = choice.get("delta", {})
            content = delta.get("content", "")
            if content:
                text_parts.append(content)
            tc = delta.get("tool_calls")
            if tc:
                if tool_calls is None:
                    tool_calls = []
                for t in tc:
                    idx = t.get("index", len(tool_calls))
                    while len(tool_calls) <= idx:
                        tool_calls.append({
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                    entry = tool_calls[idx]
                    if t.get("id"):
                        entry["id"] = t["id"]
                    if t.get("function", {}).get("name"):
                        entry["function"]["name"] = t["function"]["name"]
                    if t.get("function", {}).get("arguments"):
                        entry["function"]["arguments"] += t["function"]["arguments"]
            fr = choice.get("finish_reason")
            if fr:
                finish_reason = fr

    # If Kimi returned a complete OpenAI JSON, use it directly
    if openai_json is not None:
        result = openai_json
        # Ensure required fields
        if "id" not in result:
            result["id"] = chat_id
        if "object" not in result:
            result["object"] = "chat.completion"
        if "created" not in result:
            result["created"] = int(time.time())
        if "model" not in result:
            result["model"] = model
        if usage and "usage" not in result:
            result["usage"] = usage
        return result

    # Fallback: aggregated text/tool_calls from delta chunks
    text = "".join(text_parts)
    message: dict = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ── Route handlers ─────────────────────────────────────────────────────


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    """OpenAI-compatible /v1/chat/completions endpoint."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    model = body.get("model", "")
    stream = body.get("stream", False)
    messages = body.get("messages", [])
    tools = body.get("tools", None)

    # Resolve provider from model name
    provider_name = _resolve_provider(model)
    if not provider_name:
        return web.json_response(
            {"error": f"Unknown model '{model}'"},
            status=400,
        )

    provider = _PROVIDER_INSTANCES[provider_name]

    try:
        if stream:
            return await _handle_stream(provider, messages, model, request, tools=tools)
        else:
            result = await _nonstream_to_json(provider, messages, model, tools=tools)
            return web.json_response(result)
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.error("completion error from %s: %s", provider.name, exc)
        return web.json_response({"error": str(exc)}, status=500)


async def _handle_stream(
    provider: WebProvider,
    messages: list[dict],
    model: str,
    request: web.Request,
    tools: list[dict] | None = None,
) -> web.StreamResponse:
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    chunk_count = 0
    openai_json: dict | None = None
    try:
        async for chunk in provider.chat(messages, model=model, stream=True, tools=tools):
            chunk_count += 1

            # Check if this is a Kimi JSON result — accumulate and emit as
            # proper SSE chunks instead of passing through raw.
            if _is_kimi_json_chunk(chunk):
                for choice in chunk.get("choices", []):
                    oj = choice.get("delta", {}).get("_openai_json")
                    if oj:
                        openai_json = oj
                continue

            await response.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))

        # If Kimi returned a complete OpenAI JSON, convert to SSE chunks
        if openai_json is not None:
            logger.info("[cookie-web] Kimi returned OpenAI JSON: finish_reason=%s",
                openai_json.get("choices", [{}])[0].get("finish_reason", "?"))
            for sse_chunk in _openai_json_to_sse_chunks(openai_json):
                await response.write(f"data: {json.dumps(sse_chunk, ensure_ascii=False)}\n\n".encode("utf-8"))

        await response.write(b"data: [DONE]\n\n")
    except web.HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "streaming error from %s after %d chunks: %s",
            provider.name, chunk_count, exc,
        )
        raise

    return response


def _resolve_provider(model: str) -> str | None:
    """Find which provider handles *model*.

    Checks exact match first, then prefix match.
    """
    if model in _MODEL_TO_PROVIDER:
        return _MODEL_TO_PROVIDER[model]
    for internal_name, prov_name in _MODEL_TO_PROVIDER.items():
        if model.startswith(internal_name):
            return prov_name
    return None


async def handle_models(request: web.Request) -> web.Response:
    models = [
        {"id": m, "object": "model", "created": int(time.time()), "owned_by": "cookie-web"}
        for m in _MODEL_TO_PROVIDER
    ]
    return web.json_response({"object": "list", "data": models})


async def handle_health(request: web.Request) -> web.Response:
    creds = CredentialStore().load()
    providers = {}
    for p in ["deepseek", "gemini", "claude", "chatgpt", "kimi"]:
        c = creds.get(p, {})
        providers[p] = {
            "ready": bool(c.get("cookie") or c.get("api_key") or c.get("api_keys")),
            "has_bearer": bool(c.get("bearer") or c.get("token") or c.get("api_key") or c.get("api_keys")),
        }
    return web.json_response({
        "status": "ok",
        "providers": providers,
        "models": list(_MODEL_TO_PROVIDER.keys()),
    })


async def handle_set_creds(request: web.Request) -> web.Response:
    """Set credentials via API (POST with JSON body)."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    provider = body.get("provider", "")
    if provider not in PROVIDER_MAP:
        return web.json_response({"error": f"Invalid provider: {provider}"}, status=400)

    creds = {k: v for k, v in body.items() if k != "provider"}
    CredentialStore().set_provider_credentials(provider, creds)
    logger.info("Credentials updated for %s", provider)
    return web.json_response({"status": "ok", "provider": provider})


# ── App factory ────────────────────────────────────────────────────────


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_get("/v1/health", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/v1/credentials", handle_set_creds)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host="127.0.0.1", port=PROXY_PORT)
