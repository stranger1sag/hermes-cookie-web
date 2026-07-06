"""Chrome process management for cookie-web provider."""

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class ChromeManager:
    """Manages Chrome browser connection via CDP."""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222"):
        self.cdp_url = cdp_url
        self._ws_url: Optional[str] = None

    async def is_reachable(self, timeout: float = 0.5) -> bool:
        """Check if Chrome is reachable via CDP."""
        try:
            version_url = f"{self.cdp_url}/json/version"
            async with aiohttp.ClientSession() as session:
                async with session.get(version_url, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return bool(data.get("webSocketDebuggerUrl"))
        except Exception:
            pass
        return False

    async def get_websocket_url(self, timeout: float = 0.5) -> Optional[str]:
        """Get Chrome's WebSocket debugger URL."""
        if self._ws_url and await self._test_ws_connection(self._ws_url):
            return self._ws_url

        try:
            version_url = f"{self.cdp_url}/json/version"
            async with aiohttp.ClientSession() as session:
                async with session.get(version_url, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        ws_url = data.get("webSocketDebuggerUrl")
                        if ws_url:
                            self._ws_url = ws_url
                            return ws_url
        except Exception as e:
            logger.debug(f"Failed to get WebSocket URL: {e}")

        return None

    async def _test_ws_connection(self, ws_url: str, timeout: float = 0.8) -> bool:
        """Test if WebSocket connection is possible."""
        try:
            import websockets
            async with websockets.connect(ws_url, open_timeout=timeout) as ws:
                return True
        except Exception:
            return False

    async def get_version_info(self) -> Optional[dict]:
        """Get Chrome version information."""
        try:
            version_url = f"{self.cdp_url}/json/version"
            async with aiohttp.ClientSession() as session:
                async with session.get(version_url) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return None

    async def list_tabs(self) -> list[dict]:
        """List open browser tabs."""
        try:
            tabs_url = f"{self.cdp_url}/json/list"
            async with aiohttp.ClientSession() as session:
                async with session.get(tabs_url) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return []

    async def find_tab(self, url_pattern: str) -> Optional[dict]:
        """Find a tab matching the URL pattern."""
        tabs = await self.list_tabs()
        for tab in tabs:
            if url_pattern in tab.get("url", ""):
                return tab
        return None

    async def create_tab(self, url: str) -> Optional[dict]:
        """Create a new browser tab."""
        try:
            new_url = f"{self.cdp_url}/json/new?{url}"
            async with aiohttp.ClientSession() as session:
                async with session.get(new_url) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return None


def launch_chrome(
    cdp_port: int = 9222,
    user_data_dir: Optional[str] = None,
    chrome_path: Optional[str] = None,
) -> subprocess.Popen:
    """Launch Chrome with remote debugging enabled."""
    if chrome_path is None:
        chrome_path = _find_chrome_executable()

    if chrome_path is None:
        raise RuntimeError("Chrome executable not found")

    args = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        "--no-first-run",
        "--disable-features=AutomationControlled",
        "--disable-infobars",
    ]

    if user_data_dir:
        args.append(f"--user-data-dir={user_data_dir}")

    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _find_chrome_executable() -> Optional[str]:
    """Find Chrome executable on the system."""
    import platform

    system = platform.system()

    if system == "Darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
    elif system == "Windows":
        paths = [
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        ]
    else:
        return None

    for path in paths:
        if Path(path).exists():
            return path

    return None
