import json
import zipfile

import pytest

from ci.historical_runtime_identity import identity
from ci.install_ha_test_plugin import verify_wheel
from ci.resolve_ha_test_plugin import (
    CORE_SHA,
    GENERATED_HA_VERSION,
    GENERATED_PLUGIN_VERSION,
    GENERATOR_SHA,
    resolve_ha_test_plugin,
)


def test_release_day_bridge_yields_to_exact_published_fixtures():
    project = {"releases": {"0.13.371": [{"yanked": False}]}}
    assert (
        resolve_ha_test_plugin(
            GENERATED_HA_VERSION,
            project,
            lambda _: {"info": {"requires_dist": ["homeassistant==2026.10.0"]}},
        )
        == "0.13.371"
    )
    assert (
        resolve_ha_test_plugin(
            GENERATED_HA_VERSION,
            project,
            lambda _: {"info": {"requires_dist": ["homeassistant==2026.10.0b4"]}},
        )
        == GENERATED_PLUGIN_VERSION
    )


@pytest.mark.parametrize("drift", [None, "metadata", "fixtures", "provenance"])
def test_generated_fixtures_reject_beta_relabeling_or_wrong_source(tmp_path, drift):
    wheel = tmp_path / "fixtures.whl"
    ha_pin = "2026.10.0b4" if drift == "metadata" else GENERATED_HA_VERSION
    patch = "0b4" if drift == "fixtures" else "0"
    provenance = {
        "generator_sha": GENERATOR_SHA,
        "core_sha": "a" * 40 if drift == "provenance" else CORE_SHA,
        "homeassistant": GENERATED_HA_VERSION,
    }
    with zipfile.ZipFile(wheel, "w") as package:
        package.writestr(
            "fixtures.dist-info/METADATA",
            f"Version: {GENERATED_PLUGIN_VERSION}\nRequires-Dist: homeassistant=={ha_pin}\n",
        )
        package.writestr(
            "pytest_homeassistant_custom_component/const.py",
            f'MAJOR_VERSION: Final = 2026\nMINOR_VERSION: Final = 10\nPATCH_VERSION: Final = "{patch}"\n',
        )
        package.writestr(
            "pytest_homeassistant_custom_component/eoai_source_identity.json",
            json.dumps(provenance),
        )
    if drift:
        with pytest.raises(RuntimeError):
            verify_wheel(wheel)
    else:
        verify_wheel(wheel)


def test_ha_test_plugin_resolver_uses_newest_exact_ha_compatibility():
    project = {
        "releases": {
            "0.13.368": [{"yanked": False}],
            "0.13.367": [{"yanked": False}],
        }
    }
    metadata = {
        "0.13.368": ["homeassistant==2026.10.0b0"],
        "0.13.367": ["homeassistant==2026.9.4"],
    }

    selected = resolve_ha_test_plugin(
        "2026.9.4",
        project,
        lambda url: {
            "info": {
                "requires_dist": metadata[url.rsplit("/", 2)[-2]],
            }
        },
    )

    assert selected == "0.13.367"


def test_ha_test_plugin_resolver_fails_when_no_exact_compatibility_exists():
    with pytest.raises(RuntimeError, match="No published"):
        resolve_ha_test_plugin(
            "2026.8.0",
            {"releases": {"0.13.367": [{"yanked": False}]}},
            lambda _: {"info": {"requires_dist": ["homeassistant==2026.9.4"]}},
        )


def test_historical_runtime_identity_covers_release_dependencies_and_recipes(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "requirements": ["openai==2.7.1"],
                "dependencies": ["conversation"],
                "after_dependencies": ["http"],
                "version": "1.0.0",
            }
        ),
        encoding="utf-8",
    )
    recipe = tmp_path / "prepare.sh"
    recipe.write_text("pip install", encoding="utf-8")

    baseline = identity(manifest, "v1.0.0", "a" * 40, "2026.9.4", "ha-stable", [recipe])
    recipe.write_text("pip install --upgrade", encoding="utf-8")
    changed_recipe = identity(
        manifest, "v1.0.0", "a" * 40, "2026.9.4", "ha-stable", [recipe]
    )
    changed_source = identity(
        manifest, "v1.0.0", "b" * 40, "2026.9.4", "ha-stable", [recipe]
    )
    manifest.write_text(
        json.dumps(
            {"requirements": ["openai==2.8.0"], "dependencies": ["conversation"]}
        ),
        encoding="utf-8",
    )
    changed_requirement = identity(
        manifest, "v1.0.0", "a" * 40, "2026.9.4", "ha-stable", [recipe]
    )

    assert baseline["inputs_sha256"] != changed_recipe["inputs_sha256"]
    assert baseline["inputs_sha256"] != changed_source["inputs_sha256"]
    assert baseline["inputs_sha256"] != changed_requirement["inputs_sha256"]
    assert baseline["installed_python_packages"]


def test_historical_runtime_identity_covers_python_ha_base_and_release_tag(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"requirements": ["openai==2.7.1"]}', encoding="utf-8")
    recipe = tmp_path / "install.sh"
    recipe.write_text("install", encoding="utf-8")

    baseline = identity(
        manifest, "v1.0.0", "a" * 40, "2026.9.4", "image@sha256:one", [recipe]
    )
    changed_values = (
        identity(
            manifest, "v1.0.1", "a" * 40, "2026.9.4", "image@sha256:one", [recipe]
        ),
        identity(
            manifest, "v1.0.0", "a" * 40, "2026.9.5", "image@sha256:one", [recipe]
        ),
        identity(
            manifest, "v1.0.0", "a" * 40, "2026.9.4", "image@sha256:two", [recipe]
        ),
    )

    assert all(
        item["inputs_sha256"] != baseline["inputs_sha256"] for item in changed_values
    )
