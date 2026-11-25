"""Bounded, lossless parsing of authored YAML and JSON."""

from collections.abc import Iterator
from dataclasses import dataclass
import json
import re
from typing import Any, NoReturn

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .bounded_json import JSONBudgetExceeded, check_utf8_budget
from .strict_json import check_document_unicode, join_surrogate_escapes


class InvalidSerializedUnicode(yaml.YAMLError, ValueError):
    """An otherwise decoded scalar contains an unmatched surrogate."""


class InvalidSerializedDocument(yaml.YAMLError):
    """An authored document could not be safely constructed."""


class SerializedDocumentLimit(InvalidSerializedDocument):
    """The document exceeds the parser's resource limits."""


@dataclass(frozen=True, slots=True)
class YamlLimits:
    """Bound input, composer storage and the work implied by shared nodes."""

    max_source_bytes: int = 4 * 1024 * 1024
    max_nodes: int = 50_000
    max_depth: int = 64
    max_expanded_nodes: int = 100_000
    max_expanded_chars: int = 16 * 1024 * 1024


_AUTHORED_LIMITS = YamlLimits()
_YAML_MERGE = "tag:yaml.org,2002:merge"
_YAML_NUMBERS = {"tag:yaml.org,2002:int", "tag:yaml.org,2002:float"}
_MAX_YAML_NUMBER_CHARS = 16_384
_JSON_START = re.compile(
    r'[ \t\r\n]*(?:[{\["0-9]|-(?:[0-9]|Infinity)|true|false|null|NaN|Infinity)'
)
_JSON_TOKEN = re.compile(r'"|[\[\]{}]|[^\[\]{},:" \t\r\n]+')


def _bounded_source(value: str, limits: YamlLimits) -> None:
    try:
        check_utf8_budget(value, limits.max_source_bytes)
    except JSONBudgetExceeded:
        raise SerializedDocumentLimit("Document exceeds the text size limit") from None
    except UnicodeError:
        raise InvalidSerializedUnicode("Document contains malformed Unicode") from None


def _children(node: Node) -> Iterator[Node]:
    if isinstance(node, MappingNode):
        for key, value in node.value:
            yield key
            yield value
    elif isinstance(node, SequenceNode):
        yield from node.value


class _YamlExpansion:
    """Measure an alias graph without materializing its expanded tree."""

    def __init__(self, limits: YamlLimits, allow_recursive: bool) -> None:
        self._limits = limits
        self._allow_recursive = allow_recursive
        self._active: set[Node] = set()
        self._sizes: dict[Node, tuple[int, int, int]] = {}
        self._merge_active: set[MappingNode] = set()
        self._mapping_sizes: dict[MappingNode, int] = {}
        self._merge_work = 0

    def validate(self, root: Node) -> None:
        self._measure(root, 1)
        for node in self._sizes:
            if isinstance(node, MappingNode):
                self._flattened_entries(node)

    def _measure(self, node: Node, depth: int) -> tuple[int, int, int]:
        if depth > self._limits.max_depth:
            raise SerializedDocumentLimit("Document exceeds the nesting limit")
        if node in self._active:
            if self._allow_recursive:
                return 1, 0, 1
            raise InvalidSerializedDocument("Recursive YAML aliases are unsupported")
        if (size := self._sizes.get(node)) is not None:
            if depth + size[2] - 1 > self._limits.max_depth:
                raise SerializedDocumentLimit("Document exceeds the nesting limit")
            return size

        nodes, chars, height = 1, 0, 1
        if isinstance(node, ScalarNode):
            chars = len(node.value)
            if node.tag in _YAML_NUMBERS and chars > _MAX_YAML_NUMBER_CHARS:
                raise SerializedDocumentLimit(
                    "YAML numeric scalar exceeds the size limit"
                )
            if chars > self._limits.max_expanded_chars:
                raise SerializedDocumentLimit("YAML scalar exceeds the expansion limit")
        self._active.add(node)
        for child in _children(node):
            child_nodes, child_chars, child_height = self._measure(child, depth + 1)
            nodes += child_nodes
            chars += child_chars
            height = max(height, child_height + 1)
            if (
                nodes > self._limits.max_expanded_nodes
                or chars > self._limits.max_expanded_chars
            ):
                raise SerializedDocumentLimit("YAML alias expansion exceeds the limit")
        self._active.remove(node)
        self._sizes[node] = nodes, chars, height
        return nodes, chars, height

    def _charge_merge_work(self, count: int) -> None:
        self._merge_work += count
        if self._merge_work > self._limits.max_expanded_nodes:
            raise SerializedDocumentLimit("YAML merge expansion exceeds the limit")

    def _flattened_entries(self, node: MappingNode) -> int:
        if node in self._merge_active:
            raise InvalidSerializedDocument("Recursive YAML merges are unsupported")
        if len(self._merge_active) >= self._limits.max_depth:
            raise SerializedDocumentLimit("YAML merge exceeds the nesting limit")
        if (size := self._mapping_sizes.get(node)) is not None:
            return size
        self._merge_active.add(node)
        size = 0
        for index, (key, value) in enumerate(node.value):
            if key.tag != _YAML_MERGE:
                size += 1
                continue
            # SafeLoader deletes each merge key from the original list in place.
            # Count the entries moved as well as the materialized merged mappings.
            self._charge_merge_work(len(node.value) - index)
            sources = value.value if isinstance(value, SequenceNode) else (value,)
            for source in sources:
                if not isinstance(source, MappingNode):
                    raise InvalidSerializedDocument(
                        "YAML merge requires a mapping or a list of mappings"
                    )
                entries = self._flattened_entries(source)
                self._charge_merge_work(entries)
                size += entries
        self._merge_active.remove(node)
        self._mapping_sizes[node] = size
        return size


