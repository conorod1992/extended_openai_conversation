import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = {location:{pathname:"/extended-openai/assistant/basics"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class {attachShadow() { this.shadowRoot = {hasChildNodes:() => false, querySelector:() => null, querySelectorAll:() => []}; }};
globalThis.customElements = {define() {}, get() {}, whenDefined() { return Promise.resolve(); }};
const {ExtendedOpenAIManagementPanel:Panel} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js");

function panel(baseline = null) {
  const host = new Panel();
  host._agentId = "a";
  host._draftAgentId = baseline ? "a" : null;
  host._configData = baseline;
  host._configDataStale = true;
  host._draft = {prompt:"Unsaved prompt"};
  host._data = {agents:[{entry_id:"entry", subentry_id:"a"}]};
  host._viewKey = () => "assistant/basics";
  host._setConfigDirty = () => {};
  return host;
}

for (const [name, response] of [
  ["missing envelope", null], ["missing config", {revision:"bad"}],
  ["null config", {config:null}], ["array config", {config:[]}],
  ["text config", {config:"bad"}], ["number config", {config:42}],
]) {
  for (const baseline of [null, {revision:"old", config:{prompt:"Saved prompt"}}]) {
    test(`${name} rejects loading without replacing ${baseline ? "existing" : "absent"} configuration`, async () => {
      const host = panel(baseline);
      const draft = host._draft;
      host._call = async () => response;
      await assert.rejects(host._loadConfigDraft(), /invalid assistant configuration/);
      assert.equal(host._configData, baseline);
      assert.equal(host._draft, draft);
      assert.equal(host._cleanConfigSnapshots.size, 0);
      // A fresh read remains possible after failure.
      const recovered = {revision:"new", title:"Recovered", config:{prompt:"Valid prompt"}};
      host._call = async () => recovered;
      await host._loadConfigDraft();
      assert.equal(host._configData, recovered);
      assert.equal(host._draft.prompt, baseline ? "Unsaved prompt" : "Valid prompt");
    });
  }
}

test("a malformed response for a retired owner cannot fail the new route", async () => {
  const host = panel();
  host._call = async () => { host._agentId = "b"; return {revision:"bad"}; };
  await host._loadConfigDraft();
  assert.equal(host._configData, null);
});

test("an empty configuration object remains a valid response", async () => {
  const host = panel();
  host._call = async () => ({revision:"valid", config:{}});
  await host._loadConfigDraft();
  assert.deepEqual(host._draft, {});
});
