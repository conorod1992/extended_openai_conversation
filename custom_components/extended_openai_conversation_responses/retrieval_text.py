"""Bounded lexical terms for continuous East Asian text."""

from collections.abc import Iterable, Sequence
import re
import unicodedata

# These English roots may double their final l before -ing or -ed. Do not
# apply this to genuine -ll roots (call, fill, spell, install, etc.).
_DOUBLED_L_BASES = frozenset(
    {
        "cancel",
        "channel",
        "compel",
        "control",
        "corral",
        "counsel",
        "dial",
        "dispel",
        "excel",
        "expel",
        "extol",
        "fuel",
        "grovel",
        "impel",
        "initial",
        "jewel",
        "label",
        "level",
        "libel",
        "marshal",
        "marvel",
        "model",
        "panel",
        "parcel",
        "patrol",
        "pencil",
        "propel",
        "pummel",
        "quarrel",
        "rebel",
        "relabel",
        "remodel",
        "repel",
        "revel",
        "rival",
        "shovel",
        "signal",
        "spiral",
        "squirrel",
        "surveil",
        "swivel",
        "total",
        "travel",
        "tunnel",
        "unravel",
    }
)

# The undoubled roots are real words, unlike those for pass/fuss/buzz.
_ING_AMBIGUOUS_DOUBLE_BASES = frozenset({"gass", "buss", "quizz"})


# Normalize only established single-l British spellings to the same token as
# their double-l counterparts. Plural -s is handled here too; no new fuzzy
# aliases or alternative retrieval terms are introduced.
_COMMON_L_SPELLING_ALIASES = {
    "distil": "distill",
    "enrol": "enroll",
    "enrolment": "enrollment",
    "fulfil": "fulfill",
    "fulfilment": "fulfillment",
}


# Regular -e verbs need their silent e back after -ed and -ing. This is an
# intentionally small, audited family: blindly appending e turns walked into
# "walke" and creates false matches in retrieval and upsert candidate ranking.
_SILENT_E_VERB_BASES = frozenset(
    {
        "bake",
        "care",
        "change",
        "close",
        "dance",
        "face",
        "hope",
        "like",
        "live",
        "love",
        "move",
        "name",
        "place",
        "save",
        "share",
        "size",
    }
)

# Plurals ending in -ies are normally y forms, but these retain their -ie.
_IE_PLURAL_BASES = frozenset(
    {"movie", "cookie", "brownie", "zombie", "smoothie", "selfie"}
)


def _stem(token: str) -> str:
    """Apply conservative deterministic English suffix normalization."""
    variant = token[:-1] if token.endswith("s") else token
    if canonical := _COMMON_L_SPELLING_ALIASES.get(variant):
        return canonical
    if token.endswith("ies") and token[:-1] in _IE_PLURAL_BASES:
        return token[:-1]
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 5 and token.endswith("ing"):
        root = token[:-3]
        if (
            len(root) > 3
            and root[-1] == root[-2]
            and (
                root in _ING_AMBIGUOUS_DOUBLE_BASES
                or (root.endswith("ll") and root[:-1] in _DOUBLED_L_BASES)
                or not root.endswith(("ll", "ss", "ff", "zz", "ee"))
            )
        ):
            # The double consonant is evidence of a non-silent-e base:
            # "hopping" is hop, not hope.
            return root[:-1]
        return f"{root}e" if f"{root}e" in _SILENT_E_VERB_BASES else root
    if len(token) > 4 and token.endswith("ed") and not token.endswith("eed"):
        root = token[:-2]
        if (
            len(root) > 3
            and root[-1] == root[-2]
            and (
                root in _ING_AMBIGUOUS_DOUBLE_BASES
                or (root.endswith("ll") and root[:-1] in _DOUBLED_L_BASES)
                or not root.endswith(("ll", "ss", "ff", "zz", "ee"))
            )
        ):
            # Preserve true doubled-consonant roots such as add and call;
            # a doubled ending also distinguishes hopped from hoped.
            return root[:-1]
        return f"{root}e" if f"{root}e" in _SILENT_E_VERB_BASES else root
    if token in {"quizzes", "buses", "gases"}:
        return {"quizzes": "quiz", "buses": "bus", "gases": "gas"}[token]
    if len(token) > 5 and token.endswith(
        ("sses", "ches", "shes", "xes", "zzes", "oes")
    ):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+")


def bounded_phrase(phrase: str, text: str) -> bool:
    """Match normalized phrases at word edges, allowing continuous CJK text.

    Inspect every occurrence: an embedded occurrence must not hide a later
    genuine phrase. Hyphens/apostrophes remain parts of technical/compound words.
    """
    if not phrase:
        return False

    def word_edge(char: str) -> bool:
        return bool(char) and (char.isalnum() or char in "_'-+#")

    start = text.find(phrase)
    while start >= 0:
        end = start + len(phrase)
        if (
            start == 0
            or _CJK_RUN.fullmatch(phrase[0])
            or not word_edge(text[start - 1])
        ) and (
            end == len(text)
            or _CJK_RUN.fullmatch(phrase[-1])
            or not word_edge(text[end])
        ):
            return True
        start = text.find(phrase, start + 1)
    return False


def ordered_term_phrase(phrase: list[str], terms: Sequence[str]) -> bool:
    """Match contiguous whole normalized terms without substring collisions."""
    size = len(phrase)
    return bool(size) and any(
        list(terms[start : start + size]) == phrase
        for start in range(len(terms) - size + 1)
    )


def cjk_terms(value: str) -> set[str]:
    """Index adjacent characters so embedded terms match without spaces."""
    terms: set[str] = set()
    for run in _CJK_RUN.findall(unicodedata.normalize("NFC", value).casefold()):
        terms.update(run)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


_LEXICAL_TOKEN = re.compile(r"\w+(?:[+#]+|(?:['-]\w+)*)", re.UNICODE)


def lexical_tokens(value: str) -> list[str]:
    """Preserve meaningful token punctuation before compound expansion."""
    normalized = unicodedata.normalize("NFC", value).casefold().replace("\u2019", "'")
    return _LEXICAL_TOKEN.findall(normalized)


def lexical_terms(value: str, *, include_spelling_aliases: bool = True) -> list[str]:
    """Keep short identifiers and index compounds alongside their components."""
    return lexical_terms_from_tokens(
        lexical_tokens(value), include_spelling_aliases=include_spelling_aliases
    )


# Established spellings only: arbitrary hyphen removal conflates distinct words.
_HYPHEN_SPELLING_ALIASES = {
    "wi-fi": "wifi",
    "wifi": "wi-fi",
    "e-mail": "email",
    "email": "e-mail",
    "set-up": "setup",
    "setup": "set-up",
}


def lexical_terms_from_tokens(
    tokens: Iterable[str], *, include_spelling_aliases: bool = True
) -> list[str]:
    """Expand already normalized lexical tokens without rescanning their text."""
    terms: list[str] = []
    for token in tokens:
        terms.append(token)
        stem = token[:-2] if token.endswith("'s") else token
        if stem != token:
            terms.append(stem)
        if include_spelling_aliases and (alias := _HYPHEN_SPELLING_ALIASES.get(stem)):
            terms.append(alias)
        if "-" in stem:
            terms.extend(part for part in stem.split("-") if part)
    return terms
