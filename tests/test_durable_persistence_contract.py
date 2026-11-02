"""Review guard for every direct HA Store construction and its durability promise."""

from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "extended_openai_conversation_responses"
CONTRACT = ROOT / "ci" / "durable_persistence_contract.json"


def _source_constructions(source: str) -> Counter[str]:
    tree = ast.parse(source)
    aliases = {
        imported.asname: imported.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for imported in node.names
        if imported.asname and imported.name.endswith("Store")
    }
    result: Counter[str] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Subscript):
            function = function.value
        if isinstance(function, ast.Name):
            name = aliases.get(function.id, function.id)
        elif isinstance(function, ast.Attribute):
            name = function.attr
        else:
            continue
        if name.endswith("Store"):
            result[name] += 1
    return result


def _store_constructions() -> Counter[tuple[str, str]]:
    result: Counter[tuple[str, str]] = Counter()
    for path in INTEGRATION.glob("*.py"):
        result.update({
            (path.name, name): count
            for name, count in _source_constructions(
                path.read_text(encoding="utf-8")
            ).items()
        })
    return result


def test_import_spelling_cannot_hide_a_new_persistence_writer() -> None:
    for source in (
        "from homeassistant.helpers.storage import Store as Disk\nDisk(hass, 1, 'key')",
        "from homeassistant.helpers import storage as disk\ndisk.Store[dict](hass, 1, 'key')",
        "from .strict_store import PropagatingWriteStore as Durable\nDurable(hass, 1, 'key')",
    ):
        # An unreviewed site in a new module must change the real inventory,
        # regardless of which supported import spelling the author chooses.
        actual = _store_constructions()
        actual.update({
            ("new_writer.py", name): count
            for name, count in _source_constructions(source).items()
        })
        assert actual != _store_constructions()
        assert sum(_source_constructions(source).values()) == 1


def test_every_store_construction_has_one_reviewed_durability_owner() -> None:
    records = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert len(records) == len({record["owner"] for record in records})
    assert {record["owner"] for record in records} >= {
        "persistent_memory",
        "temporary_memory",
        "knowledge",
        "request_rules",
        "conversation_archive",
        "usage_history",
        "guest_mode",
        "delayed_function_tools",
        "model_catalogue",
        "backup_restore_journal",
        "agent_configuration_and_subentries",
    }
    reviewed: Counter[tuple[str, str]] = Counter()
    for record in records:
        assert record["classification"] in {
            "transactional_durable",
            "best_effort_or_coalesced",
            "ha_owned",
            "ephemeral",
        }
        assert record["guarantee"]
        assert record["evidence"]
        for evidence in record["evidence"]:
            path, test_name = evidence.split("::", 1)
            assert f"def {test_name}(" in (ROOT / path).read_text(encoding="utf-8")
        for site in record["stores"]:
            key = site["module"], site["constructor"]
            reviewed[key] += site["count"]
            if record["classification"] == "transactional_durable":
                assert site["constructor"] != "Store" or record["owner"] == (
                    "backup_restore_journal"
                ), record["owner"]
    assert reviewed == _store_constructions()


def test_transactional_store_subclasses_propagate_writer_failures() -> None:
    for module, name in (
        ("memory.py", "MemoryStore"),
        ("temporary_memory.py", "TemporaryMemoryStore"),
        ("knowledge.py", "KnowledgeStore"),
        ("request_rules.py", "RequestRuleStore"),
        ("usage.py", "_UsageStore"),
    ):
        tree = ast.parse((INTEGRATION / module).read_text(encoding="utf-8"))
        declaration = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == name
        )
        assert any(
            isinstance(base, ast.Name) and base.id == "PropagatingWriteStore"
            for base in declaration.bases
        ), f"{module}:{name} must surface HA writer failures"
