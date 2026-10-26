"""Review tripwires supplement artifact/log properties; they are not a proof."""

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "custom_components/extended_openai_conversation_responses"
INVENTORY = REPO / "docs/development/credential-boundaries.json"
PARSERS = {
    "yaml.safe_load",
    "yaml.safe_load_all",
    "yaml.load",
    "yaml.load_all",
    "yaml.full_load",
    "json.loads",
    "json.load",
    "orjson.loads",
}
LOG_METHODS = {"warning", "error", "exception", "debug", "info", "critical", "log"}


class BoundaryVisitor(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.stack = []
        self.aliases = {}
        self.parsers = {}
        self.exports = set()

    def visit_Import(self, node):
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = alias.name

    def visit_ImportFrom(self, node):
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        if "export" in node.name or node.name in {
            "finalize_backup_snapshot",
            "_build_archive_file",
            "_redacted_document",
            "encode_document",
            "encode_field",
        }:
            self.exports.add(self.path + ":" + ".".join(self.stack))
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node):
        name = ast.unparse(node.func)
        head, _, tail = name.partition(".")
        name = self.aliases.get(head, head) + ("." + tail if tail else "")
        if name in PARSERS:
            key = self.path + ":" + ".".join(self.stack) + ":" + name
            self.parsers[key] = self.parsers.get(key, 0) + 1
        self.generic_visit(node)


def discover_boundaries():
    parsers, exports = {}, set()
    for path in sorted(SOURCE.rglob("*.py")):
        visitor = BoundaryVisitor(path.relative_to(SOURCE).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf8")))
        parsers.update(visitor.parsers)
        exports.update(visitor.exports)
    return parsers, exports


def preformatted_errors(tree):
    """Errors may be passed to the record boundary, never stringified first.

    This intentionally flags simple derived assignments as well as direct
    f-strings/formatting. It cannot prove arbitrary interprocedural dataflow.
    """
    violations = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler) or not handler.name:
            continue
        derived = {handler.name}

        def private(node, derived=derived):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "failure_diagnostic"
            ):
                return False
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "__name__"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "type"
            ):
                return False
            return (isinstance(node, ast.Name) and node.id in derived) or any(
                private(child) for child in ast.iter_child_nodes(node)
            )

        changed = True
        while changed:
            before = len(derived)
            for node in ast.walk(handler):
                if (
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and node.value is not None
                    and private(node.value)
                ):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            derived.add(target.id)
                        elif isinstance(target, (ast.Tuple, ast.List)):
                            derived.update(
                                part.id
                                for part in ast.walk(target)
                                if isinstance(part, ast.Name)
                            )
            changed = len(derived) != before
        for call in ast.walk(handler):
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Attribute)
                or call.func.attr not in LOG_METHODS
            ):
                continue
            for argument in call.args:
                # Direct exception objects are projected safely by the factory.
                if private(argument) and not (
                    isinstance(argument, ast.Name) and argument.id == handler.name
                ):
                    violations.append(call.lineno)
            if any(
                private(keyword.value)
                for keyword in call.keywords
                if keyword.arg == "extra"
            ):
                violations.append(call.lineno)
    return violations


def test_all_parser_and_export_owners_have_reviewed_classification():
    inventory = json.loads(INVENTORY.read_text(encoding="utf8"))
    parsers, exports = discover_boundaries()
    assert parsers == {
        owner: value["calls"] for owner, value in inventory["parsers"].items()
    }, (
        "Review and classify new/moved parser boundaries; do not blindly regenerate the inventory."
    )
    assert exports == inventory["exports"].keys(), (
        "Review new/moved export writers and their public/private semantics."
    )
    for entries in (inventory["parsers"], inventory["exports"]):
        for classification in entries.values():
            assert classification["policy"] in inventory["policies"]
            assert (REPO / classification["regression"]).is_file()


def test_no_exception_prose_is_preformatted_before_operational_logging():
    violations = {}
    for path in SOURCE.rglob("*.py"):
        failures = preformatted_errors(ast.parse(path.read_text(encoding="utf8")))
        if failures:
            violations[path.relative_to(SOURCE).as_posix()] = failures
    assert not violations, "Pass exception objects or structured diagnostics: " + str(
        violations
    )


@pytest.mark.parametrize(
    "statement",
    [
        'logger.warning(f"failed {error}")',
        'logger.warning("failed %s" % error)',
        'logger.warning("failed {}".format(error))',
        "message = str(error)\n logger.warning(message)",
        'logger.warning("failed", extra={"error": error})',
    ],
)
def test_tripwire_detects_common_future_bypasses(statement):
    tree = ast.parse("try:\n pass\nexcept Exception as error:\n " + statement)
    assert preformatted_errors(tree)


def test_inventory_detects_aliased_new_parsers():
    visitor = BoundaryVisitor("future.py")
    visitor.visit(
        ast.parse(
            "from yaml import safe_load as parse\ndef read(value):\n return parse(value)"
        )
    )
    assert visitor.parsers == {"future.py:read:yaml.safe_load": 1}


@pytest.mark.parametrize(
    "module,owner",
    [
        ("management_ui.py", "_export_agent"),
        ("transfer.py", "finalize_setup_export"),
        ("request_rule_packs.py", "export_rule_pack"),
    ],
)
def test_current_sharing_writers_use_section_projection(module, owner):
    tree = ast.parse((SOURCE / module).read_text(encoding="utf8"))
    writer = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == owner
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "project_sharing_sections"
        for node in ast.walk(writer)
    )
