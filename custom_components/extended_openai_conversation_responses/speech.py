"""Spoken-response post-processing that preserves the visual assistant text."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

from .const import (
    CONF_SPEECH_PROCESSING_ENABLED,
    CONF_SPEECH_REGEX_REPLACEMENTS,
    CONF_SPEECH_STRIP_MARKDOWN,
    CONF_SPEECH_STRIP_URLS,
    DEFAULT_SPEECH_PROCESSING_ENABLED,
    DEFAULT_SPEECH_REGEX_REPLACEMENTS,
    DEFAULT_SPEECH_STRIP_MARKDOWN,
    DEFAULT_SPEECH_STRIP_URLS,
)

_LOGGER = logging.getLogger(__name__)

_MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\((?:[^()\s]|\([^()]*\))+\)")
_BARE_URL = re.compile(r"(?<![\w@])https?://[^\s<>]+", re.IGNORECASE)
_HEADING_OR_QUOTE = re.compile(r"(?m)^\s{0,3}(?:#{1,6}|>)\s+")
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+")
_EMPHASIS = re.compile(r"(?<!\w)(?:\*\*|__|~~)|(?:\*\*|__|~~)(?!\w)")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_EMPTY_PARENS = re.compile(r"\(\s*\)")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([,.;:!?])")
_WHITESPACE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def _built_in_cleanup(text: str, *, markdown: bool, urls: bool) -> str:
    if markdown:
        text = _MARKDOWN_LINK.sub("", text)
        text = _HEADING_OR_QUOTE.sub("", text)
        text = _LIST_MARKER.sub("", text)
        text = _INLINE_CODE.sub(r"\1", text)
        text = _EMPHASIS.sub("", text)
    if urls:
        text = _BARE_URL.sub("", text)
    return text


def _final_whitespace_cleanup(text: str) -> str:
    text = _EMPTY_PARENS.sub("", text)
    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n", text)
    return text.strip()


def process_speech_text(original_text: str, agent_config: Mapping[str, Any]) -> str:
    """Return TTS-only text using a deterministic cleanup pipeline.

    Order: built-in Markdown/link cleanup, ordered custom replacements, final
    whitespace cleanup, and strip. Invalid persisted rules are skipped defensively.
    """
    if not agent_config.get(
        CONF_SPEECH_PROCESSING_ENABLED, DEFAULT_SPEECH_PROCESSING_ENABLED
    ):
        return original_text
    text = _built_in_cleanup(
        original_text,
        markdown=agent_config.get(
            CONF_SPEECH_STRIP_MARKDOWN, DEFAULT_SPEECH_STRIP_MARKDOWN
        ),
        urls=agent_config.get(CONF_SPEECH_STRIP_URLS, DEFAULT_SPEECH_STRIP_URLS),
    )
    rules = agent_config.get(
        CONF_SPEECH_REGEX_REPLACEMENTS, DEFAULT_SPEECH_REGEX_REPLACEMENTS
    )
    if isinstance(rules, list):
        for index, rule in enumerate(rules):
            try:
                if not isinstance(rule, dict):
                    raise TypeError("rule is not an object")
                text = re.sub(str(rule["pattern"]), str(rule["replacement"]), text)
            except KeyError, TypeError, re.error:
                _LOGGER.warning(
                    "Skipping invalid speech regex replacement %s", index, exc_info=True
                )
    return _final_whitespace_cleanup(text)
