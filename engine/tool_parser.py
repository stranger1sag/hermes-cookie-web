"""Tool-call parser for text-injected tool invocations.

When a web API doesn't natively support ``tools`` (function calling),
the proxy injects tool definitions as text in the prompt and tells the
model to output ``<tool_call>{"name":"...","args":{...}}</tool_call>``
markers.  This module extracts those markers from the response text.
"""

from __future__ import annotations

import json
import re
import time
from typing import Iterator

_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def iter_tool_calls(text: str) -> Iterator[dict]:
    """Yield tool-call dicts found in *text*.

    Each yielded dict has the OpenAI tool-call shape::

        {
            "id": "call_<timestamp>_<n>",
            "type": "function",
            "function": {
                "name": "<function-name>",
                "arguments": "<json-string>",
            },
        }
    """
    for idx, match in enumerate(_TOOL_CALL_RE.finditer(text)):
        raw = match.group(1).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue

        name = parsed.get("name", "") or ""
        args = parsed.get("args", parsed.get("arguments", {}))
        if not name:
            continue

        yield {
            "id": f"call_{int(time.time() * 1000)}_{idx}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }


def split_text_and_tool_calls(text: str) -> tuple[str, list[dict]]:
    """Split *text* into clean text and a list of tool-call dicts.

    Returns ``(text_without_markers, [tool_call_dict, ...])``.
    """
    tool_calls = list(iter_tool_calls(text))
    clean = _TOOL_CALL_RE.sub("", text).strip()
    return clean, tool_calls


def format_tools_for_prompt(tools: list[dict]) -> str:
    """Convert OpenAI-format *tools* into a human-readable text block.

    The output is a bullet-list of tool names, descriptions, and
    parameter schemas, suitable for injection into a text prompt.
    """
    if not tools:
        return ""

    lines = [
        "## 可用工具",
        "你可以使用以下工具来协助完成用户的任务。当你需要使用工具时，必须严格按照下面的格式输出工具调用。",
        "",
    ]
    for t in tools:
        fn = t.get("function", t)
        name = fn.get("name", "?")
        desc = fn.get("description", "")
        params = fn.get("parameters", {}).get("properties", {})

        lines.append(f"### {name}")
        if desc:
            lines.append(f"描述：{desc}")
        if params:
            lines.append("参数：")
            for pname, pmeta in params.items():
                ptype = pmeta.get("type", "any")
                pdesc = pmeta.get("description", "")
                required = "必填" if pname in fn.get("parameters", {}).get("required", []) else "可选"
                lines.append(f"  - {pname} ({ptype}, {required})：{pdesc}")
        lines.append("")

    lines.extend([
        "## 工具调用规则（重要）",
        "当用户请求的操作需要使用工具时，你必须输出工具调用，而不是告诉用户「你可以用XX工具」。",
        "你必须实际调用工具来完成任务，不能仅仅描述应该如何操作。",
        "",
        "工具调用格式（必须严格遵循）：",
        "<tool_call>{\"name\":\"工具名称\",\"args\":{\"参数名\":\"参数值\"}}</tool_call>",
        "",
        "示例：",
        "<tool_call>{\"name\":\"execute_code\",\"args\":{\"command\":\"ls -la\"}}</tool_call>",
        "",
        "多条工具调用可以连续输出。工具调用的结果将在后续消息中提供给你。",
        "请根据工具返回的结果继续回答用户的问题。",
        "",
    ])
    return "\n".join(lines)
