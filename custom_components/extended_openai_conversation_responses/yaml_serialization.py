"""Safe, readable YAML that preserves scalar values through parsing."""

import json
from typing import Any

import yaml
from yaml.nodes import ScalarNode


def load_yaml_or_json(value: str) -> Any:
    """Decode JSON with JSON scalar semantics, otherwise use safe YAML.

    PyYAML's YAML 1.1 resolver treats JSON exponent floats such as 1e-06 as
    strings. Do not send valid JSON through that resolver or change global YAML
    behavior: authored YAML scalars, quoted strings and safe tags stay intact.
    """
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return yaml.safe_load(value)


class _LosslessSafeDumper(yaml.SafeDumper):
    """Keep the safe tag set without changing PyYAML's global representers."""


def _represent_string(dumper: yaml.SafeDumper, value: str) -> ScalarNode:
    # YAML normalizes physical line breaks, including NEL. Double quotes make
    # PyYAML emit its reversible \N/\L/\P escapes while ordinary Unicode remains
    # readable. This applies to mapping keys as well as values.
    style = '"' if any(char in value for char in "\x85\u2028\u2029") else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_LosslessSafeDumper.add_representer(str, _represent_string)


def dump_yaml(value: Any, *, sort_keys: bool = False) -> str:
    """Serialize safe YAML types without folding credential or template text."""
    return yaml.dump(
        value, Dumper=_LosslessSafeDumper, sort_keys=sort_keys, allow_unicode=True
    )
