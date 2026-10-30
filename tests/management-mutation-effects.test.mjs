import assert from "node:assert/strict";
import test from "node:test";

// This reviewed inventory describes observable effects independently of production metadata.
const commands = {
  backup:["restore"], quiet_hours:["update"], configuration:["update", "save", "import", "duplicate"],
  function_repair:["save", "save_one", "delete_one", "configuration_save"],
  conversations:["delete", "end_active"], guest_mode:["save_policy", "update", "disable"],
  knowledge:["create", "update", "delete", "set_enabled"],
  memories:["add", "update", "delete", "clear", "temporary_add", "temporary_delete", "temporary_update", "temporary_clear", "reassign_legacy"],
  request_rules:["settings", "defaults", "wording_groups", "groups", "create", "update", "delete", "duplicate", "move", "rule_pack_import"],
  settings:["update"], tools:["save", "delete", "set_enabled", "save_group", "delete_group", "ha_add"], usage:["clear_details"],
};

globalThis.window = {location:{pathname:"/extended-openai/capabilities/functions"}, addEventListener() {}, removeEventListener() {}};
globalThis.history = {pushState() {}};
globalThis.localStorage = {getItem() { return null; }, setItem() {}};
globalThis.HTMLElement = class { attachShadow() { this.shadowRoot = {hasChildNodes:() => false, querySelector:() => null, querySelectorAll:() => []}; } };
globalThis.customElements = {define() {}, get() {}, whenDefined() { return Promise.resolve(); }};
const {ExtendedOpenAIManagementPanel:Panel} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js");
const {isAgentMutation} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-actions.js");

function panel() {
  const host = new Panel();
  host._agentId = host._draftAgentId = "a";
  host._data = {agents:[{entry_id:"entry-a", subentry_id:"a"}, {entry_id:"entry-b", subentry_id:"b"}]};
  host._configData = {revision:"config-1", config:{functions:[]}};
  host._configDataStale = false;
  host._cacheGeneration = 100;
  host._cleanConfigSnapshots = new Map(["a", "b"].flatMap(id => ["full", "retention"].map(p => [`entry-${id}|${id}|${p}`, {}])));
  host._sectionCache = new Map(["a", "b"].flatMap(id => ["overview", "capabilities/request-rules", "data-memory/knowledge", "usage-maintenance/usage|7", "usage-maintenance/usage|90", "guide"].map(view => [`${id}|${view}`, {}])));
  host._eocSectionCacheTimes = new Map([...host._sectionCache.keys()].map(key => [key, Date.now()]));
  host._scopeCatalogCache = new Map(["a", "b"].flatMap(id => ["memory", "archive"].map(kind => [`${id}|scopes|${kind}`, []])));
  host._scopeCatalogVisitKey = "a|scopes|memory";
  host._eocUsageWindowCache = new Map([["a|7|date", {}], ["b|90|date", {}]]);
  host._eocPendingRouteReads = new Map([["entry-a|a|100|overview", {}], ["entry-b|b|100|overview", {}]]);
  host._usageWindowGeneration = 10;
  return host;
}

for (const [section, actions] of Object.entries(commands)) for (const action of actions) {
  test(`${section}.${action} preserves mutation protection and exact projection effects`, () => {
    const host = panel();
    const restore = section === "backup";
    const config = (["backup", "configuration", "function_repair", "tools", "settings"].includes(section) && action !== "duplicate")
      || section === "knowledge" && action === "set_enabled" || section === "guest_mode" && action === "save_policy";
    const rules = ["configuration", "function_repair", "tools", "request_rules"].includes(section) && !(section === "configuration" && action === "duplicate");
    const collection = ["request_rules", "knowledge", "memories"].includes(section);
    const usage = section === "usage";
    assert.equal(isAgentMutation(section, action), true);
    host._invalidateAfterMutation("a", section, action);
    assert.equal(host._cacheGeneration, 101 + Number(restore || collection));
    assert.equal(host._configDataStale, config);
    assert.equal(host._cleanConfigSnapshots.has("entry-a|a|full"), !config);
    assert.equal(host._cleanConfigSnapshots.has("entry-b|b|full"), true);
    assert.equal(host._sectionCache.has("a|overview"), false);
    assert.equal(host._sectionCache.has("a|capabilities/request-rules"), !(restore || rules));
    assert.equal(host._sectionCache.has("a|data-memory/knowledge"), !(restore || section === "knowledge"));
    assert.equal(host._sectionCache.has("a|usage-maintenance/usage|7"), !(restore || usage));
    assert.equal(host._sectionCache.has("a|usage-maintenance/usage|90"), !(restore || usage));
    assert.equal(host._sectionCache.has("a|guide"), !restore);
    assert.equal(host._scopeCatalogCache.has("a|scopes|memory"), !(restore || section === "memories"));
    assert.equal(host._scopeCatalogCache.has("a|scopes|archive"), !(restore || section === "memories"));
    assert.equal(host._scopeCatalogCache.has("b|scopes|memory"), true);
    assert.equal(host._eocPendingRouteReads.size, 0);
    assert.equal(host._eocUsageWindowCache.has("a|7|date"), !(restore || usage));
    assert.equal(host._eocUsageWindowCache.has("b|90|date"), !restore, "restore retains its existing global Usage-window replacement");
    assert.equal(host._usageWindowGeneration, 10 + Number(restore || usage));
    for (const key of host._sectionCache.keys()) assert.equal(host._eocSectionCacheTimes.has(key), true);
    for (const view of ["overview", "capabilities/request-rules", "usage-maintenance/usage|90"]) assert.equal(host._sectionCache.has(`b|${view}`), !(section === "quiet_hours" && view === "overview"));
  });
}

