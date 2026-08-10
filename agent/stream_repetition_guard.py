"""Conservative detection for obviously degenerate streamed text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


class RepetitiveStreamError(RuntimeError):
    """Raised when a provider text stream collapses into repeated output."""


@dataclass(frozen=True)
class RepetitionMatch:
    line_length: int
    repetitions: int


class StreamRepetitionGuard:
    """Detect consecutive repetition while ignoring lists, tables and code."""

    _MAX_LINE_LENGTH = 16
    _PUNCTUATION_REPETITIONS = 16
    _TEXT_REPETITIONS = 32
    _MAX_PENDING_LENGTH = 8192
    _MAX_BLOCK_LENGTH = 8192
    _MIN_REPEATED_BLOCK_LENGTH = 160
    _MAX_BLOCK_PERIOD = 3
    _INTENT_REPETITIONS = 3
    _MIN_INTENT_LENGTH = 18
    _INTENT_SIMILARITY = 0.88
    _LIST_PREFIX = re.compile(r"(?:[-+*]|\d+[.)]|[A-Za-z][.)])\s+")
    _ACTION_PREFIX = re.compile(
        r"(?:(?:我(?:将|会|需要|应该|先|接下来|现在)|接下来|下一步|现在|首先|继续)"
        r"|(?:i(?:'ll|\s+will|\s+need\s+to|\s+should)|let\s+me|next(?:\s+step)?|first))",
        re.IGNORECASE,
    )
    _PUNCTUATION = frozenset(".!?…。、，！？:;~_-")
    _CODE_PUNCTUATION = frozenset("{}[]();,:<>/\\=+-*&#|'")

    def __init__(self) -> None:
        self._pending = ""
        self._skip_long_line = False
        self._in_fence = False
        self._candidate: str | None = None
        self._repetitions = 0
        self._block_lines: list[str] = []
        self._block_length = 0
        self._recent_blocks: list[str] = []
        self._intent_candidate: str | None = None
        self._intent_repetitions = 0

    def observe(self, text: str) -> RepetitionMatch | None:
        if not text:
            return None
        if self._skip_long_line:
            newline = text.find("\n")
            if newline < 0:
                return None
            self._skip_long_line = False
            text = text[newline + 1 :]
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            match = self._observe_line(line.rstrip("\r"))
            if match is not None:
                return match
        if len(self._pending) > self._MAX_PENDING_LENGTH:
            self._pending = ""
            self._skip_long_line = True
            self._reset_candidate()
        return None

    def _observe_line(self, line: str) -> RepetitionMatch | None:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            match = self._finish_block()
            self._in_fence = not self._in_fence
            self._reset_candidate()
            self._reset_intent()
            return match
        if self._in_fence:
            return None
        if not stripped:
            return self._finish_block()
        if self._block_length <= self._MAX_BLOCK_LENGTH:
            self._block_lines.append(stripped)
            self._block_length += len(stripped) + 1
        if not self._is_candidate(line, stripped):
            self._reset_candidate()
            return None
        if stripped == self._candidate:
            self._repetitions += 1
        else:
            self._candidate = stripped
            self._repetitions = 1
        punctuation_only = all(char in self._PUNCTUATION for char in stripped)
        threshold = (
            self._PUNCTUATION_REPETITIONS if punctuation_only else self._TEXT_REPETITIONS
        )
        if self._repetitions >= threshold:
            return RepetitionMatch(len(stripped), self._repetitions)
        return None

    def _finish_block(self) -> RepetitionMatch | None:
        if not self._block_lines:
            return None
        block = self._normalize_block("\n".join(self._block_lines))
        self._block_lines = []
        self._block_length = 0
        if not block:
            return None
        self._recent_blocks.append(block)
        history_limit = self._MAX_BLOCK_PERIOD * 2
        if len(self._recent_blocks) > history_limit:
            self._recent_blocks = self._recent_blocks[-history_limit:]
        for period in range(1, self._MAX_BLOCK_PERIOD + 1):
            if len(self._recent_blocks) < period * 2:
                continue
            previous = self._recent_blocks[-period * 2 : -period]
            current = self._recent_blocks[-period:]
            repeated_length = sum(len(part) for part in current)
            if previous == current and repeated_length >= self._MIN_REPEATED_BLOCK_LENGTH:
                return RepetitionMatch(repeated_length, 2)
        return self._observe_intent(block)

    def _observe_intent(self, block: str) -> RepetitionMatch | None:
        if len(block) < self._MIN_INTENT_LENGTH or not self._ACTION_PREFIX.search(block):
            self._reset_intent()
            return None
        if (
            self._intent_candidate is not None
            and self._similarity(self._intent_candidate, block) >= self._INTENT_SIMILARITY
        ):
            self._intent_repetitions += 1
        else:
            self._intent_candidate = block
            self._intent_repetitions = 1
        if self._intent_repetitions >= self._INTENT_REPETITIONS:
            return RepetitionMatch(len(block), self._intent_repetitions)
        return None

    @staticmethod
    def _normalize_block(block: str) -> str:
        return re.sub(r"\s+", " ", block).strip().casefold()

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        if max(len(left), len(right)) > min(len(left), len(right)) * 1.35:
            return 0.0
        return SequenceMatcher(None, left, right, autojunk=False).ratio()

    def _is_candidate(self, line: str, stripped: str) -> bool:
        if len(stripped) > self._MAX_LINE_LENGTH:
            return False
        if line.startswith("\t") or len(line) - len(line.lstrip(" ")) >= 4:
            return False
        if self._LIST_PREFIX.match(stripped) or stripped.startswith(">"):
            return False
        if "|" in stripped:
            return False
        if all(char in self._CODE_PUNCTUATION for char in stripped):
            return False
        return True

    def _reset_candidate(self) -> None:
        self._candidate = None
        self._repetitions = 0

    def _reset_intent(self) -> None:
        self._intent_candidate = None
        self._intent_repetitions = 0
