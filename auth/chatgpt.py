"""ChatGPT authentication via browser cookie capture."""

import asyncio
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


class ChatGPTAuth:
    """Capture ChatGPT credentials from browser."""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222"):
        self.cdp_url = cdp_url

    async def capture_credentials(self) -> Optional[dict]:
        """Capture ChatGPT session token and cookies from browser."""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]

                # Find or create ChatGPT page
                page = None
                for pg in context.pages:
                    if "chatgpt.com" in pg.url or "chat.openai.com" in pg.url:
                        page = pg
                        break

                if not page:
                    page = await context.new_page()
                    await page.goto("https://chatgpt.com", wait_until="networkidle")

                # Capture cookies
                cookies = await context.cookies(["https://chatgpt.com"])
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

                # Extract session token
                session_token = ""
                for cookie in cookies:
                    if cookie["name"] == "__Secure-next-auth.session-token":
                        session_token = cookie["value"]
                        break

                user_agent = await page.evaluate("() => navigator.userAgent")

                return {
                    "sessionToken": session_token,
                    "cookie": cookie_str,
                    "userAgent": user_agent,
                }

        except Exception as e:
            logger.error(f"Failed to capture ChatGPT credentials: {e}")
            return None

    async def validate_credentials(self, credentials: dict) -> bool:
        """Validate captured credentials."""
        return bool(credentials.get("sessionToken") or credentials.get("cookie"))
