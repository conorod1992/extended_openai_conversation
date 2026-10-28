import assert from "node:assert/strict";
import test from "node:test";
import {readFile} from "node:fs/promises";
import {createDataCollectionBackend} from "../tests_browser/data-collection-backend.mjs";

globalThis.window = {location:{pathname:"/extended-openai/data-memory/knowledge"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {}; } };
globalThis.customElements = {define() {}, get() {}, whenDefined() { return Promise.resolve(); }};

const frontend = new URL("../custom_components/extended_openai_conversation_responses/frontend/", import.meta.url);
const {ExtendedOpenAIManagementPanel:Panel} = await import(new URL("management-panel.js", frontend));
const {routeAssetPromise} = await import(new URL("management-route.js", frontend));
const acknowledgement = await import(new URL("configuration-acknowledgement.js", frontend));
const {applyConfigurationImport, bindSavedConfigurationActions} = await import(new URL("assistant-configuration-actions.js", frontend));
await Promise.all([routeAssetPromise("data-memory/knowledge"), routeAssetPromise("data-memory/memories")]);

function host(kind = "knowledge", PanelType = Panel) {
  const panel = new PanelType();
  const backend = createDataCollectionBackend(0);
  const controls = new Map();
  const control = id => {
    if (!controls.has(id)) controls.set(id, {disabled:false, hidden:false, value:"", dataset:{}, textContent:"", focus() {}, addEventListener(event, callback) { this[event] = callback; }});
    return controls.get(id);
  };
  const dialog = {open:true, querySelectorAll:() => [], close() { this.open = false; }};
  controls.set(`#${kind}-dialog`, dialog);
  panel.shadowRoot = {querySelector:selector => controls.get(selector) || null, querySelectorAll:() => [], contains:() => true};
  control(`#${kind}-save`);
  control("#memory-type");
  panel._agentId = "agent-1";
  panel._data = {agents:[{entry_id:"entry-1", subentry_id:"agent-1"}], is_admin:true};
  panel._hass = {user:{id:"test-user"}, callWS:request => backend.call(request)};
  panel._page = "data-memory";
  panel._subsection = kind === "knowledge" ? "knowledge" : "memories";
  panel._scopeId = "user:test-user";
  panel._memoryKind = "persistent";
  panel._result = kind === "knowledge" ? {sources:[]} : {memories:[]};
  panel._knowledgeMode = "create";
  panel._memoryEditorAgent = panel._agentId;
  panel._memoryEditorScope = panel._scopeId;
  panel._memoryMutationScope = panel._scopeId;
  panel._knowledgeValues = () => ({title:"Saved source", description:"", content:"A", enabled:true});
  panel._memoryValues = () => ({content:"A", category:"general", target_scope_id:panel._scopeId, type:"persistent", subject:"", key:"", valid_from:""});
  panel._setSaving = (button, pending) => { if (button) button.disabled = pending; };
  panel._setDialogError = (_kind, message) => panel.errors.push(message);
  panel._render = () => {};
  panel._confirm = async () => true;
  panel.toasts = [];
  panel.errors = [];
  panel._toast = (...args) => panel.toasts.push(args);
  return {panel, backend, control, dialog};
}

async function createdIdentityWitness(PanelType = Panel, kind = "knowledge") {
  const context = host(kind, PanelType);
  const {panel, backend, dialog} = context;
  // The server applies the write. Closing its editor then fails independently.
  dialog.close = () => { throw Error("modal unavailable"); };
  await panel[kind === "knowledge" ? "_saveKnowledge" : "_saveMemory"]();
  assert.deepEqual(panel.errors, [], "a committed create is not a failed save");
  assert.match(panel.toasts.at(-1)?.[0] || "", /(?:saved|added), but .*could not be refreshed/i);
  await panel[kind === "knowledge" ? "_saveKnowledge" : "_saveMemory"]();
  const actions = backend.calls.filter(call => call.section === (kind === "knowledge" ? "knowledge" : "memories")).map(call => call.action);
  assert.deepEqual(actions, kind === "knowledge" ? ["create", "update"] : ["add", "update"], "retry updates the acknowledged record rather than creating a duplicate");
  assert.equal(kind === "knowledge" ? backend.state.sources.length : backend.state.memories.length, 1);
  return context;
}

for (const kind of ["knowledge", "memory"]) {
  test(`${kind}: committed identity survives modal failure and a subsequent save`, async () => {
    await createdIdentityWitness(Panel, kind);
  });
}

test("temporary creation retains its identity and accepts the scoped update response", async () => {
  const {panel, backend, dialog} = host("memory");
  backend.state.temporary = [];
  panel._memoryKind = "temporary";
  panel._hass.config = {time_zone:"UTC"};
  panel._memoryValues = () => ({content:"Temporary A", category:"general", target_scope_id:panel._scopeId, type:"temporary", expires_at:new Date(Date.now() + 86400000).toISOString().slice(0, 19)});
  dialog.close = () => { throw Error("close failed"); };
  await panel._saveMemory();
  await panel._saveMemory();
  assert.deepEqual(panel.errors, []);
  assert.deepEqual(backend.calls.map(call => call.action), ["temporary_add", "temporary_update"]);
  assert.equal(backend.state.temporary.length, 1);
  assert.match(panel.toasts.at(-1)[0], /Memory updated, but/);
});

