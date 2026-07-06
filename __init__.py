"""Cookie Web provider profile.

Provides free AI access via browser cookie simulation.
Supports DeepSeek, Claude, ChatGPT, Gemini, and Kimi web interfaces.
"""

import logging
import os
import signal
import subprocess
import sys
import time

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

PROXY_PORT = int(os.environ.get("COOKIE_WEB_PROXY_PORT", "13000"))
os.environ.setdefault("COOKIE_WEB_PROXY_PORT", str(PROXY_PORT))
PROXY_BASE_URL = f"http://127.0.0.1:{PROXY_PORT}"

_proxy_process: subprocess.Popen | None = None
_proxy_started = False


def _try_start_proxy() -> bool:
    """Start the proxy server as an external subprocess.

    Returns True if proxy is running (either started now or already up).
    Never raises — logs and returns False on failure.
    """
    global _proxy_process, _proxy_started

    if _proxy_started:
        return True

    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.3)
        result = s.connect_ex(("127.0.0.1", PROXY_PORT))
        s.close()
        if result == 0:
            _proxy_started = True
            return True
    except Exception:
        s.close()

    proxy_script = os.path.join(os.path.dirname(__file__), "proxy_server.py")
    if not os.path.exists(proxy_script):
        logger.warning("cookie-web: proxy_server.py not found at %s", proxy_script)
        return False

    try:
        proc = subprocess.Popen(
            [sys.executable, proxy_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.warning("cookie-web: cannot start proxy subprocess: %s", exc)
        return False

    for _ in range(10):
        time.sleep(0.5)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", PROXY_PORT)) == 0:
                _proxy_process = proc
                _proxy_started = True
                s.close()
                logger.info("cookie-web proxy started on %s (PID %d)", PROXY_BASE_URL, proc.pid)
                return True
            s.close()
        except Exception:
            s.close()

    logger.warning("cookie-web proxy subprocess did not become ready in 5s (PID %d)", proc.pid)
    try:
        proc.kill()
    except Exception:
        pass
    return False


class CookieWebProfile(ProviderProfile):
    """Cookie-based free AI provider profile."""

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Return list of supported models."""
        return [
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-chat-search",
            "deepseek-reasoner-search",
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-haiku-4-6",
            "gpt-4",
            "gpt-4-turbo",
            "gpt-4o",
            "kimi-k2.5",
            "kimi-k2.6",
            "kimi-k2-thinking",
            "moonshot-v1-8k",
            "moonshot-v1-32k",
            "moonshot-v1-128k",
        ]


cookie_web = CookieWebProfile(
    name="cookie-web",
    aliases=(
        "deepseek-free",
        "claude-free",
        "chatgpt-free",
        "browser-free",
    ),
    display_name="Cookie Web (Free)",
    description="Free AI via browser cookie simulation",
    signup_url="",
    env_vars=("COOKIE_WEB_PROXY_PORT",),  # Config env var (not an API key)
    base_url=PROXY_BASE_URL,
    auth_type="api_key",
    supports_health_check=False,
    supports_vision=True,
    default_max_tokens=4096,
)

register_provider(cookie_web)

# Try to start proxy non-blockingly at import time
if not _try_start_proxy():
    logger.error("cookie-web proxy failed to start at import time")
