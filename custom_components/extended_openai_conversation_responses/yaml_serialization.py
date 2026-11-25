"""Safe, readable YAML that preserves scalar values through parsing."""

from dataclasses import dataclass
import json
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from .strict_json import check_document_unicode, join_surrogate_escapes


class InvalidSerializedUnicode(yaml.YAMLError, ValueError):
    """An otherwise decoded scalar contains an unmatched surrogate."""


class _UnicodeSafeLoader(yaml.SafeLoader):
    def construct_scalar(self, node: ScalarNode | MappingNode) -> str:
        value = super().construct_scalar(node)
        try:
            return join_surrogate_escapes(value)
        except ValueError as err:
            raise InvalidSerializedUnicode(str(err)) from None


def load_unicode_yaml(value: str) -> Any:
    """Decode safe YAML without admitting malformed Unicode scalar values."""
    # The standard reader already rejects literal surrogates. Only Unicode
    # escapes can create surrogate code units later during scalar decoding.
    if "\\u" not in value and "\\U" not in value:
        return yaml.safe_load(value)
    return yaml.load(value, Loader=_UnicodeSafeLoader)


def load_yaml_or_json(value: str) -> Any:
    """Decode JSON with JSON scalar semantics, otherwise use safe YAML.

    PyYAML's YAML 1.1 resolver treats JSON exponent floats such as 1e-06 as
    strings. Do not send valid JSON through that resolver or change global YAML
    behavior: authored YAML scalars, quoted strings and safe tags stay intact.
    """
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return load_unicode_yaml(value)
    try:
        check_document_unicode(decoded)
    except ValueError as err:
        raise InvalidSerializedUnicode(str(err)) from None
    return decoded


class _LosslessSafeDumper(yaml.SafeDumper):
    """Keep the safe tag set without changing PyYAML's global representers."""


@dataclass(frozen=True, slots=True)
class SecretReference:
    """An unresolved Home Assistant YAML secret reference."""

    key: str


def _represent_secret(
    dumper: _LosslessSafeDumper, value: SecretReference
) -> ScalarNode:
    return dumper.represent_scalar("!secret", value.key, style='"')


def _represent_string(dumper: yaml.SafeDumper, value: str) -> ScalarNode:
    # YAML normalizes physical line breaks, including NEL. Double quotes make
    # PyYAML emit its reversible \N/\L/\P escapes while ordinary Unicode remains
    # readable. This applies to mapping keys as well as values.
    style = '"' if any(char in value for char in "\x85\u2028\u2029") else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


@dataclass(frozen=True, slots=True)
class _YamlPair:
    key: Any
    value: Any


def is_yaml_pair_sequence(value: list[Any]) -> bool:
    """Recognize the shape SafeLoader produces for !!pairs and !!omap."""
    return (
        bool(value)
        and isinstance(value[0], tuple)
        and all(isinstance(item, tuple) and len(item) == 2 for item in value)
    )


def _represent_pair(dumper: yaml.SafeDumper, pair: _YamlPair) -> MappingNode:
    # YAML pairs allow unhashable keys. Emit the single-entry mapping node
    # directly so serializing it does not require a Python dictionary.
    return MappingNode(
        "tag:yaml.org,2002:map",
        [(dumper.represent_data(pair.key), dumper.represent_data(pair.value))],
    )


def _represent_list(dumper: yaml.SafeDumper, value: list[Any]) -> SequenceNode:
    if is_yaml_pair_sequence(value):
        return dumper.represent_sequence(
            "tag:yaml.org,2002:pairs", [_YamlPair(key, item) for key, item in value]
        )
    return dumper.represent_list(value)


_LosslessSafeDumper.add_representer(str, _represent_string)
_LosslessSafeDumper.add_representer(SecretReference, _represent_secret)
_LosslessSafeDumper.add_representer(_YamlPair, _represent_pair)
_LosslessSafeDumper.add_representer(list, _represent_list)


class _AuthoredConfigurationDumper(_LosslessSafeDumper):
    """Preserve mapping order and give unordered YAML sets a stable encoding."""


def _represent_ordered_set(dumper: yaml.SafeDumper, value: set[Any]) -> MappingNode:
    ordered = sorted(value, key=dump_yaml)
    return dumper.represent_mapping(
        "tag:yaml.org,2002:set", ((item, None) for item in ordered)
    )


_AuthoredConfigurationDumper.add_representer(set, _represent_ordered_set)


def dump_yaml(
    value: Any,
    *,
    sort_keys: bool = False,
    flow_style: bool = False,
    deterministic_sets: bool = False,
) -> str:
    """Serialize safe YAML types without folding credential or template text."""
    return yaml.dump(
        value,
        Dumper=_AuthoredConfigurationDumper
        if deterministic_sets
        else _LosslessSafeDumper,
        sort_keys=sort_keys,
        allow_unicode=True,
        default_flow_style=flow_style,
    )
