"""Bounded supported schema witnesses constructed without production validators."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import random


@dataclass(frozen=True)
class SchemaCase:
    schema: dict
    arguments: dict
    expected: dict
    invalid: dict
    witness: str


def schema_cases(seed):
    rng = random.Random(seed)
    leaves = [
        (
            {"type": "integer", "minimum": -2, "maximum": 2},
            0.0,
            0,
            False,
            "integral-float",
        ),
        (
            {"type": "integer", "minimum": -2, "maximum": 2},
            "-2",
            -2,
            -3,
            "integer-boundary",
        ),
        (
            {"type": "number", "minimum": -1.5, "maximum": 1.5},
            "1.5",
            1.5,
            1.6,
            "fractional-boundary",
        ),
        (
            {"type": "number", "exclusiveMinimum": -1, "exclusiveMaximum": 1},
            0,
            0,
            1,
            "exclusive-boundary",
        ),
        ({"type": "boolean"}, "FaLsE", False, 0, "false-coercion"),
        (
            {"type": "string", "minLength": 0, "maxLength": 4},
            "",
            "",
            "large",
            "empty-string",
        ),
        ({"type": ["integer", "null"]}, None, None, 0.5, "nullable"),
        ({"type": ["integer", "string"]}, "0", "0", False, "union-preserves-string"),
        ({"type": ["integer", "null"]}, 2.0, 2.0, 2.5, "union-preserves-float"),
        (
            {"type": ["integer", "boolean"], "enum": [False]},
            False,
            False,
            0,
            "type-sensitive-enum",
        ),
        (
            {"type": ["integer", "boolean"], "const": 0},
            0,
            0,
            False,
            "type-sensitive-const",
        ),
        ({"type": "array", "items": {"type": "string"}}, "", [], [0], "empty-array"),
        (
            {
                "type": "object",
                "properties": {"optional": {"type": "null"}},
                "additionalProperties": False,
            },
            {},
            {},
            {"extra": None},
            "empty-object",
        ),
    ]
    rng.shuffle(leaves)
    for schema, raw, expected, invalid, witness in leaves:
        for depth in range(4):
            child, value, outcome, bad = (
                deepcopy(schema),
                deepcopy(raw),
                deepcopy(expected),
                deepcopy(invalid),
            )
            for level in range(depth):
                if rng.choice((False, True)):
                    child = {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": child,
                    }
                    value, outcome, bad = [value], [outcome], [bad]
                else:
                    key = f"nested_{level}"
                    child = {
                        "type": "object",
                        "properties": {
                            key: child,
                            "optional": {"type": ["null", "boolean"]},
                        },
                        "required": [key],
                        "additionalProperties": False,
                    }
                    value, outcome, bad = {key: value}, {key: outcome}, {key: bad}
                    if rng.choice((False, True)):
                        value["optional"] = outcome["optional"] = bad["optional"] = None
            root = {
                "type": "object",
                "properties": {"payload": child, "optional": {"type": "string"}},
                "required": ["payload"],
                "additionalProperties": False,
            }
            yield SchemaCase(
                root,
                {"payload": value},
                {"payload": outcome},
                {"payload": bad},
                f"{seed}:{witness}:depth={depth}",
            )


def assert_typed_value(actual, expected):
    """Python equality alone considers false/zero and integral floats equal."""
    if isinstance(expected, dict):
        assert isinstance(actual, Mapping), (actual, expected)
        assert set(actual) == set(expected)
        for key in expected:
            assert_typed_value(actual[key], expected[key])
    elif isinstance(expected, list):
        assert isinstance(actual, list), (actual, expected)
        for one, other in zip(actual, expected, strict=True):
            assert_typed_value(one, other)
    else:
        assert type(actual) is type(expected), (actual, expected)
        assert actual == expected, (actual, expected)


def script_cases(seed):
    rng = random.Random(seed)
    for index in range(12):
        branch = index % 2 == 0
        count = rng.randrange(1, 4)
        marker = f"native-{seed}-{index}"
        action = {
            "action": "script_probe.record",
            "data": {"marker": "{{ marker }}:{{ repeat.index }}"},
        }
        expected = (
            [f"{marker}:{number}" for number in range(1, count + 1)]
            if branch
            else [f"{marker}:default"]
        )
        sequence = [
            {"variables": {"marker": marker, "branch": branch, "iterations": count}},
            {"condition": "template", "value_template": "{{ iterations > 0 }}"},
            {
                "choose": [
                    {
                        "conditions": "{{ branch }}",
                        "sequence": [
                            {
                                "repeat": {
                                    "count": "{{ iterations }}",
                                    "sequence": [action],
                                }
                            }
                        ],
                    }
                ],
                "default": [
                    {
                        "action": "script_probe.record",
                        "data": {"marker": "{{ marker }}:default"},
                    }
                ],
            },
            {"wait_template": "{{ true }}", "timeout": 1, "continue_on_timeout": False},
            {"action": "script_probe.response", "response_variable": "business"},
            {"variables": {"_function_result": "{{ business }}"}},
            {"set_conversation_response": "{{ marker }}"},
        ]
        if index % 3 == 0:
            sequence += [
                {"stop": "intentional stop", "response_variable": "business"},
                {
                    "action": "script_probe.record",
                    "data": {"marker": "forbidden-after-stop"},
                },
            ]
        yield sequence, expected, {"count": 0, "flag": False, "empty": []}, marker


@dataclass(frozen=True)
class ExecutionCase:
    value: object
    failure: str
    continuation: bool
    guest: str
    unretained: bool
    reverse: bool

    @property
    def effects(self):
        """Independent literal execution model, never invoke production helpers."""
        if self.guest == "denied" or self.guest != "off" and self.failure != "none":
            return []
        inner = ["inner-last", "inner-first"] if self.reverse else ["inner-first", "inner-last"]
        if self.failure == "before":
            inner = []
        elif self.failure == "after":
            inner = inner[:1]
        return ["outer-first", *inner, *(["outer-last"] if self.failure == "none" or self.continuation else [])]


def execution_cases(seed, count=8):
    """Small reviewed witnesses, then seeded combinations for the nightly lane."""
    from itertools import product
    values = [{"count": 0, "flag": False}, [], "Grüße 東京", 0, 0.5, False, None]
    witnesses = [
        ExecutionCase(values[0], "none", False, "off", False, False),
        ExecutionCase(values[1], "before", False, "off", False, False),
        ExecutionCase(values[2], "after", True, "off", True, True),
        ExecutionCase(values[3], "none", False, "denied", False, False),
        ExecutionCase(values[4], "none", False, "allowed", False, True),
        ExecutionCase(values[5], "none", False, "off", True, False),
        ExecutionCase(values[6], "none", False, "off", False, True),
    ]
    combinations = [ExecutionCase(*fields) for fields in product(values, ("none", "before", "after"), (False, True), ("off", "allowed", "denied"), (False, True), (False, True))]
    random.Random(seed).shuffle(combinations)
    return (witnesses + combinations)[:count]
