"""DeepSeek authentication via browser cookie capture."""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DeepSeekAuth:
    """Capture DeepSeek credentials from browser."""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222"):
        self.cdp_url = cdp_url

    async def capture_credentials(self) -> Optional[dict]:
        """Capture DeepSeek cookie and bearer token from browser."""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]

                # Find or create DeepSeek page
                page = None
                for pg in context.pages:
                    if "deepseek.com" in pg.url:
                        page = pg
                        break

                if not page:
                    page = await context.new_page()
                    await page.goto("https://chat.deepseek.com", wait_until="networkidle")

                # Capture cookies
                cookies = await context.cookies(["https://chat.deepseek.com"])
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

                # Capture bearer token from localStorage
                bearer = await page.evaluate("""() => {
                    try {
                        const data = localStorage.getItem('user');
                        if (data) {
                            const parsed = JSON.parse(data);
                            return parsed.token || '';
                        }
                    } catch (e) {}
                    return '';
                }""")

                user_agent = await page.evaluate("() => navigator.userAgent")

                return {
                    "cookie": cookie_str,
                    "bearer": bearer,
                    "userAgent": user_agent,
                }

        except Exception as e:
            logger.error(f"Failed to capture DeepSeek credentials: {e}")
            return None

    async def validate_credentials(self, credentials: dict) -> bool:
        """Validate captured credentials."""
        return bool(credentials.get("cookie") or credentials.get("bearer"))
