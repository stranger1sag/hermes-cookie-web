"""Kimi Web API provider (Connect RPC protocol).

Uses the internal Kimi web API at www.kimi.com with JWT credentials
captured from the browser. The API uses Connect RPC with 5-byte framing
on both request and response.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import time

from datetime import datetime, timezone
from hashlib import md5
from pathlib import Path
from threading import Lock
from typing import Any, AsyncGenerator

import aiohttp
from aiohttp import web

from .base import WebProvider, ModelRoute
from .identity import DefaultIdentity, IdentityStrategy, JsonApiStrategy

logger = logging.getLogger("cookie-web-proxy.kimi")

KIMI_WEB_URL = "https://www.kimi.com"
CHAT_ENDPOINT = (
    f"{KIMI_WEB_URL}/apiv2/kimi.gateway.chat.v1.ChatService/Chat"
)

MODEL_ROUTES = [
    ModelRoute(provider="kimi", internal_name="kimi-k2.5"),
    ModelRoute(provider="kimi", internal_name="kimi-k2.6"),
    ModelRoute(provider="kimi", internal_name="kimi-k2-thinking"),
    ModelRoute(provider="kimi", internal_name="moonshot-v1-8k"),
    ModelRoute(provider="kimi", internal_name="moonshot-v1-32k"),
    ModelRoute(provider="kimi", internal_name="moonshot-v1-128k"),
]

_SCENARIO_MAP = {
    "kimi-k2.5": "SCENARIO_K2D5",
    "kimi-k2.6": "SCENARIO_K2D6",
    "kimi-k2-thinking": "SCENARIO_K2_THINKING",
}

_XID_CODING = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
    "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v",
]
_xid_counter = 0
_xid_lock = Lock()
_xid_machine_id = md5(os.uname().nodename.encode()).digest()[:3]
_xid_pid = os.getpid() & 0xFFFF


def _make_xid() -> str:
    global _xid_counter
    with _xid_lock:
        _xid_counter = (_xid_counter + 1) & 0xFFFFFF
        counter = _xid_counter
    t = int(time.time())
    b = bytearray(12)
    struct.pack_into(">I", b, 0, t)
    b[4:7] = _xid_machine_id
    struct.pack_into(">H", b, 7, _xid_pid)
    b[9] = (counter >> 16) & 0xFF
    b[10] = (counter >> 8) & 0xFF
    b[11] = counter & 0xFF

    out = []
    for i in range(0, 12, 5):
        chunk = b[i : i + 5]
        chunk = chunk.ljust(5, b"\x00")
        val = int.from_bytes(chunk, "big")
        for j in range(8):
            out.append(_XID_CODING[(val >> (5 * (7 - j))) & 0x1F])
    return "".join(out[:20])


def _decode_jwt(jwt: str) -> dict:
    try:
        parts = jwt.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        pad = 4 - len(payload) % 4
        if pad != 4:
            payload += "=" * pad
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _build_headers(jwt: str, creds: dict) -> dict:
    payload = _decode_jwt(jwt)
    device_id = creds.get("deviceId", "") or payload.get("device_id", "")
    session_id = payload.get("ssid", "")
    traffic_id = payload.get("sub", "")

    headers = {
        "Content-Type": "application/connect+json",
        "Accept": "application/connect+json",
        "Authorization": f"Bearer {jwt}",
        "Origin": KIMI_WEB_URL,
        "Referer": f"{KIMI_WEB_URL}/",
        "connect-protocol-version": "1",
        "x-language": "zh-CN",
        "x-msh-platform": "web",
        "x-msh-version": "1.0.0",
        "r-timezone": "Asia/Shanghai",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

    user_agent = creds.get("userAgent", "")
    if user_agent:
        headers["User-Agent"] = user_agent
    if device_id:
        headers["x-msh-device-id"] = device_id
    if session_id:
        headers["x-msh-session-id"] = session_id
    if traffic_id:
        headers["x-traffic-id"] = traffic_id

    cookie = creds.get("cookie", "")
    if cookie:
        headers["Cookie"] = cookie

    return headers


def _frame_request(body: dict) -> bytes:
    """Apply Connect RPC 5-byte framing to request body."""
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    return b"\x00" + len(raw).to_bytes(4, "big") + raw


def _normalize_messages(
    messages: list[dict],
) -> tuple[list[dict], list[str]]:
    """Normalize OpenAI multimodal messages for text-only identity injection.

    Returns (text_messages, image_urls) where:
    - text_messages has content converted to plain text
    - image_urls is a list of (data_uri) strings
    """
    image_urls: list[str] = []
    text_messages: list[dict] = []

    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "")

        if isinstance(content, str):
            text_messages.append(msg)
            continue

        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                t = part.get("type", "")
                if t == "text":
                    text_parts.append(part.get("text", ""))
                elif t == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if url:
                        if url.startswith("file://"):
                            image_urls.append(url)
                        elif url.startswith("data:"):
                            image_urls.append(url)
            text_msg = {**msg, "content": "\n".join(text_parts)}
            text_messages.append(text_msg)

    return text_messages, image_urls


def _load_image_data(url: str) -> tuple[str, str]:
    """Load an image from a file:// URL and return (base64_data, mime)."""
    import mimetypes

    path = url[7:] if url.startswith("file://") else url
    with open(path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    return b64, mime


_shared_session: aiohttp.ClientSession | None = None


class KimiProvider(WebProvider):
    def __init__(self, identity: IdentityStrategy | None = None):
        self._identity = identity or JsonApiStrategy()
        self._creds = _load_creds()

    @property
    def name(self) -> str:
        return "kimi"

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
        creds = self._creds.get("kimi")
        if not creds:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "No Kimi credentials"})
            )

        jwt = (
            creds.get("bearer", "")
            or creds.get("api_key", "")
            or creds.get("token", "")
        )
        if not jwt:
            raise web.HTTPUnauthorized(
                text=json.dumps({"error": "No Kimi JWT token in credentials"})
            )

        headers = _build_headers(jwt, creds)
        text_messages, image_urls = _normalize_messages(messages)
        prompt = self._identity.build_prompt(text_messages, tools=tools, model=model)
        scenario = _SCENARIO_MAP.get(model, "SCENARIO_K2D5")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        blocks = [{"messageId": "", "text": {"content": prompt}}]
        for url in image_urls:
            b64_data, mime = _load_image_data(url)
            img_md = f"\n![image](data:{mime};base64,{b64_data})"
            blocks[0]["text"]["content"] += img_md

        msg_id = _make_xid()
        body = {
            "op": "set",
            "mask": "message",
            "eventOffset": 1,
            "message": {
                "id": msg_id,
                "parentId": None,
                "role": "user",
                "status": "MESSAGE_STATUS_COMPLETED",
                "blocks": blocks,
                "scenario": scenario,
                "createTime": now,
                "isGoal": False,
            },
        }

        global _shared_session
        if _shared_session is None or _shared_session.closed:
            _shared_session = aiohttp.ClientSession()

        framed = _frame_request(body)
        async with _shared_session.post(
            CHAT_ENDPOINT, headers=headers, data=framed
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error("Kimi HTTP %d: %s", resp.status, error_text[:500])
                if resp.status == 401:
                    raise web.HTTPUnauthorized(
                        text=json.dumps(
                            {"error": "Kimi JWT expired, please re-login"}
                        )
                    )
                raise RuntimeError(
                    f"Kimi API error {resp.status}: {error_text[:300]}"
                )

            chat_id = f"chatcmpl-{int(time.time() * 1000)}"
            parser = JsonOutputParser()
            got_text = False

            async for event in _iter_kimi_stream(resp):
                if event.get("done") is not None:
                    # End of stream — try to extract JSON from accumulated text
                    result = parser.try_extract()
                    if result is not None:
                        logger.info("[Kimi JSON] Extracted OpenAI JSON result: finish_reason=%s",
                            result.get("choices", [{}])[0].get("finish_reason", "?"))
                        yield {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": model,
                            "choices": [{
                                "index": 0,
                                "delta": {"_openai_json": result},
                                "finish_reason": None,
                            }],
                        }
                    else:
                        # Fallback: return remaining text as plain content
                        remaining = parser.get_remaining_text()
                        logger.info("[Kimi JSON] No JSON found, fallback to plain text (%d chars)", len(remaining))
                        if remaining:
                            yield _make_chunk(chat_id, model, remaining)
                        yield _make_chunk(chat_id, model, "", "stop")
                    return

                if event.get("error"):
                    logger.error("Kimi error event: %s", event)
                    raise RuntimeError(
                        f"Kimi error: {event.get('error')}"
                    )

                if event.get("heartbeat"):
                    continue

                op = event.get("op", "")
                mask = event.get("mask", "")

                if op == "set" and mask == "message.status":
                    status = event.get("message", {}).get("status", "")
                    if status == "MESSAGE_STATUS_COMPLETED":
                        continue

                if op in ("set", "append") and mask in (
                    "block.text", "block.text.content",
                ):
                    block = event.get("block", {})
                    text = (block.get("text", {}) or {}).get("content", "")
                    if text:
                        got_text = True
                        parser.feed(text)


async def _iter_kimi_stream(
    resp: aiohttp.ClientResponse,
) -> AsyncGenerator[dict, None]:
    buf = b""
    while True:
        chunk = await resp.content.read(4096)
        if not chunk:
            break
        buf += chunk

        while len(buf) >= 5:
            flags = buf[0]
            msg_len = int.from_bytes(buf[1:5], "big")
            total = 5 + msg_len
            if len(buf) < total:
                break

            msg_bytes = buf[5:total]
            buf = buf[total:]

            text = msg_bytes.decode("utf-8", errors="replace").strip()
            if not text:
                continue

            try:
                obj = json.loads(text)
                logger.debug("Kimi stream msg: op=%s mask=%s",
                    obj.get("op", ""), obj.get("mask", ""))
                yield obj
            except json.JSONDecodeError:
                logger.debug("Skipping non-JSON in Kimi stream: %s", text[:100])

            if flags & 0x02:
                return


class JsonOutputParser:
    """Accumulates Kimi's streaming text and extracts a complete JSON object.

    Kimi streams character-by-character, so individual fragments are rarely
    a complete JSON. This buffer accumulates text, tracks brace depth, and
    emits the first complete JSON object found.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._found_json: dict | None = None

    def feed(self, text: str) -> None:
        if self._found_json is not None:
            return
        self._buf += text

    def try_extract(self) -> dict | None:
        """Try to extract a complete JSON object from the buffer."""
        if self._found_json is not None:
            return self._found_json

        buf = self._buf.strip()
        # Try to find the first complete JSON object by tracking brace depth
        start = -1
        depth = 0
        in_string = False
        escape = False

        for i, ch in enumerate(buf):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"' and (i == 0 or buf[i - 1] != '\\'):
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = buf[start:i + 1]
                    try:
                        obj = json.loads(candidate)
                        self._found_json = obj
                        return obj
                    except json.JSONDecodeError:
                        continue
        return None

    def get_remaining_text(self) -> str:
        """Return any text that wasn't part of a complete JSON."""
        if self._found_json is not None:
            return ""
        return self._buf


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


def _load_creds() -> dict[str, dict]:
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
