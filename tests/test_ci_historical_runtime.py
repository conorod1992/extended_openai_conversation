import json
import zipfile

import pytest

from ci.historical_runtime_identity import identity
from ci.install_ha_test_plugin import verify_wheel
from ci.resolve_ha_test_plugin import GENERATED_FIXTURES, resolve_ha_test_plugin


@pytest.mark.parametrize("ha_version", list(GENERATED_FIXTURES))
def test_release_day_bridge_yields_to_exact_published_fixtures(ha_version):
    fixture = GENERATED_FIXTURES[ha_version]
    project = {"releases": {"0.13.371": [{"yanked": False}]}}
    assert (
        resolve_ha_test_plugin(
            ha_version,
            project,
            lambda _: {"info": {"requires_dist": [f"homeassistant=={ha_version}"]}},
        )
        == "0.13.371"
    )
    assert (
        resolve_ha_test_plugin(
            ha_version,
            project,
            lambda _: {"info": {"requires_dist": ["homeassistant==2026.10.0b4"]}},
        )
        == fixture.plugin_version
    )


@pytest.mark.parametrize("ha_version", list(GENERATED_FIXTURES))
@pytest.mark.parametrize(
    "drift", [None, "metadata", "fixtures", "provenance", "generator", "plugin"]
)
def test_generated_fixtures_reject_beta_relabeling_or_wrong_source(
    tmp_path, drift, ha_version
):
    fixture = GENERATED_FIXTURES[ha_version]
    wheel = tmp_path / "fixtures.whl"
    ha_pin = "2026.10.0b4" if drift == "metadata" else ha_version
    patch = "0b4" if drift == "fixtures" else ha_version.rsplit(".", 1)[-1]
    provenance = {
        "generator_sha": "b" * 40 if drift == "generator" else fixture.generator_sha,
        "core_sha": "a" * 40 if drift == "provenance" else fixture.core_sha,
        "homeassistant": ha_version,
    }
    with zipfile.ZipFile(wheel, "w") as package:
        package.writestr(
            "fixtures.dist-info/METADATA",
            f"Version: {'0.0.0' if drift == 'plugin' else fixture.plugin_version}\nRequires-Dist: homeassistant=={ha_pin}\n",
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
            verify_wheel(wheel, fixture)
    else:
        verify_wheel(wheel, fixture)


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


@pytest.mark.parametrize("ha_version", list(GENERATED_FIXTURES))
def test_generated_bridge_does_not_accept_another_release(monkeypatch, ha_version):
    from ci import install_ha_test_plugin as installer

    fixture = GENERATED_FIXTURES[ha_version]
    monkeypatch.setattr(
        installer.sys, "argv", ["installer", "2026.10.99", fixture.plugin_version]
    )
    with pytest.raises(SystemExit) as error:
        installer.main()
    assert error.value.code == 2


def test_release_day_bridge_does_not_guess_future_releases():
    with pytest.raises(RuntimeError, match="No published"):
        resolve_ha_test_plugin("2026.10.5", {"releases": {}})
