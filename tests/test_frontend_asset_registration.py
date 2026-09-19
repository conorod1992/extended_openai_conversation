"""The declared asset registry is complete without runtime installation."""

from __future__ import annotations

import ast
from pathlib import Path

from custom_components.extended_openai_conversation_responses import management_ui
from custom_components.extended_openai_conversation_responses.const import DOMAIN
from custom_components.extended_openai_conversation_responses.management_loading_performance import (
    _asset_url,
    _static_paths,
)

COMPONENT = Path(management_ui.__file__).parent
REGISTRY_NAME = "MANAGEMENT_FRONTEND_MODULES"


def _declared_modules() -> tuple[str, ...]:
    """Read only the literal declaration, without executing any installers."""
    tree = ast.parse((COMPONENT / "management_ui.py").read_text(encoding="utf-8"))
    declarations = [
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == REGISTRY_NAME
    ]
    assert len(declarations) == 1
    return ast.literal_eval(declarations[0].value)


def test_declared_registry_matches_runtime_and_shipped_modules() -> None:
    """Cold import must not need side effects to complete the served file set."""
    modules = _declared_modules()
    assert isinstance(modules, tuple)
    assert len(modules) == len(set(modules))
    assert management_ui.MANAGEMENT_FRONTEND_MODULES == modules
    # Debug is registered separately. The retained standalone Memory panels are
    # deliberately not served by unified Management (see the retirement audit).
    excluded = {"debug-panel.js", "memory-panel.js", "memory-management-panel.js"}
    assert set(modules) == {
        file.name for file in (COMPONENT / "frontend").glob("*.js")
    } - excluded


def test_feature_modules_cannot_reassign_or_extend_registry() -> None:
    """Reject the assignment/setattr patterns formerly used by installers."""
    violations = []
    for file in COMPONENT.glob("*.py"):
        if file.name == "management_ui.py":
            continue
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Reads are allowed in setup functions; writes have no outside owner.
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name == REGISTRY_NAME and isinstance(node.ctx, (ast.Store, ast.Del)):
                    violations.append(f"{file.name}:{node.lineno}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in {"setattr", "delattr"}:
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        if node.args[1].value == REGISTRY_NAME:
                            violations.append(f"{file.name}:{node.lineno}")
    assert violations == [], violations


def test_all_declared_assets_keep_alias_and_versioned_cache_contracts() -> None:
    """Every declared module keeps both URL forms and their original cache policy."""
    frontend = COMPONENT / "frontend"
    modules = _declared_modules()
    paths = _static_paths(frontend, modules)
    assert len(paths) == len(modules) * 2
    by_url = {config.url_path: config for config in paths}
    assert len(by_url) == len(paths)
    for name in modules:
        assert (frontend / name).is_file()
        alias = by_url[f"/{DOMAIN}/{name}"]
        versioned = by_url[_asset_url(name)]
        assert alias.path == versioned.path == str(frontend / name)
        assert alias.cache_headers is False
        assert versioned.cache_headers is True
