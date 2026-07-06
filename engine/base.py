from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncGenerator


@dataclass
class ModelRoute:
    """Maps an OpenAI model name to a provider and its internal identifier."""
    provider: str
    internal_name: str = ""


class WebProvider(ABC):
    """Abstract base for a cookie-based web API provider.

    Each subclass implements the web-specific protocol for one service
    (DeepSeek, Claude, ChatGPT) and yields OpenAI-format chunk dicts.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def routes(self) -> list[ModelRoute]:
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        *,
        model: str = "",
        stream: bool = True,
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Yield OpenAI-format chunk dicts.

        For streaming (stream=True), each yield is one chunk:
          {"id": ..., "object": "chat.completion.chunk",
           "created": ..., "model": ...,
           "choices": [{"index": 0, "delta": {...}, "finish_reason": None}]}

        For non-streaming (stream=False), yield exactly one item with the full
        response, then stop.

        *tools* — OpenAI-format tool definitions (list of function schemas).
        Web APIs that don't support ``tools`` natively fall back to injecting
        them as text via ToolAwareStrategy.

        Raises web.HTTPException subclasses for user-facing errors.
        """
        ...
        yield  # pragma: no cover
