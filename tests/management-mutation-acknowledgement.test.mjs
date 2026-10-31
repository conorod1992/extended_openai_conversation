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
const {updateProviderApiKey} = await import(new URL("management-provider-credentials.js", frontend));
const {ExtendedOpenAIDebugPanel:DebugPanel} = await import(new URL("debug-panel.js", frontend));
const {bindBroadcast} = await import(new URL("overview-broadcast.js", frontend));
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

function authenticationRoundTrip(panel) {
  const original = panel.hass;
  panel.style ||= {};
  panel.hass = {...original, user:{id:"intervening-user"}};
  panel.hass = original;
  assert.equal(panel.hass.user.id, "test-user");
  assert.equal(panel._eocAuthenticationGeneration, 2);
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

for (const failure of ["incomplete", "lost", "lost-nested", "lost-bare", "rejected"]) {
  test(`${failure} reply cannot advance the creation identity`, async () => {
    const {panel, backend} = host();
    panel._hass.callWS = async request => {
      if (failure === "rejected") throw {code:"invalid_request", message:"Rejected before write"};
      const result = await backend.call(request);
      if (failure === "lost") throw Error("connection lost");
      if (failure === "lost-nested") throw {type:"result", success:false, error:{code:3, message:"Connection lost"}};
      if (failure === "lost-bare") throw 3;
      return {...result, source:null};
    };
    await panel._saveKnowledge();
    assert.equal(panel._knowledgeMode, "create");
    assert.equal(panel._editingSource, undefined);
    assert.equal(backend.state.sources.length, failure === "rejected" ? 0 : 1);
    assert.equal(backend.calls.length, failure === "rejected" ? 0 : 1, "no automatic retry");
    assert.equal(panel.toasts.length, 0);
    assert.match(panel.errors[0], failure === "rejected" ? /Rejected before write/ : /outcome is unknown/);
    assert.equal(panel._eocRequestDiagnostics.at(-1).status,
      failure.startsWith("lost") ? "uncertain" : failure === "rejected" ? "rejected" : "fulfilled");
  });
}

async function malformedReplyWitness(PanelType, reply, cacheFailure) {
  const {panel, backend} = host("knowledge", PanelType);
  panel._hass.callWS = async request => { await backend.call(request); return reply; };
  if (cacheFailure) panel._invalidateAfterMutation = () => { throw Error("cache publication failed"); };
  await panel._saveKnowledge();
  assert.equal(backend.state.sources.length, 1);
  assert.equal(backend.calls.length, 1, "a malformed response cannot trigger another write");
  assert.match(panel.errors[0], /outcome is unknown/, "a malformed response must retain uncertainty when presentation also fails");
  assert.equal(panel._eocRequestDiagnostics.at(-1).status, "fulfilled");
  assert.equal(panel._knowledgeMode, "create", "an invalid acknowledgement cannot adopt the committed identity");
  assert.equal(panel._editingSource, undefined);
  assert.deepEqual(panel.toasts, []);
}

for (const reply of [null, false]) for (const cacheFailure of [false, true]) {
  test(`Knowledge malformed ${reply} response remains uncertain with cache failure=${cacheFailure}`, async () => {
    await malformedReplyWitness(Panel, reply, cacheFailure);
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

for (const operation of ["_deleteSource", "_deleteMemory", "_disableGuestMode", "_clearUsageDetails"]) {
  for (const transition of ["none", "agent", "authentication round trip"]) {
    test(`${operation}: delayed confirmation preserves its original owner (${transition})`, async () => {
      const {panel} = host(operation === "_deleteMemory" ? "memory" : "knowledge");
      panel._data.agents.push({entry_id:"entry-2", subentry_id:"replacement"});
      const records = {"agent-1":["shared-restored-id"], replacement:["shared-restored-id"]};
      const writes = [];
      panel._confirm = async () => {
        if (transition === "agent") panel._agentId = "replacement";
        if (transition === "authentication round trip") authenticationRoundTrip(panel);
        return true;
      };
      panel._hass.callWS = async request => {
        writes.push(request.subentry_id);
        records[request.subentry_id] = [];
        return operation === "_disableGuestMode" ? {status:{active:false}}
          : operation === "_clearUsageDetails" ? {deleted_requests:1, deleted_runs:1} : {deleted:1};
      };
      panel._refreshGuestModeMutation = async () => {};
      await panel[operation](["_deleteSource", "_deleteMemory"].includes(operation) ? "shared-restored-id" : undefined);
      assert.deepEqual(writes, transition === "none" ? ["agent-1"] : [], "a confirmation must not authorize another ownership session");
      assert.deepEqual(records.replacement, ["shared-restored-id"], "a matching restored ID in another agent must remain untouched");
      assert.deepEqual(records["agent-1"], transition === "none" ? [] : ["shared-restored-id"]);
    });
  }
}

test("a same-user authentication round trip cannot reclaim a pending editor", async () => {
  const {panel, backend} = host();
  panel._hass.callWS = async request => {
    const result = await backend.call(request);
    authenticationRoundTrip(panel);
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

async function duplicateRequestWitness(PanelType = Panel) {
  const {panel, control} = host("knowledge", PanelType);
  const button = control("#duplicate-agent"), created = [];
  const source = {revision:"source-r1", config:{chat_model:"source-model"}};
  panel._configData = source;
  panel._draft = source.config;
  panel._hass.callWS = async request => {
    assert.equal(request.action, "duplicate");
    created.push({subentry_id:`copy-${created.length + 1}`, title:"Copy"});
    return created.at(-1);
  };
  panel._invalidateAfterMutation = () => { throw Error("post-commit cache failure"); };
  panel._loadAgents = async () => {};
  bindSavedConfigurationActions(panel, panel.shadowRoot);
  await button.click({currentTarget:button});
  assert.equal(created.length, 1);
  assert.match(panel.toasts.at(-1)[0], /Created Copy, but .*post-commit cache failure/, "a created agent must remain acknowledged across request processing failure");
  assert.equal(panel._configData, source, "duplicating does not replace the source configuration");
  assert.equal(panel._draft, source.config);
  assert.equal(panel._eocRequestDiagnostics.at(-1).status, "fulfilled");
}

test("duplicate's production request path retains acknowledgement after invalidation failure", async () => {
  await duplicateRequestWitness();
});

test("duplicate's lost response remains uncertain without an automatic second creation", async () => {
  const {panel, control} = host();
  const button = control("#duplicate-agent");
  let created = 0;
  panel._hass.callWS = async () => { created++; throw Error("connection lost after create"); };
  panel._loadAgents = async () => assert.fail("an uncertain reply cannot select a created agent");
  bindSavedConfigurationActions(panel, panel.shadowRoot);
  await button.click({currentTarget:button});
  assert.equal(created, 1);
  assert.match(panel.toasts.at(-1)[0], /Unable to duplicate agent: .*outcome is unknown/);
  assert.equal(panel._eocRequestDiagnostics.at(-1).status, "uncertain");
  assert.equal(button.disabled, false);
});

for (const shape of ["nested", "bare"]) {
  const lost = () => shape === "nested" ? {type:"result", success:false, error:{code:3, message:"Connection lost"}} : 3;
  test(`credentials: ${shape} HA disconnect keeps one applied update uncertain`, async () => {
    const {panel} = host();
    let stored, writes = 0;
    panel._hass.callWS = async message => { writes++; stored = message.api_key; throw lost(); };
    await assert.rejects(() => updateProviderApiKey(panel, {entry_id:"entry-1"}, "replacement"), error => {
      assert.equal(error.mutationOutcome, "uncertain");
      assert.match(error.message, /outcome is unknown/);
      if (shape === "nested") assert.match(error.message, /Connection lost/);
      return true;
    });
    assert.equal(stored, "replacement");
    assert.equal(writes, 1);
  });

  test(`debugging: ${shape} HA disconnect cannot certify an applied configuration`, async () => {
    const panel = new DebugPanel();
    panel._agent = {entry_id:"entry-1", subentry_id:"agent-1"};
    let enabled = true, writes = 0;
    panel._hass = {callWS:async message => { writes++; enabled = message.enabled; throw lost(); }};
    panel._fetchRuns = async () => {};
    const notifications = [];
    panel._toast = message => notifications.push(message);
    await panel._configure({enabled:false});
    assert.equal(enabled, false);
    assert.equal(writes, 1);
    assert.match(notifications.at(-1), /outcome is unknown/);
  });

  for (const action of ["send", "set_enabled"]) {
    test(`Broadcast ${action}: ${shape} HA disconnect retains one uncertain effect`, async () => {
      const {panel, control} = host();
      panel._viewKey = () => "overview";
      panel._broadcastMessage = "Dinner is ready";
      panel._broadcastWholeHome = true;
      panel._e = String;
      panel._titleCase = String;
      control("#broadcast-card");
      control("#broadcast-send");
      control("#broadcast-enabled");
      let writes = 0;
      const state = {enabled:true, can_manage:true, catalog:{satellites:[{id:"satellite.kitchen", name:"Kitchen"}]}, history:[], pending:[]};
      panel._hass.callWS = async request => {
        if (request.action === "snapshot") return state;
        writes++;
        if (action === "set_enabled") state.enabled = request.enabled;
        throw lost();
      };
      await bindBroadcast(panel, Promise.resolve(state));
      if (action === "send") {
        const button = control("#broadcast-send");
        await button.click({currentTarget:button});
        assert.equal(panel._broadcastMessage, "Dinner is ready", "uncertainty must not acknowledge the message draft");
      } else await control("#broadcast-enabled").change({target:{checked:false}});
      assert.equal(writes, 1);
      assert.match(panel.toasts.at(-1)[0], /outcome is unknown/);
    });
  }
}

for (const newer of [false,true]) test(`Broadcast consumes only its submitted draft across assistant changes (newer: ${newer})`, async()=>{
  const {panel,control}=host();
  panel._viewKey=()=>"overview"; panel._broadcastMessage="Dinner";panel._broadcastWholeHome=true;
  panel._e=String;panel._titleCase=String;
  control("#broadcast-card");const button=control("#broadcast-send");control("#broadcast-enabled");
  const composer=control("#broadcast-message");composer.value="Dinner";
  const state={enabled:true,can_manage:true,catalog:{satellites:[{id:"satellite.kitchen",name:"Kitchen"}]},history:[],pending:[]};
  const sent=[];
  panel._hass.callWS=async request=>{
    if(request.action==="snapshot")return state;
    sent.push(request.message); panel._agentId="another-agent";
    if(newer){panel._broadcastMessage="New message";composer.value="New message";panel._broadcastDraftRevision=(panel._broadcastDraftRevision||0)+1;}
    return {id:"broadcast-1", targets:["satellite.kitchen"], deliveries:{"satellite.kitchen":{status:"queued_idle"}}};
  };
  await bindBroadcast(panel,Promise.resolve(state));
  await button.click({currentTarget:button});
  assert.equal(panel._broadcastMessage,newer?"New message":"");
  assert.equal(composer.value,newer?"New message":"");
  await button.click({currentTarget:button});
  assert.deepEqual(sent,newer?["Dinner","New message"]:["Dinner"]);
});

async function broadcastAdmissionWitness(bind = bindBroadcast, {refreshFails = false, status = "queued_idle"} = {}) {
  const {panel, control} = host();
  panel._viewKey = () => "overview";
  panel._broadcastMessage = "Dinner";
  panel._broadcastWholeHome = true;
  panel._e = String; panel._titleCase = String;
  control("#broadcast-card"); control("#broadcast-enabled");
  const button = control("#broadcast-send"), composer = control("#broadcast-message");
  composer.value = "Dinner";
  const state = {enabled:true, can_manage:true, catalog:{satellites:[{id:"assist_satellite.kitchen", name:"Kitchen"}]}, history:[], pending:[]};
  const admitted = [];
  panel._hass.callWS = async request => {
    if (request.action === "snapshot") {
      if (refreshFails) throw Error("history unavailable");
      return state;
    }
    // Match BroadcastMessage.as_dict(): admission identity and concrete targets,
    // with status on each delivery, never on the top-level acknowledgement.
    const record = {id:"broadcast-1", message:request.message, targets:["assist_satellite.kitchen"],
      deliveries:{"assist_satellite.kitchen":{status}}};
    admitted.push(record);
    return record;
  };
  await bind(panel, Promise.resolve(state));
  await button.click({currentTarget:button});
  assert.equal(panel._broadcastMessage, "", "an admitted Broadcast must retire its submitted draft");
  assert.equal(composer.value, "");
  assert.match(panel.toasts.at(-1)[0], /Broadcast queued/);
  if (refreshFails) assert.match(panel.toasts.at(-1)[0], /could not be refreshed/);
  await button.click({currentTarget:button});
  assert.equal(admitted.length, 1, "the next click cannot resubmit the acknowledged message");
}

for (const status of ["queued_idle", "delivered", "failed"]) {
  test(`Broadcast admission retires the composer independently of delivery state (${status})`, async () => {
    await broadcastAdmissionWitness(bindBroadcast, {status});
  });
}
test("Broadcast admission survives a subsequent history refresh failure", async () => {
  await broadcastAdmissionWitness(bindBroadcast, {refreshFails:true});
});

test("Broadcast acknowledgement cannot retire a draft after an authentication round trip", async () => {
  const {panel, control} = host();
  panel._viewKey = () => "overview"; panel._broadcastMessage = "Dinner"; panel._broadcastWholeHome = true;
  panel._e = String; panel._titleCase = String;
  control("#broadcast-card"); control("#broadcast-enabled");
  const button = control("#broadcast-send"), composer = control("#broadcast-message"); composer.value = "Dinner";
  const state = {enabled:true, can_manage:true, catalog:{satellites:[{id:"assist_satellite.kitchen",name:"Kitchen"}]},history:[],pending:[]};
  panel._hass.callWS = async request => {
    if (request.action === "snapshot") return state;
    authenticationRoundTrip(panel);
    panel._broadcastMessage = "Other session's draft"; composer.value = panel._broadcastMessage;
    return {id:"broadcast-1",targets:["assist_satellite.kitchen"]};
  };
  await bindBroadcast(panel, Promise.resolve(state));
  await button.click({currentTarget:button});
  assert.equal(panel._broadcastMessage, "Other session's draft");
  assert.equal(composer.value, "Other session's draft");
});

for (const response of [{}, {status:"queued"}, {id:"",targets:["assist_satellite.kitchen"]}, {id:"b",targets:[]}, {id:"b",targets:[null]}]) {
  test(`Broadcast retains its draft for an incomplete admission: ${JSON.stringify(response)}`, async () => {
    const {panel, control} = host();
    panel._viewKey = () => "overview"; panel._broadcastMessage = "Dinner"; panel._broadcastWholeHome = true;
    panel._e = String; panel._titleCase = String;
    control("#broadcast-card"); control("#broadcast-enabled");
    const button = control("#broadcast-send");
    const state = {enabled:true,can_manage:true,catalog:{satellites:[{id:"assist_satellite.kitchen",name:"Kitchen"}]},history:[],pending:[]};
    panel._hass.callWS = async () => response;
    await bindBroadcast(panel, Promise.resolve(state));
    await button.click({currentTarget:button});
    assert.equal(panel._broadcastMessage, "Dinner");
    assert.match(panel.toasts.at(-1)[0], /outcome is unknown/);
  });
}

test("transport adaptation preserves an authoritative server rejection unchanged", async () => {
  const rejection = {code:"invalid_request", message:"Rejected before dispatch"};
  await assert.rejects(() => acknowledgement.callMutation(async () => { throw rejection; }), error => error === rejection);
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
async function mutantModule(file, anchor, replacement, urlOnly = false) {
  const source = (await readFile(new URL(file, frontend), "utf8")).replaceAll("\r\n", "\n");
  assert.equal(source.split(anchor).length, 2, "sensitivity anchor must be unique");
  const mutated = source.replace(anchor, replacement).replaceAll("import.meta.url", JSON.stringify(new URL(file, frontend).href)).replace(/from "(\.\/[^\"]+)"/g, (_match, path) => `from "${new URL(path, frontend)}"`);
  const url = `data:text/javascript;base64,${Buffer.from(mutated).toString("base64")}`;
  return urlOnly ? url : import(url);
}

test("sensitivity: Broadcast's next-action witness detects the obsolete top-level status contract", async () => {
  const brokenContract = await mutantModule("broadcast-acknowledgement.js",
    'value !== null && "id" in value\n    && typeof value.id === "string" && !!value.id.trim()\n    && "targets" in value && Array.isArray(value.targets) && value.targets.length > 0\n    && value.targets.every((/** @type {unknown} */ target) => typeof target === "string" && !!target.trim())',
    'value !== null && "status" in value && typeof value.status === "string"', true);
  const mutant = await mutantModule("overview-broadcast.js", 'from "./broadcast-acknowledgement.js"', `from "${brokenContract}"`);
  await assert.rejects(() => broadcastAdmissionWitness(mutant.bindBroadcast), /an admitted Broadcast must retire its submitted draft/);
});

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

test("sensitivity: duplicate witness detects a bypassed request acknowledgement boundary", async () => {
  const mutant = await mutantModule("management-panel.js",
    "if (mutationEffects(section, action)) processMutationResponse(result, process);",
    "if (mutationEffects(section, action) && action !== 'duplicate') processMutationResponse(result, process);");
  await assert.rejects(() => duplicateRequestWitness(mutant.ExtendedOpenAIManagementPanel), /a created agent must remain acknowledged/);
});

test("sensitivity: a retired authentication confirmation cannot dispatch a deletion", async () => {
  const guarded = 'if (!sourceId || !await this._confirm("Delete Knowledge source?", "This permanently removes the selected source from this agent\'s local Knowledge Library.", "Delete")) return;\n    if (!configurationSubmissionIsCurrent(this, submission) || !this._ownsRetainedMutation(owner)) return;';
  const mutant = await mutantModule("management-panel.js", guarded, guarded.slice(0, guarded.indexOf("\n")));
  async function witness(PanelType) {
    const {panel} = host("knowledge", PanelType);
    let deleted = 0;
    panel._confirm = async () => { authenticationRoundTrip(panel); return true; };
    panel._hass.callWS = async () => { deleted++; return {deleted:1}; };
    await panel._deleteSource("retained-source");
    assert.equal(deleted, 0, "retired authentication confirmation must not delete a source");
  }
  await witness(Panel);
  await assert.rejects(() => witness(mutant.ExtendedOpenAIManagementPanel), /retired authentication confirmation must not delete/);
});

test("sensitivity: primitive reply processing cannot replace the uncertain mutation outcome", async () => {
  const file = new URL("mutation-response.js", frontend);
  const source = await readFile(file, "utf8");
  const guarded = 'if (result && typeof result === "object") responseProcessingErrors.set(result, error);';
  assert.equal(source.split(guarded).length, 2);
  const oldThrow = source.replace(guarded, 'if (!result || typeof result !== "object") throw error;\n    responseProcessingErrors.set(result, error);')
    .replace(/from "(\.\/[^\"]+)"/g, (_match, path) => `from "${new URL(path, file)}"`);
  const url = `data:text/javascript;base64,${Buffer.from(oldThrow).toString("base64")}`;
  const acknowledgementSource = (await readFile(new URL("configuration-acknowledgement.js", frontend), "utf8"))
    .replace('from "./mutation-response.js"', `from "${url}"`)
    .replace(/from "(\.\/[^\"]+)"/g, (_match, path) => `from "${new URL(path, frontend)}"`);
  const acknowledgementUrl = `data:text/javascript;base64,${Buffer.from(acknowledgementSource).toString("base64")}`;
  const mutant = await mutantModule("management-panel.js", 'from "./configuration-acknowledgement.js";', `from "${acknowledgementUrl}";`);
  await assert.rejects(() => malformedReplyWitness(mutant.ExtendedOpenAIManagementPanel, null, true), /a malformed response must retain uncertainty/);
});

for (const code of ["unknown_error", ""]) test(`generic mutation failure remains uncertain: ${code}`, async () => {
  const error = {code, message:"Unknown error"};
  await assert.rejects(() => acknowledgement.callMutation(async () => {throw error;}), failure =>
    failure.mutationOutcome === "uncertain" && failure.cause === error && /refresh before retrying/.test(failure.message));
});