class _BoundedSafeLoader(yaml.SafeLoader):
    def __init__(
        self, stream: str, limits: YamlLimits, *, allow_recursive: bool
    ) -> None:
        super().__init__(stream)
        self._limits = limits
        self._allow_recursive = allow_recursive
        self._composed_nodes = 0
        self._composition_depth = 0

    def compose_node(self, parent: Node | None, index: Any) -> Node | None:
        self._composed_nodes += 1
        if self._composed_nodes > self._limits.max_nodes:
            raise SerializedDocumentLimit("Document exceeds the node limit")
        if self._composition_depth >= self._limits.max_depth:
            raise SerializedDocumentLimit("Document exceeds the nesting limit")
        self._composition_depth += 1
        try:
            return super().compose_node(parent, index)
        finally:
            self._composition_depth -= 1

    def get_checked_node(self) -> Node | None:
        node = self.get_single_node()
        if node is not None:
            _YamlExpansion(self._limits, self._allow_recursive).validate(node)
        return node

    def construct_scalar(self, node: ScalarNode | MappingNode) -> str:
        value = super().construct_scalar(node)
        try:
            return join_surrogate_escapes(value)
        except ValueError as err:
            raise InvalidSerializedUnicode(str(err)) from None


def _load_yaml(
    value: str, limits: YamlLimits, *, allow_recursive: bool, construct: bool = True
) -> Any:
    loader = _BoundedSafeLoader(value, limits, allow_recursive=allow_recursive)
    try:
        try:
            node = loader.get_checked_node()
        except ValueError:
            # PyYAML's scanner calls chr() when decoding a long Unicode escape.
            raise InvalidSerializedUnicode(
                "Document contains malformed Unicode"
            ) from None
        if node is None or not construct:
            return None
        try:
            return loader.construct_document(node)
        except yaml.YAMLError:
            raise
        except (
            AttributeError,
            IndexError,
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
        ):
            # Invalid calendar dates and malformed explicit safe tags may raise
            # ordinary Python exceptions instead of a marked PyYAML error.
            raise InvalidSerializedDocument("Invalid YAML scalar or value") from None
    except RecursionError:
        raise SerializedDocumentLimit("Document exceeds the nesting limit") from None
    finally:
        loader.dispose()


def load_unicode_yaml(value: str, *, limits: YamlLimits | None = None) -> Any:
    """Decode bounded YAML whose values are safe to traverse and use as UTF-8."""
    limits = _AUTHORED_LIMITS if limits is None else limits
    _bounded_source(value, limits)
    return _load_yaml(value, limits, allow_recursive=False)


def validate_yaml_structure(value: str, *, limits: YamlLimits | None = None) -> None:
    """Check generated YAML against reader limits before publishing it."""
    limits = _AUTHORED_LIMITS if limits is None else limits
    _bounded_source(value, limits)
    _load_yaml(value, limits, allow_recursive=False, construct=False)


def cacheable_authored_config(value: str | None) -> bool:
    """Reserve multi-entry caches for small configuration and canonical keys."""
    if value is None:
        return True
    return (
        len(value) <= 32 * 1024
        and value.count("\n") <= 512
        and value.count(",") <= 512
        # An alias or merge can expand a short source to the full graph budget.
        and ("&" not in value or "*" not in value)
    )


def _check_json_shape(value: str, limits: YamlLimits) -> None:
    """Count the tokens of JSON before its decoder constructs containers."""
    position = depth = nodes = 0
    decode_string = json.JSONDecoder().raw_decode
    while (match := _JSON_TOKEN.search(value, position)) is not None:
        token = value[match.start()]
        position = match.end()
        if token in "]}":
            depth -= 1
            continue
        nodes += 1
        if nodes > limits.max_nodes:
            raise SerializedDocumentLimit("Document exceeds the node limit")
        if depth >= limits.max_depth:
            raise SerializedDocumentLimit("Document exceeds the nesting limit")
        if token in "[{":
            depth += 1
        elif token == '"':
            # The source cap bounds the allocation of any one decoded token.
            _, position = decode_string(value, match.start())


def _reject_json_constant(token: str) -> NoReturn:
    """Allow the bounded YAML parser to interpret nonstandard JSON constants."""
    raise json.JSONDecodeError("Nonstandard JSON constant", token, 0)


def _load_yaml_or_json(value: str, *, allow_recursive: bool) -> Any:
    limits = _AUTHORED_LIMITS
    _bounded_source(value, limits)
    # JSON exponent floats such as 1e-06 must retain their JSON scalar types.
    if _JSON_START.match(value):
        try:
            _check_json_shape(value, limits)
            decoded = json.loads(value, parse_constant=_reject_json_constant)
        except json.JSONDecodeError, SerializedDocumentLimit:
            # Flow YAML may resemble JSON while using different quoting or
            # token boundaries. Its composer independently enforces these limits.
            pass
        except OverflowError, RecursionError, ValueError:
            raise InvalidSerializedDocument("Invalid JSON scalar or value") from None
        else:
            try:
                check_document_unicode(decoded)
            except ValueError as err:
                raise InvalidSerializedUnicode(str(err)) from None
            return decoded
    return _load_yaml(value, limits, allow_recursive=allow_recursive)


def load_authored_config(value: str) -> Any:
    """Decode YAML or JSON configuration for consumers that traverse a tree."""
    return _load_yaml_or_json(value, allow_recursive=False)


def load_yaml_or_json(value: str) -> Any:
    """Decode bounded data for lossless YAML serialization and identity checks."""
    return _load_yaml_or_json(value, allow_recursive=True)


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
