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
