"""Explicit snapshot inheritance is validated before request-time lookup."""

from __future__ import annotations

from copy import deepcopy

import pytest

from custom_components.extended_openai_conversation_responses import model_catalog


@pytest.fixture(autouse=True)
def restore_catalog():
    model_catalog.activate_catalog(None)
    yield
    model_catalog.activate_catalog(None)


def candidate() -> dict:
    value = deepcopy(model_catalog.BUNDLED_CATALOG)
    value["catalog_version"] += 1
    return value


def test_bundled_snapshot_inherits_parent_and_exact_override_wins(monkeypatch):
    inherited = model_catalog.model_metadata("gpt-4o-2024-08-06")
    parent = model_catalog.model_metadata("gpt-4o")
    assert inherited["kind"] == "snapshot"
    assert inherited["alias_of"] == "gpt-4o"
    assert inherited["limits"] == parent["limits"]

    updated = candidate()
    snapshot = next(
        item for item in updated["models"] if item["id"] == "gpt-4o-2024-08-06"
    )
    snapshot["limits"] = {"max_output_tokens": 8192}
    prepared = model_catalog.validate_catalog(updated)
    model_catalog.activate_catalog(prepared)

    def unexpected_resolution(*_args):
        raise AssertionError("snapshot inheritance ran on a live lookup")

    monkeypatch.setattr(model_catalog, "_merge_snapshot", unexpected_resolution)
    exact = model_catalog.model_metadata("gpt-4o-2024-08-06")
    assert exact["limits"] == {"context_tokens": 128000, "max_output_tokens": 8192}
    assert model_catalog.model_metadata("gpt-4o")["limits"] == parent["limits"]
    exact["limits"]["max_output_tokens"] = 1
    assert (
        model_catalog.model_metadata("gpt-4o-2024-08-06")["limits"]["max_output_tokens"]
        == 8192
    )


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda item: item.update(alias_of="missing-parent"), "parent is missing"),
        (lambda item: item.update(limits={"invalid": 100}), "Unexpected or missing"),
        (lambda item: item.update(streaming="yes"), "Streaming capability"),
        (lambda item: item.update(kind="alias"), "Unexpected or missing"),
    ],
)
def test_invalid_snapshot_overrides_are_rejected(change, error):
    value = candidate()
    item = next(
        entry for entry in value["models"] if entry["id"] == "gpt-4o-2024-08-06"
    )
    change(item)
    with pytest.raises(ValueError, match=error):
        model_catalog.validate_catalog(value)


def test_snapshot_cycles_and_deprecated_parent_are_rejected():
    value = candidate()
    value["models"].extend(
        [
            {
                "id": "snapshot-a",
                "display_name": "A",
                "kind": "snapshot",
                "alias_of": "snapshot-b",
            },
            {
                "id": "snapshot-b",
                "display_name": "B",
                "kind": "snapshot",
                "alias_of": "snapshot-a",
            },
        ]
    )
    with pytest.raises(ValueError, match="cycle"):
        model_catalog.validate_catalog(value)

    value["models"] = value["models"][:-2]
    snapshot = next(
        item for item in value["models"] if item["id"] == "gpt-4o-2024-08-06"
    )
    snapshot.update(alias_of="gpt-4-turbo", status="current")
    with pytest.raises(ValueError, match="deprecated model"):
        model_catalog.validate_catalog(value)


def test_parent_capability_changes_flow_to_omitted_snapshot_on_activation():
    value = candidate()
    value["models"] = [
        item for item in value["models"] if item["id"] != "gpt-4o-2024-08-06"
    ]
    parent = next(item for item in value["models"] if item["id"] == "gpt-4o")
    parent["limits"]["context_tokens"] = 256000
    model_catalog.activate_catalog(model_catalog.validate_catalog(value))
    assert (
        model_catalog.model_metadata("gpt-4o-2024-08-06")["limits"]["context_tokens"]
        == 256000
    )


def test_transition_checks_effective_snapshot_capabilities():
    value = candidate()
    snapshot = next(
        item for item in value["models"] if item["id"] == "gpt-4o-2024-08-06"
    )
    snapshot["limits"] = {"max_output_tokens": 8192}
    with pytest.raises(ValueError, match="max output"):
        model_catalog.validate_catalog_transition(
            None, model_catalog.validate_catalog(value)
        )


def test_prepared_catalog_persists_only_raw_snapshot_fields():
    prepared = model_catalog.validate_catalog(candidate())
    stored = deepcopy(prepared)
    snapshot = next(
        item for item in stored["models"] if item["id"] == "gpt-4o-2024-08-06"
    )
    assert snapshot == {
        "id": "gpt-4o-2024-08-06",
        "display_name": "gpt-4o-2024-08-06",
        "kind": "snapshot",
        "alias_of": "gpt-4o",
    }