test("a cache invalidation failure cannot reject the server acknowledgement", async () => {
  const {panel, backend} = host();
  panel._invalidateAfterMutation = () => { throw Error("cache unavailable"); };
  await panel._saveKnowledge();
  assert.equal(backend.state.sources.length, 1);
  assert.deepEqual(panel.errors, []);
  assert.match(panel.toasts.at(-1)[0], /Knowledge source saved, but .*cache unavailable/);
  assert.equal(panel._eocRequestDiagnostics.at(-1).status, "fulfilled");
});

test("rendering a confirmed deletion fails without restoring or replaying it", async () => {
  const {panel, backend} = host();
  await backend.call({section:"knowledge", action:"create", title:"Delete", content:"A"});
  const source = backend.state.sources[0];
  panel._result.sources = [source];
  panel._render = () => { throw Error("renderer failed"); };
  assert.equal(await panel._deleteSource(source.source_id), true);
  assert.equal(backend.state.sources.length, 0);
  assert.equal(backend.calls.filter(call => call.action === "delete").length, 1);
  assert.match(panel.toasts.at(-1)[0], /Knowledge source deleted, but/);
});

for (const failure of ["incomplete", "lost", "rejected"]) {
  test(`${failure} reply cannot advance the creation identity`, async () => {
    const {panel, backend} = host();
    panel._hass.callWS = async request => {
      if (failure === "rejected") throw {code:"invalid_request", message:"Rejected before write"};
      const result = await backend.call(request);
      if (failure === "lost") throw Error("connection lost");
      return {...result, source:null};
    };
    await panel._saveKnowledge();
    assert.equal(panel._knowledgeMode, "create");
    assert.equal(panel._editingSource, undefined);
    assert.equal(backend.state.sources.length, failure === "rejected" ? 0 : 1);
    assert.equal(backend.calls.length, failure === "rejected" ? 0 : 1, "no automatic retry");
    assert.equal(panel.toasts.length, 0);
    assert.match(panel.errors[0], failure === "rejected" ? /Rejected before write/ : /outcome is unknown/);
  });
}

test("an acknowledgement for a retired owner cannot edit or notify its replacement", async () => {
  const {panel, backend} = host();
  panel._hass.callWS = async request => {
    const result = await backend.call(request);
    panel._agentId = "replacement";
    return result;
  };
  await panel._saveKnowledge();
  assert.equal(backend.state.sources.length, 1);
  assert.equal(panel._knowledgeMode, "create");
  assert.deepEqual(panel.toasts, []);
});

test("a same-user authentication round trip cannot reclaim a pending editor", async () => {
  const {panel, backend} = host();
  panel._hass.callWS = async request => {
    const result = await backend.call(request);
    panel._eocAuthenticationGeneration = 2;
    return result;
  };
  await panel._saveKnowledge();
  assert.equal(backend.state.sources.length, 1);
  assert.equal(panel._knowledgeMode, "create");
  assert.deepEqual(panel.toasts, []);
});

test("a zero-delete acknowledgement is unsuccessful, not uncertain", async () => {
  const {panel} = host();
  await panel._deleteSource("absent");
  assert.match(panel.toasts.at(-1)[0], /No Knowledge source was deleted/);
  assert.doesNotMatch(panel.toasts.at(-1)[0], /outcome is unknown/);
});

for (const operation of ["_updateGuestMode", "_disableGuestMode", "_saveReassign", "_clearUsageDetails", "_deleteSession"]) {
  test(`${operation}: a confirmed effect cannot become a failed mutation during presentation`, async () => {
    const {panel, control} = host();
    panel._viewKey = () => operation === "_deleteSession" ? "data-memory/conversations" : "capabilities/guest-mode";
    control("#reassign-scope").value = "user:test-user";
    let writes = 0;
    panel._hass.callWS = async () => {
      writes++;
      return operation === "_saveReassign" ? {reassigned:1}
        : operation === "_clearUsageDetails" ? {deleted_requests:0, deleted_runs:1}
        : operation === "_deleteSession" ? {deleted_sessions:1} : {status:{active:true}};
    };
    panel._contentData = {sessions:{sessions:[{session_id:"session"}]}};
    panel._refreshGuestModeMutation = async () => { throw Error("status render failed"); };
    panel._render = () => { throw Error("render failed"); };
    panel._refreshAfterMutation = async () => { throw Error("refresh failed"); };
    await panel[operation](operation === "_deleteSession" ? "session" : undefined);
    assert.equal(writes, 1);
    assert.match(panel.toasts.at(-1)?.[0] || "", /but .*could not be refreshed/);
    assert.doesNotMatch(panel.toasts.at(-1)[0], /Unable to (?:update|end|reassign|clear|delete)/);
  });
}

