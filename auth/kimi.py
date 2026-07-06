"""Kimi (Moonshot) authentication via browser cookie capture."""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class KimiAuth:
    """Capture Kimi credentials from browser."""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222"):
        self.cdp_url = cdp_url

    async def capture_credentials(self) -> Optional[dict]:
        """Capture Kimi JWT token and cookies from browser."""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(self.cdp_url)
                context = browser.contexts[0]

                page = None
                for pg in context.pages:
                    if "kimi.com" in pg.url or "moonshot.cn" in pg.url:
                        page = pg
                        break

                if not page:
                    page = await context.new_page()
                    await page.goto("https://kimi.com", wait_until="networkidle")

                cookies = await context.cookies(["https://kimi.com", "https://www.kimi.com"])
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

                jwt = ""
                for c in cookies:
                    if c["name"] == "kimi-auth":
                        jwt = c["value"]
                        break

                jwt_from_ls = await page.evaluate("""() => {
                    try {
                        const auth = localStorage.getItem('kimi-auth') || localStorage.getItem('auth');
                        if (auth) {
                            const parsed = JSON.parse(auth);
                            return parsed.accessToken || parsed.token || parsed.jwt || auth;
                        }
                    } catch (e) {}
                    try {
                        return localStorage.getItem('kimi-auth') || '';
                    } catch (e) {}
                    return '';
                }""")

                device_id = await page.evaluate("""() => {
                    try {
                        return localStorage.getItem('kimi_device_id') || '';
                    } catch (e) {}
                    return '';
                }""")

                user_agent = await page.evaluate("() => navigator.userAgent")

                final_jwt = jwt or jwt_from_ls

                return {
                    "cookie": cookie_str,
                    "bearer": final_jwt,
                    "token": final_jwt,
                    "api_key": final_jwt,
                    "deviceId": device_id,
                    "userAgent": user_agent,
                }

        except Exception as e:
            logger.error(f"Failed to capture Kimi credentials: {e}")
            return None

    async def validate_credentials(self, credentials: dict) -> bool:
        """Validate captured credentials."""
        return bool(credentials.get("bearer") or credentials.get("cookie"))
