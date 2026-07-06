"""DeepSeek API client with PoW (Proof of Work) support."""

import asyncio
import base64
import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

# Path to the Node.js PoW solver script
POW_SOLVER_PATH = Path(__file__).parent.parent / "browser" / "pow_solver.mjs"


class DeepSeekClient:
    """DeepSeek web API client."""

    def __init__(self, cookie: str, bearer: str, user_agent: str):
        self.cookie = cookie
        self.bearer = bearer
        self.user_agent = user_agent
        self.base_url = "https://chat.deepseek.com"

    def _get_headers(self) -> dict:
        """Get request headers."""
        headers = {
            "Cookie": self.cookie,
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Referer": "https://chat.deepseek.com/",
            "Origin": "https://chat.deepseek.com",
            "x-client-platform": "web",
            "x-client-version": "1.7.0",
            "x-app-version": "20241129.1",
            "x-client-locale": "zh_CN",
            "x-client-timezone-offset": "28800",
        }

        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"

        return headers

    async def _create_pow_challenge(self, target_path: str) -> dict:
        """Create a PoW challenge."""
        url = f"{self.base_url}/api/v0/chat/create_pow_challenge"
        payload = {"target_path": target_path}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=self._get_headers(), json=payload
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Failed to create PoW challenge: {resp.status}")
                data = await resp.json()
                return data.get("data", {}).get("biz_data", {}).get("challenge", {})

    async def create_chat_session(self) -> dict:
        """Create a new chat session."""
        url = f"{self.base_url}/api/v0/chat_session/create"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=self._get_headers(), json={}
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Failed to create chat session: {resp.status}")
                data = await resp.json()
                biz_data = data.get("data", {}).get("biz_data", {})
                return {
                    "chat_session_id": biz_data.get("id") or biz_data.get("chat_session_id", ""),
                    "biz_id": biz_data.get("biz_id", ""),
                    "title": biz_data.get("title", ""),
                }

    def _solve_pow(self, challenge: dict) -> int:
        """Solve PoW challenge using Node.js solver."""
        try:
            # Call Node.js script to solve PoW
            result = subprocess.run(
                ["node", str(POW_SOLVER_PATH), json.dumps(challenge)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                raise RuntimeError(f"Node.js solver failed: {result.stderr}")

            output = json.loads(result.stdout)
            if output.get("success"):
                return int(output["answer"])
            else:
                raise RuntimeError(f"PoW solver error: {output.get('error')}")

        except subprocess.TimeoutExpired:
            raise RuntimeError("PoW solver timeout")
        except FileNotFoundError:
            # Fallback to Python implementation
            logger.warning("Node.js not found, using Python fallback")
            return self._solve_pow_python(challenge)

    def _solve_pow_python(self, challenge: dict) -> int:
        """Solve PoW challenge using Python (fallback)."""
        algorithm = challenge.get("algorithm", "")
        target = challenge.get("challenge", "")
        salt = challenge.get("salt", "")
        difficulty = challenge.get("difficulty", 1000)
        expire_at = challenge.get("expire_at", 0)

        if algorithm == "sha256":
            return self._solve_pow_sha256(target, salt, difficulty)
        elif algorithm == "DeepSeekHashV1":
            return self._solve_pow_deepseek_hash_v1(target, salt, difficulty, expire_at)
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

    def _solve_pow_sha256(self, target: str, salt: str, difficulty: int) -> int:
        """Solve PoW challenge using SHA256."""
        # Normalize difficulty
        target_difficulty = difficulty if difficulty <= 1000 else int(
            __import__("math").log2(difficulty)
        )

        nonce = 0
        while nonce < 1000000:
            input_str = f"{salt}{target}{nonce}"
            hash_hex = hashlib.sha256(input_str.encode()).hexdigest()

            # Count leading zero bits
            zero_bits = 0
            for char in hash_hex:
                val = int(char, 16)
                if val == 0:
                    zero_bits += 4
                else:
                    zero_bits += 32 - val.bit_length()
                    break

            if zero_bits >= target_difficulty:
                return nonce

            nonce += 1

        raise RuntimeError("SHA256 PoW timeout")

    def _solve_pow_deepseek_hash_v1(self, target: str, salt: str, difficulty: int, expire_at: int) -> int:
        """Solve PoW challenge using DeepSeekHashV1 (SHA3/Keccak)."""
        try:
            from Crypto.Hash import keccak
        except ImportError:
            # Fallback to SHA256 if pycryptodome is not available
            logger.warning("pycryptodome not available, using SHA256 fallback")
            return self._solve_pow_sha256(target, salt, difficulty)

        # Normalize difficulty
        target_difficulty = difficulty if difficulty <= 1000 else int(
            __import__("math").log2(difficulty)
        )

        nonce = 0
        while nonce < 1000000:
            # DeepSeekHashV1 format: prefix = salt_expire_at_, input = target
            # Then the WASM module does: hash(prefix + target + nonce)
            prefix = f"{salt}_{expire_at}_"
            input_str = f"{prefix}{target}{nonce}"
            
            # Use Keccak-256 (SHA3 variant)
            k = keccak.new(digest_bits=256)
            k.update(input_str.encode('utf-8'))
            hash_hex = k.hexdigest()

            # Count leading zero bits
            zero_bits = 0
            for char in hash_hex:
                val = int(char, 16)
                if val == 0:
                    zero_bits += 4
                else:
                    zero_bits += 32 - val.bit_length()
                    break

            if zero_bits >= target_difficulty:
                return nonce

            nonce += 1

        raise RuntimeError("DeepSeekHashV1 PoW timeout")

    async def chat_completions(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        """Call DeepSeek chat completions API."""
        # Create chat session
        session = await self.create_chat_session()
        session_id = session["chat_session_id"]

        # Create PoW challenge
        challenge = await self._create_pow_challenge("/api/v0/chat/completion")

        # Solve PoW
        answer = self._solve_pow(challenge)

        # Encode PoW response
        pow_response = base64.b64encode(
            json.dumps({**challenge, "answer": answer}).encode()
        ).decode()

        headers = self._get_headers()
        headers["x-ds-pow-response"] = pow_response

        # Get the last user message
        prompt = messages[-1]["content"] if messages else ""

        payload = {
            "chat_session_id": session_id,
            "parent_message_id": None,
            "prompt": prompt,
            "ref_file_ids": [],
            "thinking_enabled": True,
            "search_enabled": True,
            "preempt": False,
        }

        url = f"{self.base_url}/api/v0/chat/completion"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"DeepSeek API error: {resp.status} - {error_text}"
                    )
                return await resp.json()

    async def chat_completions_stream(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ):
        """Call DeepSeek chat completions API with streaming."""
        # Create PoW challenge
        challenge = await self._create_pow_challenge("/api/v0/chat/completion")

        # Solve PoW
        answer = self._solve_pow(challenge)

        # Encode PoW response
        pow_response = base64.b64encode(
            json.dumps({**challenge, "answer": answer}).encode()
        ).decode()

        headers = self._get_headers()
        headers["x-ds-pow-response"] = pow_response

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        url = f"{self.base_url}/api/v0/chat/completion"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(
                        f"DeepSeek API error: {resp.status} - {error_text}"
                    )

                async for line in resp.content:
                    if line:
                        yield line.decode("utf-8")