test("duplication acknowledges the created agent even if catalogue navigation fails", async () => {
  const {panel, control} = host();
  const button = control("#duplicate-agent");
  let created = 0;
  panel._call = async () => ({subentry_id:`copy-${++created}`, title:"Copy"});
  panel._loadAgents = async () => { throw Error("catalogue unavailable"); };
  bindSavedConfigurationActions(panel, panel.shadowRoot);
  await button.click({currentTarget:button});
  assert.equal(created, 1);
  assert.match(panel.toasts.at(-1)[0], /Created Copy, but .*catalogue unavailable/);
});

for (const mode of ["current", "new"]) {
  test(`${mode} import consumes an acknowledged preview before failed UI processing`, async () => {
    const {panel, control} = host();
    control("#import-document").value = "document";
    control('input[name="import-mode"]:checked').value = mode;
    control("#import-apply");
    const dialog = control("#import-dialog");
    dialog.close = () => { throw Error("close failed"); };
    panel._importDocument = "document";
    panel._importMode = mode;
    panel._importRevision = "r1";
    panel._configData = {revision:"r1"};
    let writes = 0, discarded = 0;
    panel._call = async () => { writes++; return {status:mode === "current" ? "updated" : "created", subentry_id:"imported"}; };
    panel._clearConfigDraft = () => { discarded++; };
    await applyConfigurationImport(panel, panel.shadowRoot);
    assert.equal(panel._importDocument, null);
    assert.equal(discarded, mode === "current" ? 1 : 0);
    assert.match(panel.toasts.at(-1)[0], /import.*(?:but|could not be refreshed)/i);
    await applyConfigurationImport(panel, panel.shadowRoot);
    assert.equal(writes, 1);
  });
}

// Run the same independent outcome assertion against deliberately broken source.
// Mutations are evaluated in memory; the checkout is never rewritten.
async function mutantModule(file, anchor, replacement) {
  const source = (await readFile(new URL(file, frontend), "utf8")).replaceAll("\r\n", "\n");
  assert.equal(source.split(anchor).length, 2, "sensitivity anchor must be unique");
  const mutated = source.replace(anchor, replacement).replaceAll("import.meta.url", JSON.stringify(new URL(file, frontend).href)).replace(/from "(\.\/[^\"]+)"/g, (_match, path) => `from "${new URL(path, frontend)}"`);
  return import(`data:text/javascript;base64,${Buffer.from(mutated).toString("base64")}`);
}

test("sensitivity: the creation witness detects loss of committed identity", async () => {
  const mutant = await mutantModule("management-panel.js", 'this._knowledgeMode = "edit";\n      setDialogBaseline', 'this._knowledgeMode = "create";\n      setDialogBaseline');
  await assert.rejects(() => createdIdentityWitness(mutant.ExtendedOpenAIManagementPanel), /retry updates the acknowledged record/);
});

async function presentationWitness(present) {
  const {panel} = host();
  const owner = acknowledgement.captureConfigurationOwner(panel);
  const outcome = await present(panel, owner, "Record saved", () => { throw Error("render failure"); });
  assert.equal(outcome, true);
  assert.match(panel.toasts.at(-1)[0], /Record saved, but/);
}
test("a failed notification cannot reject an acknowledged write", async () => {
  const {panel} = host();
  panel._toast = () => { throw Error("notification surface gone"); };
  assert.equal(await acknowledgement.presentAcknowledgedMutation(panel, acknowledgement.captureConfigurationOwner(panel), "Saved", () => {}), true);
});

test("import draft retirement preserves its acknowledgement owner", async () => {
  const {panel, control} = host();
  control("#import-document").value = "document";
  control('input[name="import-mode"]:checked').value = "current";
  control("#import-apply");
  control("#import-dialog").close = () => { throw Error("modal failed after retirement"); };
  panel._importDocument = "document";
  panel._importMode = "current";
  panel._importRevision = "r1";
  panel._configData = {revision:"r1"};
  panel._setConfigDirty = () => {};
  panel._call = async () => ({status:"updated", subentry_id:panel._agentId});
  await applyConfigurationImport(panel, panel.shadowRoot);
  assert.equal(panel._eocDraftGeneration, 1);
  assert.match(panel.toasts.at(-1)[0], /Configuration imported, but/);
});

test("sensitivity: the presentation witness detects rejection after acknowledgement", async () => {
  await presentationWitness(acknowledgement.presentAcknowledgedMutation);
  const mutant = await mutantModule("configuration-acknowledgement.js", "    processingError = error;", "    throw error;");
  await assert.rejects(() => presentationWitness(mutant.presentAcknowledgedMutation), /render failure/);
});
