"""Keep native-backed HA timestamp parsing behind the Unicode guard."""

import ast
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "extended_openai_conversation_responses"
)
NATIVE_PARSERS = {"homeassistant.util.dt.parse_datetime", "ciso8601.parse_datetime"}


def _native_parser_references(source: str) -> list[int]:
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                aliases[alias.asname or root] = alias.name if alias.asname else root
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    references = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Name, ast.Attribute)) or not isinstance(
            node.ctx, ast.Load
        ):
            continue
        head, dot, tail = ast.unparse(node).partition(".")
        if head in aliases and aliases[head] + dot + tail in NATIVE_PARSERS:
            references.append(node.lineno)
    return sorted(references)


def test_native_timestamp_parser_is_only_used_by_guard():
    violations = []
    for path in sorted(SOURCE.rglob("*.py")):
        if path == SOURCE / "record_timestamps.py":
            continue
        violations.extend(
            f"{path.relative_to(SOURCE).as_posix()}:{line}"
            for line in _native_parser_references(path.read_text(encoding="utf-8"))
        )
    assert not violations, "Use record_timestamps.parse_datetime at " + ", ".join(
        violations
    )


@pytest.mark.parametrize(
    "source",
    [
        "from homeassistant.util import dt as ha_dt\nha_dt.parse_datetime(value)",
        "from homeassistant.util.dt import parse_datetime as parse\nparser = parse",
        "import homeassistant.util.dt as ha_dt\nha_dt.parse_datetime(value)",
        "import homeassistant.util.dt\nhomeassistant.util.dt.parse_datetime(value)",
        "from homeassistant import util\nutil.dt.parse_datetime(value)",
        "import ciso8601 as native\nnative.parse_datetime(value)",
    ],
)
def test_guard_resolves_native_parser_import_aliases(source):
    assert _native_parser_references(source) == [2]


def test_guard_allows_eoai_and_standard_library_parsers():
    assert not _native_parser_references(
        "from .record_timestamps import parse_datetime\n"
        "from datetime import datetime\n"
        "parse_datetime(value)\n"
        "datetime.fromisoformat(value)\n"
    )