for (const [section, action] of [["tools", "validate_current"], ["request_rules", "rule_pack_review"], ["guest_mode", "get"], ["configuration", "request_preview"], ["usage", "daily"], ["unknown", "save"], ["tools", "toString"], ["__proto__", "save"]]) {
  test(`${section}.${action} has no mutation effects`, () => {
    const host = panel(), before = [...host._sectionCache];
    assert.equal(isAgentMutation(section, action), false);
    host._invalidateAfterMutation("a", section, action);
    assert.deepEqual([...host._sectionCache], before);
    assert.equal(host._cacheGeneration, 100);
    assert.equal(host._configDataStale, false);
  });
}

test("missing owner does not invalidate any projections", () => {
  const host = panel(), before = [...host._sectionCache];
  host._invalidateAfterMutation(null, "backup", "restore");
  assert.deepEqual([...host._sectionCache], before);
  assert.equal(host._cacheGeneration, 100);
});

test("background agent restore preserves current reads while global policy changes invalidate them", () => {
  const host = panel();
  host._agentId = "b";
  host._invalidateAfterMutation("a", "backup", "restore");
  assert.equal(host._cacheGeneration, 100);
  assert.deepEqual([...host._eocPendingRouteReads.keys()], ["entry-b|b|100|overview"]);
  assert.equal(host._eocUsageWindowCache.has("b|90|date"), true);
  host._invalidateAfterMutation("a", "quiet_hours", "update");
  assert.equal(host._cacheGeneration, 101);
  assert.equal(host._sectionCache.has("b|overview"), false);
  assert.equal(host._eocPendingRouteReads.size, 0);
});


for (const family of ["tools", "request_rules"]) for (const explicit of [false, true]) {
  test(`${family} queues use acknowledged revisions and preserve explicit guards (${explicit})`, async () => {
    const host = panel();
    host._eocRequestRuleRevision = {agentId:"a", value:"rules-1"};
    const calls = [];
    let release;
    host._callCore = async (section, action, extra) => {
      calls.push({section, action, extra});
      if (calls.length === 1) return new Promise(resolve => { release = resolve; });
      return {revision:"ack-3", functions:[], function_groups:[], rules:[], groups:[]};
    };
    const action = family === "tools" ? "set_enabled" : "rule_pack_import";
    const first = host._call(family, action, {marker:"first"});
    while (!release) await Promise.resolve();
    const second = host._call(family, action, {marker:"second", ...(explicit ? {revision:"caller-guard"} : {})});
    await Promise.resolve();
    assert.equal(calls.length, 1);
    assert.equal(host._eocAgentMutations, 1);
    assert.equal(calls[0].extra.revision, family === "tools" ? "config-1" : "rules-1");
    release({revision:"ack-2", functions:[], function_groups:[], rules:[], groups:[]});
    await Promise.all([first, second]);
    assert.equal(calls[1].extra.revision, explicit ? "caller-guard" : "ack-2");
    assert.equal(host._pendingMutations.size, 0);
    assert.equal(host._eocAgentMutations, 0);
    assert.equal(family === "tools" ? host._configData.revision : host._eocRequestRuleRevision.value, "ack-3");
    if (family === "request_rules") assert.equal(host._eocRequestRuleMutationEpoch, 4);
  });
}

for (const fail of [false, true]) {
  test(`a Rules read crossing a mutation cannot overwrite its acknowledged revision (${fail})`, async () => {
    const host = panel();
    host._result = {revision:"rules-1"};
    let release;
    host._hass = {callWS:message => message.action === "list"
      ? new Promise(resolve => { release = resolve; })
      : fail ? Promise.reject(new Error("write rejected")) : Promise.resolve({revision:"rules-2", groups:[], rules:[]})};
    const reading = host._call("request_rules", "list");
    while (!release) await Promise.resolve();
    const writing = host._call("request_rules", "groups", {groups:[]});
    if (fail) await assert.rejects(writing, /write rejected/);
    else await writing;
    release({revision:"old-read"});
    await reading;
    assert.notEqual(host._eocRequestRuleRevision?.value, "old-read");
    assert.equal(host._eocRequestRuleMutationEpoch, 2);
    assert.equal(host._eocAgentMutations, 0);
  });
}

test("shared command declarations cannot be mutated by a consumer", async () => {
  const {mutationEffects} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-mutation-effects.js");
  assert.throws(() => { mutationEffects("tools", "save").configuration = false; }, TypeError);
  assert.equal(mutationEffects("tools", "save").configuration, true);
});
