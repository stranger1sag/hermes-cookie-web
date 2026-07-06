#!/usr/bin/env python3
"""Cookie-web provider CLI for login and credential management."""

import argparse, asyncio, sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Cookie-web provider management")
    sub = parser.add_subparsers(dest="command")

    login_p = sub.add_parser("login", help="Capture credentials (browser or manual)")
    login_p.add_argument("--provider", choices=["deepseek", "claude", "chatgpt", "kimi"], default="deepseek")
    login_p.add_argument("--mode", choices=["auto", "manual"], default="manual",
                         help="auto=launch Chrome, manual=paste cookies (default: manual)")
    login_p.add_argument("--cookie", help="Cookie string (for manual mode, non-interactive)")
    login_p.add_argument("--bearer", help="Bearer token (for manual mode, non-interactive)")
    login_p.add_argument("--session-key", help="Claude sessionKey (for manual mode)")
    login_p.add_argument("--session-token", help="ChatGPT session token (for manual mode)")
    login_p.add_argument("--api-key", help="Kimi API key from platform.moonshot.ai (for manual mode)")

    sub.add_parser("status", help="Show credential status")

    args = parser.parse_args()

    if args.command == "status":
        _status()
    elif args.command == "login":
        if args.mode == "manual":
            _manual_login(args)
        else:
            asyncio.run(_auto_login(args.provider))
    else:
        parser.print_help()


def _status():
    try:
        from storage.store import CredentialStore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from storage.store import CredentialStore
    store = CredentialStore()
    creds = store.load()
    print("Cookie-web credential status:")
    for provider in ["deepseek", "claude", "chatgpt", "kimi"]:
        c = creds.get(provider, {})
        if c:
            print(f"  {provider}: ✅ {len(c.get('cookie', ''))} chars cookie" +
                  (f", {len(c.get('bearer', c.get('token', '')))} chars token" if c.get('bearer') or c.get('token') else ""))
        else:
            print(f"  {provider}: ❌ No credentials")


def _manual_login(args):
    """Manual paste login — paste cookies from another machine's browser."""
    try:
        from storage.store import CredentialStore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from storage.store import CredentialStore
    store = CredentialStore()

    if args.cookie:
        cookie_str = args.cookie
    else:
        print(f"\n=== Manual {args.provider.title()} Login ===")
        print(f"1. On your other computer, open Chrome and go to {_login_urls[args.provider]}")
        print(f"2. Login if needed")
        print(f"3. Open DevTools (F12) -> {'Application -> Cookies' if args.provider != 'deepseek' else 'Network tab'}")
        if args.provider == "deepseek":
            print(f"4. Find a request to /api/v0/chat/completion, copy the 'Cookie:' header value")
            print(f"5. Also copy the 'Authorization: Bearer ...' header value")
        elif args.provider == "claude":
            print(f"4. Copy the 'sessionKey' cookie value (sk-ant-sid...)")
        elif args.provider == "kimi":
            print(f"4. Copy your Kimi API key from https://platform.moonshot.ai/console/api-keys")
            print(f"   Or paste the cookie string from the browser's Application tab")
        else:
            print(f"4. Copy the '__Secure-next-auth.session-token' cookie value")
        print()
        cookie_str = input("Paste cookie string: ").strip()

    if args.provider == "deepseek":
        if args.bearer:
            bearer = args.bearer
        else:
            bearer = input("Paste Bearer token (or Enter to skip): ").strip()
        cookie_str = cookie_str.replace("Cookie: ", "", 1).strip()
        if bearer:
            bearer = bearer.replace("Authorization: ", "", 1).replace("Bearer ", "", 1).strip()
        creds = {
            "cookie": cookie_str,
            "bearer": bearer,
            "token": bearer,
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
    elif args.provider == "claude":
        sk = args.session_key or input("Paste sessionKey value (sk-ant-...): ").strip()
        full_cookie = f"sessionKey={sk}; {cookie_str}" if cookie_str and not cookie_str.startswith("sessionKey=") else (f"sessionKey={sk}" if sk else cookie_str)
        creds = {
            "sessionKey": sk or cookie_str.replace("sessionKey=", "", 1).split(";")[0].strip(),
            "cookie": full_cookie,
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
    elif args.provider == "kimi":
        print("\nKimi 使用浏览器 JWT token 认证。")
        print("方式一：在 kimi.com 登录后，F12 → Application → Cookies → 复制 kimi-auth 的值")
        print("方式二：F12 → Network → 找任意请求 → 复制 Authorization: Bearer <token> 的值")
        print()
        bearer = args.bearer or args.api_key or input("Paste JWT token (Bearer eyJ...): ").strip()
        creds = {
            "bearer": bearer,
            "api_key": bearer,
            "token": bearer,
            "cookie": cookie_str,
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
    else:  # chatgpt
        st = args.session_token or input("Paste __Secure-next-auth.session-token value: ").strip()
        full_cookie = f"__Secure-next-auth.session-token={st}; {cookie_str}" if cookie_str else f"__Secure-next-auth.session-token={st}"
        creds = {
            "sessionToken": st,
            "cookie": full_cookie,
            "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

    store.set_provider_credentials(args.provider, creds)
    print(f"✅ {args.provider} credentials saved")


_login_urls = {
    "deepseek": "https://chat.deepseek.com",
    "claude": "https://claude.ai",
    "chatgpt": "https://chatgpt.com",
    "kimi": "https://kimi.com",
}


async def _auto_login(provider: str):
    """Launch Chrome and capture credentials automatically."""
    print(f"Opening browser for {provider} login...")
    try:
        from browser.chrome import launch_chrome
        from storage.store import CredentialStore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from browser.chrome import launch_chrome
        from storage.store import CredentialStore

    store = CredentialStore()
    auth_classes = {
        "deepseek": ("auth.deepseek", "DeepSeekAuth"),
        "claude": ("auth.claude", "ClaudeAuth"),
        "chatgpt": ("auth.chatgpt", "ChatGPTAuth"),
        "kimi": ("auth.kimi", "KimiAuth"),
    }

    proc = launch_chrome()
    print(f"Chrome launched (PID {proc.pid}). Please log in to {_login_urls[provider]}")
    print("Press Enter when done...")
    input()

    mod_path, cls_name = auth_classes[provider]
    mod = __import__(mod_path, fromlist=[cls_name])
    auth_cls = getattr(mod, cls_name)
    auth = auth_cls("http://127.0.0.1:9222")
    creds = await auth.capture_credentials()

    if creds:
        store.set_provider_credentials(provider, creds)
        print(f"✅ {provider} credentials saved")
    else:
        print(f"❌ Failed to capture {provider} credentials")

    proc.terminate()


if __name__ == "__main__":
    main()
