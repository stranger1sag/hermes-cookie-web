"""Generic SSE (Server-Sent Events) reader for aiohttp.

Reads line-delimited SSE from an aiohttp StreamReader and yields parsed
(event, data) tuples. Provider-specific handlers consume these events.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator, Optional

from aiohttp import ClientResponse


class SseEvent:
    """A single parsed SSE event."""

    __slots__ = ("event", "data", "raw")

    def __init__(self, event: str = "", data: Optional[dict] = None, raw: str = ""):
        self.event = event
        self.data = data or {}
        self.raw = raw

    def __repr__(self) -> str:
        return f"SseEvent(event={self.event!r}, data={self.data!r})"


async def iter_sse(resp: ClientResponse) -> AsyncGenerator[SseEvent, None]:
    """Read line-delimited SSE from *resp* and yield parsed events.

    Standard SSE format per line:
      - ``event: <type>`` — event type (applies to following data lines)
      - ``data: <payload>`` — JSON or text payload
      - ``id: <id>`` — event ID (ignored)
      - ``retry: <ms>`` — reconnection time (ignored)
      - `` (empty line)`` — delimiter between events (skipped)

    Each ``data:`` line is yielded as an ``SseEvent``. The *event* field
    carries the most recent ``event:`` type seen.
    """
    event_type = ""
    async for raw_line in _read_lines(resp):
        if not raw_line:
            continue

        if raw_line.startswith("event: "):
            event_type = raw_line[7:]
        elif raw_line.startswith("data: "):
            payload = raw_line[6:]
            ev = SseEvent(event=event_type, data=_try_parse(payload), raw=raw_line)
            event_type = ""  # event type is consumed with this data
            yield ev
        # else: ignore id:, retry:, comments (starting with :)
        #       (CommonMark SSE also supports : comment lines, which we skip)


async def _read_lines(resp: ClientResponse) -> AsyncGenerator[str, None]:
    """Read line by line from *resp* content, yielding stripped strings.

    This is a line-delimited reader that works correctly with aiohttp's
    chunked transfer encoding.  It yields one line at a time, stripping
    trailing ``\\r`` and ``\\n``.
    """
    while True:
        raw = await resp.content.readline()
        if not raw:
            break
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            continue  # skip empty delimiter lines between events
        yield line


def _try_parse(payload: str) -> dict:
    """Attempt JSON parse; return raw string as ``{"text": ...}`` on failure."""
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"text": payload}
