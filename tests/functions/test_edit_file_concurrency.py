"""Regression tests for edit_file read-modify-write concurrency."""

from __future__ import annotations

import asyncio

from custom_components.extended_openai_conversation_responses.functions import (
    EditFileFunction,
)
from custom_components.extended_openai_conversation_responses.functions import (
    file as file_module,
)
from tests.helpers import prepare_function_tool_from_yaml


async def test_concurrent_edits_to_same_file_are_serialized(
    hass, exposed_entities, llm_context, tmp_path, monkeypatch
) -> None:
    """Two Function instances must not both edit the same stale file snapshot."""
    workdir = tmp_path / "extended_openai_conversation_responses"
    target = workdir / "concurrent.txt"
    target.write_text("alpha beta")
    function_config = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)[
        "function"
    ]
    first = EditFileFunction()
    second = EditFileFunction()

    original_executor = hass.async_add_executor_job
    first_read_started = asyncio.Event()
    release_first_read = asyncio.Event()
    snapshot_reads = 0

    async def controlled_executor(function, *args):
        nonlocal snapshot_reads
        if function is file_module._read_text_bounded_snapshot:
            snapshot_reads += 1
            result = await original_executor(function, *args)
            if snapshot_reads == 1:
                first_read_started.set()
                await release_first_read.wait()
            return result
        return await original_executor(function, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", controlled_executor)

    first_task = asyncio.create_task(
        first.execute(
            hass,
            function_config,
            {
                "filename": "concurrent.txt",
                "old_text": "alpha",
                "new_text": "ALPHA",
            },
            llm_context,
            exposed_entities,
        )
    )
    await first_read_started.wait()

    second_task = asyncio.create_task(
        second.execute(
            hass,
            function_config,
            {
                "filename": "concurrent.txt",
                "old_text": "beta",
                "new_text": "BETA",
            },
            llm_context,
            exposed_entities,
        )
    )
    await asyncio.sleep(0)

    # Without the shared canonical-path lock, the second edit reaches its read
    # while the first is paused and both build replacements from "alpha beta".
    assert snapshot_reads == 1

    release_first_read.set()
    first_result, second_result = await asyncio.gather(first_task, second_task)

    assert first_result["success"] is True
    assert second_result["success"] is True
    assert target.read_text() == "ALPHA BETA"


async def test_external_change_before_replace_returns_conflict(
    hass, exposed_entities, llm_context, tmp_path, monkeypatch
) -> None:
    """An out-of-band write after the read must not be silently overwritten."""
    workdir = tmp_path / "extended_openai_conversation_responses"
    target = workdir / "external-change.txt"
    target.write_text("original target")
    function_config = prepare_function_tool_from_yaml("edit_file_example.yaml", 0)[
        "function"
    ]
    function = EditFileFunction()

    original_executor = hass.async_add_executor_job
    injected_change = False

    async def controlled_executor(callable_, *args):
        nonlocal injected_change
        if callable_ is file_module._atomic_replace_text_if_unchanged:
            injected_change = True
            target.write_text("external writer won")
        return await original_executor(callable_, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", controlled_executor)

    result = await function.execute(
        hass,
        function_config,
        {
            "filename": "external-change.txt",
            "old_text": "target",
            "new_text": "replacement",
        },
        llm_context,
        exposed_entities,
    )

    assert injected_change is True
    assert "error" in result
    assert "changed since it was read" in result["error"].lower()
    assert "retry" in result["error"].lower()
    assert target.read_text() == "external writer won"


def test_canonical_path_uses_one_shared_lock(hass, tmp_path) -> None:
    """Equivalent path spellings share one integration-level edit lock."""
    target = tmp_path / "same.txt"
    target.write_text("content")

    canonical = target.resolve()
    equivalent = target.parent / "." / target.name

    first = file_module._get_edit_lock(hass, canonical)
    second = file_module._get_edit_lock(hass, equivalent.resolve())

    assert first is second
