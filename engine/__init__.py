from .base import WebProvider, ModelRoute
from .deepseek import DeepSeekProvider
from .gemini import GeminiProvider
from .claude import ClaudeProvider
from .chatgpt import ChatGPTProvider
from .kimi import KimiProvider

PROVIDER_MAP: dict[str, type[WebProvider]] = {
    "deepseek": DeepSeekProvider,
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
    "chatgpt": ChatGPTProvider,
    "kimi": KimiProvider,
}

__all__ = [
    "WebProvider", "ModelRoute",
    "DeepSeekProvider", "GeminiProvider", "ClaudeProvider", "ChatGPTProvider", "KimiProvider",
    "PROVIDER_MAP",
]
