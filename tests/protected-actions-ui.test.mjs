import assert from "node:assert/strict";
import test from "node:test";

import {protectedActionsDialogs, renderProtectedActions} from "../custom_components/extended_openai_conversation_responses/frontend/protected-actions-ui.js";

const panel = {
  _e: (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;"),
  _result: {
    pin_configured: false,
    rules: [],
  },
};

test("Protected Actions empty state explains confirmation, PIN, privacy, and examples", () => {
  const html = renderProtectedActions(panel);
  for (const copy of [
    "Protected Actions",
    "Add an extra check",
    "PIN checking happens locally",
    "never sent to OpenAI",
    "Confirmation is not authentication",
    "Protection boundary",
    "cannot always check one by one",
    "Protect the wrapper itself",
    "lock.unlock",
    "homeassistant.restart",
  ]) assert.match(html, new RegExp(copy.replace(".", "\\."), "i"));
});

test("Protected Actions dialogs use masked repeated PIN and Home Assistant selector", () => {
  const html = protectedActionsDialogs();
  assert.match(html, /type="password"/);
  assert.match(html, /Repeat PIN/);
  assert.match(html, /Set a PIN on the Protected Actions page/);
  assert.match(html, /ha-selector/);
  assert.match(html, /Entity/);
  assert.match(html, /Device/);
  assert.match(html, /Area/);
});

test("PIN rule status remains text-readable without relying on colour", () => {
  const html = renderProtectedActions({
    ...panel,
    _result: {
      pin_configured: true,
      rules: [{id:"1",name:"Front door",enabled:true,domain:"lock",service:"unlock",protection:"pin",entity_id:["lock.front"],device_id:[],area_id:[]}],
    },
  });
  assert.match(html, />Set</);
  assert.match(html, /Require PIN/);
  assert.match(html, /lock\.front/);
});
