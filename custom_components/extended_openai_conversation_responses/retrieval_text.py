"""Bounded lexical terms for continuous East Asian text."""

import re
import unicodedata

_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+")


def cjk_terms(value: str) -> set[str]:
    """Index adjacent characters so embedded terms match without spaces."""
    terms: set[str] = set()
    for run in _CJK_RUN.findall(unicodedata.normalize("NFC", value).casefold()):
        terms.update(run)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


_LEXICAL_TOKEN = re.compile(r"\w+(?:[+#]+|(?:['-]\w+)*)", re.UNICODE)


def lexical_terms(value: str) -> list[str]:
    """Keep short identifiers and index compounds alongside their components."""
    normalized = unicodedata.normalize("NFC", value).casefold().replace("\u2019", "'")
    terms: list[str] = []
    for token in _LEXICAL_TOKEN.findall(normalized):
        terms.append(token)
        stem = token[:-2] if token.endswith("'s") else token
        if stem != token:
            terms.append(stem)
        if "-" in stem:
            terms.extend(part for part in stem.split("-") if part)
    return terms
