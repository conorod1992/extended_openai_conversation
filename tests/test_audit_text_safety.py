"""Regression checks for reserved captures and synchronous regex isolation."""

import time

from jinja2 import Environment
import pytest

from custom_components.extended_openai_conversation_responses import (
    regex_execution as regex,
    request_rules as rr,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    AgentConfigError,
    validate_speech_regex_replacements,
)


@pytest.mark.parametrize("name", ["context", "self", "request"])
def test_reserved_capture_uses_explicit_namespace(name):
    template = rr._migrate_slot_templates("{" + name + "}")
    assert (
        Environment().from_string(template).render(request={"slots": {name: "kitchen"}})
        == "kitchen"
    )
    assert rr._migrate_slot_templates({"value_from": "slot", "slot": name}) == template
    assert rr._resolve_guest_slot_templates(template, {name: "kitchen"}) == "kitchen"


def test_unknown_replacement_group_is_configuration_error():
    with pytest.raises(AgentConfigError, match="replacement"):
        validate_speech_regex_replacements(
            [{"pattern": "x", "replacement": r"\g<missing>"}]
        )


def test_sync_pathological_pattern_is_killed(monkeypatch):
    monkeypatch.setattr(regex, "_CONFIGURED_REGEX_TIMEOUT_SECONDS", 0.1)
    original = "a" * 1000 + "!"
    started = time.monotonic()
    assert (
        regex.apply_speech_replacements(
            original, [{"pattern": "(a+)+$", "replacement": "x"}]
        )
        == original
    )
    assert time.monotonic() - started < 3
    monkeypatch.setattr(regex, "_CONFIGURED_REGEX_TIMEOUT_SECONDS", 1)
    assert (
        regex.apply_speech_replacements("x", [{"pattern": "x", "replacement": "y"}])
        == "y"
    )


def test_sync_invalid_group_skips_rule_without_losing_valid_following_rule():
    assert (
        regex.apply_speech_replacements(
            "x",
            [
                {"pattern": "x", "replacement": r"\g<missing>"},
                {"pattern": "x", "replacement": "y"},
            ],
        )
        == "y"
    )


@pytest.mark.parametrize("replacement", [r"\g<missing>", r"\2"])
def test_zero_match_validation_still_rejects_invalid_references(replacement):
    with pytest.raises(AgentConfigError, match="replacement"):
        validate_speech_regex_replacements(
            [{"pattern": "(?P<answer>x)", "replacement": replacement}]
        )


def test_configuration_validation_never_runs_matching(monkeypatch):
    from custom_components.extended_openai_conversation_responses import agent_config

    compile_pattern = agent_config.re.compile

    class NonMatchingPattern:
        def __init__(self, pattern):
            self.compiled = compile_pattern(pattern)

        def sub(self, replacement, text, *, count):
            assert count < 0
            return self.compiled.sub(replacement, text, count=count)

    monkeypatch.setattr(agent_config.re, "compile", NonMatchingPattern)
    rules = [{"pattern": r"(?P<answer>a?){1000000}", "replacement": r"\g<answer>"}]
    assert validate_speech_regex_replacements(rules) == rules


@pytest.mark.parametrize(
    "replacement", [r"\1\1", "y" * 1000], ids=["backreferences", "literal"]
)
def test_worker_bounds_allocation_during_zero_width_replacement(replacement):
    import json
    import subprocess
    import sys

    payload = {
        "op": "sub_many",
        "text": "x" * 4000,
        "items": [{"index": 0, "pattern": "(?=(.*))", "replacement": replacement}],
        "max_output_chars": 16000,
    }
    script = (
        "import re, json, sys, tracemalloc\ntracemalloc.start()\n"
        + regex._REGEX_WORKER
        + "\nprint(tracemalloc.get_traced_memory()[1], file=sys.stderr)"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["text"] == payload["text"] and result["overflow"]
    assert int(completed.stderr.strip()) < 2_000_000


@pytest.mark.parametrize(
    "pattern,replacement,text",
    [
        ("x*", "-", "abxd"),
        (".*?", "-", "abc"),
        (r"(?=(.))", r"[\1]", "abc"),
        (r"(?P<word>\w+)", r"<\g<word>>", "hello world"),
        (r"(a)?b", r"\1!", "b ab"),
        ("never-matches", r"\n", "unchanged"),
    ],
)
def test_incremental_replacements_preserve_stdlib_semantics(pattern, replacement, text):
    import re

    assert regex.apply_speech_replacements(
        text, [{"pattern": pattern, "replacement": replacement}]
    ) == re.sub(pattern, replacement, text)
