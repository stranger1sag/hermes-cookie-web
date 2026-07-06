"""CDP (Chrome DevTools Protocol) helpers."""

import json
import logging
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class CDPHelper:
    """Helper class for CDP operations."""

    @staticmethod
    def get_auth_headers(url: str) -> dict[str, str]:
        """Extract authentication headers from URL."""
        parsed = urlparse(url)
        headers = {}

        if parsed.username or parsed.password:
            import base64
            auth_str = f"{parsed.username or ''}:{parsed.password or ''}"
            auth_bytes = base64.b64encode(auth_str.encode()).decode()
            headers["Authorization"] = f"Basic {auth_bytes}"

        return headers

    @staticmethod
    def normalize_ws_url(ws_url: str, cdp_url: str) -> str:
        """Normalize WebSocket URL."""
        if ws_url.startswith("ws://") or ws_url.startswith("wss://"):
            return ws_url

        # Convert HTTP URL to WebSocket URL
        parsed = urlparse(cdp_url)
        ws_protocol = "wss" if parsed.scheme == "https" else "ws"
        return f"{ws_protocol}://{parsed.hostname}:{parsed.port}{ws_url}"

    @staticmethod
    async def send_cdp_command(
        ws_url: str,
        method: str,
        params: Optional[dict] = None,
        timeout: float = 5.0,
    ) -> Optional[dict]:
        """Send a CDP command via WebSocket."""
        try:
            import websockets

            async with websockets.connect(
                ws_url,
                open_timeout=timeout,
                close_timeout=timeout,
            ) as ws:
                command = {
                    "id": 1,
                    "method": method,
                    "params": params or {},
                }

                await ws.send(json.dumps(command))
                response = await asyncio.wait_for(ws.recv(), timeout=timeout)
                return json.loads(response)

        except Exception as e:
            logger.debug(f"CDP command failed: {e}")
            return None

    @staticmethod
    async def evaluate_js(
        ws_url: str,
        expression: str,
        timeout: float = 5.0,
    ) -> Optional[Any]:
        """Evaluate JavaScript expression in browser context."""
        result = await CDPHelper.send_cdp_command(
            ws_url,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            timeout,
        )

        if result and "result" in result:
            return result["result"].get("result", {}).get("value")

        return None

    @staticmethod
    async def get_cookies(
        ws_url: str,
        urls: Optional[list[str]] = None,
        timeout: float = 5.0,
    ) -> list[dict]:
        """Get browser cookies."""
        params = {}
        if urls:
            params["urls"] = urls

        result = await CDPHelper.send_cdp_command(
            ws_url,
            "Network.getCookies",
            params,
            timeout,
        )

        if result and "result" in result:
            return result["result"].get("cookies", [])

        return []

    @staticmethod
    async def set_cookie(
        ws_url: str,
        cookie: dict,
        timeout: float = 5.0,
    ) -> bool:
        """Set a browser cookie."""
        result = await CDPHelper.send_cdp_command(
            ws_url,
            "Network.setCookie",
            cookie,
            timeout,
        )

        return bool(result and result.get("result", {}).get("success"))
