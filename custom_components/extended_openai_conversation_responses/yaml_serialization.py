"""Safe, readable YAML that preserves scalar values through parsing."""

from typing import Any

import yaml
from yaml.nodes import ScalarNode


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
