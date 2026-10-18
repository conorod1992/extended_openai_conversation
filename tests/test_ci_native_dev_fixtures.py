"""Exact Core fixtures must replace stable helpers without changing test semantics."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from ci import run_ha_dev_pytest


def test_native_fixture_bootstrap_preserves_module_identity_and_test_arguments(
    monkeypatch, tmp_path
):
    paths = SimpleNamespace()
    native = ModuleType("pytest_homeassistant")
    native._paths = paths
    modules = {
        "pytest_homeassistant": native,
        "pytest_homeassistant.common": ModuleType("native_common"),
        "pytest_homeassistant.components.recorder.common": ModuleType(
            "native_recorder"
        ),
    }
    monkeypatch.setitem(sys.modules, "pytest_homeassistant", native)
    for name in modules:
        monkeypatch.setitem(
            sys.modules,
            name.replace(
                "pytest_homeassistant", "pytest_homeassistant_custom_component"
            ),
            None,
        )
    monkeypatch.setattr(
        run_ha_dev_pytest.importlib, "import_module", modules.__getitem__
    )
    invoked = []
    monkeypatch.setattr(pytest, "main", lambda args: invoked.append(args) or 7)
    args = ["tests_real_ha/test_public_version_journeys.py", "--stress-seed=12"]
    assert run_ha_dev_pytest.run_pytest(tmp_path, args) == 7
    assert tmp_path / "tests" == paths.TESTS_DIR
    assert invoked == [
        ["-p", "no:homeassistant", "-p", "pytest_homeassistant.plugins", *args]
    ]
    for name, module in modules.items():
        assert (
            sys.modules[
                name.replace(
                    "pytest_homeassistant", "pytest_homeassistant_custom_component"
                )
            ]
            is module
        )


def test_native_fixture_install_pins_core_and_preserves_child_failure(monkeypatch):
    sha = "e" * 40
    extracted = []
    commands = []
    monkeypatch.setattr(
        sys, "argv", ["runner", "--core-sha", sha, "tests_real_ha", "-q"]
    )
    monkeypatch.setattr(run_ha_dev_pytest, "version", lambda _: "2026.11.0.dev0")

    def extract(repository, commit, root):
        extracted.append((repository, commit, root))
        (root / "tests").mkdir(parents=True)

    def run(args, **kwargs):
        commands.append((args, kwargs))
        assert extracted[0][2].exists()
        return SimpleNamespace(returncode=5)

    monkeypatch.setattr(run_ha_dev_pytest, "extract_source", extract)
    monkeypatch.setattr(run_ha_dev_pytest.subprocess, "run", run)
    assert run_ha_dev_pytest.main() == 5
    assert extracted[0][:2] == ("home-assistant/core", sha)
    assert commands[0][0][1:4] == ["-m", "pip", "install"]
    assert commands[0][0][-1] == "homeassistant==2026.11.0.dev0"
    assert commands[0][1] == {"check": True}
    assert commands[1][0][-2:] == ["tests_real_ha", "-q"]
    assert commands[1][1] == {"check": False}
    assert not extracted[0][2].exists()


def test_native_fixture_install_rejects_moving_branch_before_network(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runner", "--core-sha", "dev"])
    monkeypatch.setattr(
        run_ha_dev_pytest, "extract_source", lambda *_: pytest.fail("network")
    )
    with pytest.raises(ValueError, match="40-character"):
        run_ha_dev_pytest.main()
