"""Streaming helpers for the narrator pass (v3 Phase 2).

Local "reasoning" models often inline their chain-of-thought in the text stream as
``<think>...</think>`` spans. The narrator is a separate prose-only call, so reasoning
should never be there — but as a model-agnostic safety net we strip those spans before
the text reaches the player (and the transcript). pydantic-ai already keeps provider
``reasoning_content`` in separate thinking parts, which ``stream_text`` excludes; this
handles the inline-tag case the framework can't see.
"""

from __future__ import annotations

_OPEN = "<think>"
_CLOSE = "</think>"


def _partial_suffix_len(buf: str, tag: str) -> int:
    """Length of the longest proper-prefix-of ``tag`` that ``buf`` ends with (0 if none).

    Used to hold back a few trailing chars that might be the start of a tag split across
    chunk boundaries. ``tag`` is lowercase; matching is case-insensitive.
    """
    lowered = buf.lower()
    for k in range(min(len(buf), len(tag) - 1), 0, -1):
        if lowered[-k:] == tag[:k]:
            return k
    return 0


class ReasoningStripper:
    """Incrementally strip ``<think>...</think>`` spans from a streamed text.

    Feed each raw delta to :meth:`feed`, which returns the player-visible portion so far
    (possibly empty while a tag is being assembled). Call :meth:`flush` once the stream
    ends to release any held-back tail. Robust to tags split across chunk boundaries and
    to case (``<Think>`` etc.).
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def feed(self, chunk: str) -> str:
        """Add a raw delta; return the visible text it yields."""
        self._buf += chunk
        out: list[str] = []
        while True:
            lowered = self._buf.lower()
            if self._in_think:
                end = lowered.find(_CLOSE)
                if end == -1:  # still inside reasoning — discard, keep only a partial close
                    keep = _partial_suffix_len(self._buf, _CLOSE)
                    self._buf = self._buf[len(self._buf) - keep :] if keep else ""
                    break
                self._buf = self._buf[end + len(_CLOSE) :]
                self._in_think = False
                continue
            start = lowered.find(_OPEN)
            if start == -1:  # no open tag — emit all but a possible partial open
                keep = _partial_suffix_len(self._buf, _OPEN)
                emit_to = len(self._buf) - keep
                out.append(self._buf[:emit_to])
                self._buf = self._buf[emit_to:]
                break
            out.append(self._buf[:start])
            self._buf = self._buf[start + len(_OPEN) :]
            self._in_think = True
        return "".join(out)

    def flush(self) -> str:
        """Release any buffered visible tail at end of stream (drops an unclosed think)."""
        tail = "" if self._in_think else self._buf
        self._buf = ""
        self._in_think = False
        return tail
