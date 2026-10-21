"""Full provider handling fingerprints valid attachment inputs outside HA's loop."""

import asyncio
import threading

from PIL import Image
import pytest

from custom_components.extended_openai_conversation_responses import (
    context_usage_hardening as context,
    entity as owner,
)
from custom_components.extended_openai_conversation_responses.completion_events import (
    completion_event_scope,
)
from homeassistant.components import conversation
from tests.test_empty_responses_completion import response as responses_response
from tests.test_normal_chat_completion_termination import response as chat_response
from tests.test_responses_api import _control_entity
from tests.test_responses_history_coherence import message


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
@pytest.mark.parametrize(
    "large", [False, True], ids=["small-control", "large-valid-images"]
)
async def test_full_handler_revision_is_off_loop_and_detects_replaced_attachment(
    hass, tmp_path, monkeypatch, api, large
):
    paths = [tmp_path / f"image-{i}.png" for i in range(3 if large else 1)]

    def prepare():
        size = 2048 if large else 64
        for path in paths:
            Image.new("RGBA", (size, size), (10, 20, 30, 255)).save(
                path, compress_level=0
            )
            with Image.open(path) as decoded:
                decoded.load()
        return sum(path.stat().st_size for path in paths)

    total = await asyncio.to_thread(prepare)
    if large:
        assert 48 * 1024 * 1024 < total <= 50 * 1024 * 1024
        assert all(path.stat().st_size < 20 * 1024 * 1024 for path in paths)

    async def executor(function, *args):
        return await asyncio.to_thread(function, *args)

    monkeypatch.setattr(hass, "async_add_executor_job", executor)
    entity, log, create = _control_entity(hass, api, [])
    log.content[-1] = conversation.UserContent(
        content="Describe the images",
        attachments=[
            conversation.Attachment(
                media_content_id=str(path), mime_type="image/png", path=path
            )
            for path in paths
        ],
    )

    async def provider(**kwargs):
        if api == "responses":
            return responses_response(
                True, [message("answer-" + str(create.await_count), "Done")]
            )
        return chat_response(True, answer="Done")

    create.side_effect = provider
    fingerprint = context.prepared_input_revision
    main = threading.get_ident()
    threads, revisions = [], []

    def checked(value):
        threads.append(threading.get_ident())
        result = fingerprint(value)
        revisions.append(result)
        return result

    monkeypatch.setattr(context, "prepared_input_revision", checked)
    monkeypatch.setattr(owner, "prepared_input_revision", checked, raising=False)
    initial_content = tuple(log.content)
    async with completion_event_scope():
        await entity._async_handle_chat_log(log, [], [])
    assert len(revisions) == 2 and revisions[0] == revisions[1]
    first = revisions[0]
    log.content[:] = initial_content
    await asyncio.to_thread(
        lambda: Image.new("RGBA", (64, 64), (200, 30, 40, 255)).save(
            paths[0], compress_level=0
        )
    )
    # Do not change the retained text: the changed image itself must change revision.
    async with completion_event_scope():
        await entity._async_handle_chat_log(log, [], [])
    assert len(revisions) == 4 and revisions[2] == revisions[3]
    assert first != revisions[2]
    assert create.await_count == 2
    assert main not in threads
