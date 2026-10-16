"""Search links open the matching turn in the real size-limited archive page."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from custom_components.extended_openai_conversation_responses.conversation_archive import (
    ConversationArchive,
)
from custom_components.extended_openai_conversation_responses.management_history_queries import (
    archive_get_page,
    archive_search_page,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_archive import FakeArchiveStorage

NAVIGATE = r"""
import {readFileSync} from "node:fs";
const fixture = JSON.parse(readFileSync(0, "utf8"));
const module = await import(fixture.module);
class Element {
  constructor() { this.children = []; this.style = {}; this.listeners = {}; }
  append(element) { this.children.push(element); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  set innerHTML(value) { this.html = value; this.children = []; }
}
globalThis.document = {createElement: () => new Element()};
const body = new Element(), title = new Element(), host = {};
const dialog = {open: false, showModal() { this.open = true; }};
let links = [];
const requests = [];
const panel = {
  _agentId: "agent", _scopeId: "user:alice",
  _contentData: {sessions: {sessions: module.searchSessionRows(fixture.search)}},
  _ensureOwnedDialog() {}, _loading: () => "loading", _e: value => String(value),
  _formatDate: value => value, _empty: value => value,
  _activate(element, callback) { element.activate = callback; },
  shadowRoot: {
    querySelector: selector => ({
      "#archive-query": {closest: () => host, addEventListener() {}},
      "#session-dialog": dialog, "#session-body": body, "#session-title": title,
    })[selector],
    querySelectorAll: () => links,
  },
  async _call(section, action, cursor) {
    requests.push({section, action, ...cursor});
    return fixture.pages[String(cursor.start_turn)];
  },
};
const markup = module.renderConversations(panel);
links = [...markup.matchAll(/data-start-turn="(\d+)" data-id="([^"]+)"/g)]
  .map(match => ({dataset: {startTurn: match[1], id: match[2]}}));
module.bindConversationActions(panel);
const bodies = [];
for (const link of links) { await link.activate(); bodies.push(body.html); }
panel._contentData = {sessions: {sessions: [{session_id: fixture.sessionId}]}};
const listStarts = [...module.renderConversations(panel).matchAll(/data-start-turn="(\d+)"/g)]
  .map(match => Number(match[1]));
process.stdout.write(JSON.stringify({requests, bodies, listStarts}));
"""


@pytest.mark.parametrize(("count", "answer_length"), [(2, 80), (20, 2000)])
async def test_search_card_and_view_open_matching_size_limited_turn(
    count, answer_length
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to exercise the production frontend module")
    archive = ConversationArchive(FakeArchiveStorage(), "agent")
    await archive.async_initialize()
    session = await archive.async_begin_session(
        "browser",
        user_scope("alice", source="authenticated_user"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    assert session is not None
    for index in range(count):
        await archive.async_record_turn(
            session.session_id,
            run_id=f"run-{index}",
            user_text="needlephrase" if index == count - 1 else f"Question {index}",
            assistant_text="x" * answer_length,
            successful=True,
        )
    search = await archive_search_page(archive, "user:alice", "needlephrase")
    assert search["results"][0]["turn_index"] == count - 1
    pages = {
        str(start): await archive_get_page(
            archive, "user:alice", session.session_id, start_turn=start, limit=20
        )
        for start in (0, count - 1)
    }
    if count == 20:
        assert len(pages["0"]["turns"]) < count
        assert "needlephrase" not in json.dumps(pages["0"]["turns"])
        assert pages["0"]["next_start_turn"] < count - 1
    module = (
        Path(__file__).resolve().parents[1]
        / "custom_components/extended_openai_conversation_responses/frontend/management-history-pagination.js"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", NAVIGATE],
        input=json.dumps(
            {
                "module": module.as_uri(),
                "search": search,
                "pages": pages,
                "sessionId": session.session_id,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    navigation = json.loads(result.stdout)
    assert len(navigation["requests"]) == 2  # Card activation and View button.
    for request, body in zip(navigation["requests"], navigation["bodies"], strict=True):
        assert request["section"] == "conversations"
        assert request["action"] == "get"
        assert request["start_turn"] == count - 1
        assert request["text_offset"] == 0
        assert request["limit"] == 20
        assert "needlephrase" in body
    assert navigation["listStarts"] == [0, 0]

    # Size-dependent cursor traversal still recovers every turn exactly once.
    cursor = {"start_turn": 0, "text_offset": 0}
    recovered = []
    while True:
        page = await archive_get_page(
            archive, "user:alice", session.session_id, **cursor, limit=20
        )
        recovered.extend(turn["turn_id"] for turn in page["turns"])
        if not page["has_more"]:
            break
        cursor = {
            "start_turn": page["next_start_turn"],
            "text_offset": page["next_text_offset"],
        }
    assert len(recovered) == len(set(recovered)) == count
