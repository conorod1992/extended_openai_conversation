import assert from "node:assert/strict";
import {ExtendedOpenAIManagementPanel as Panel} from "./management-ownership-harness.mjs";
import {captureConfigurationSubmission, completeConfigurationAcknowledgement, presentAcknowledgedMutation} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";
import {synchronizePersistedFunctions} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js";
import {applyRequestRuleMutation} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-core.js";
import {applyConfigurationImport, refreshUncertainConfigurationImport} from "../custom_components/extended_openai_conversation_responses/frontend/assistant-configuration-actions.js";
import {callBackupTransfer} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";
import {routeAssetPromise} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const probe = JSON.parse(input);
const collectionFamily = probe.family.split("_")[0];
const panel = new Panel();
const controls = new Map();
const clone = value => structuredClone(value);
Object.assign(panel, {
  isConnected:true, _agentId:"agent-1", _draftAgentId:"agent-1",
  _page:"assistant", _subsection:"basics", _scopeId:"shared:household", _memoryKind:"persistent", _query:"",
  _data:{is_admin:true, agents:[{entry_id:"entry-1", subentry_id:"agent-1", memory_count:1}], scopes:[{scope_id:"shared:household", memory_count:1}]},
  _configData:clone(probe.before), _draft:clone(probe.before.config), _draftTitle:probe.before.title,
  _render() {}, _setSaving(button, value) { if (button) button.disabled = value; },
  _confirm:async () => true, _loadAgents:async () => {}, toasts:[],
  _toast(message, warning) { this.toasts.push({message, warning}); },
});
panel.shadowRoot.querySelector = selector => controls.get(selector) || null;
panel.shadowRoot.querySelectorAll = () => [];
panel.shadowRoot.contains = () => true;
panel._result = clone(probe.before);
let sends = 0;
const send = async request => {
  sends++;
  assert.equal(request.action, probe.family === "restore" ? "import_restore" : probe.action);
  if (probe.family !== "restore") {
    assert.equal(request.section, probe.section);
    for (const [key, value] of Object.entries(probe.payload)) {
      assert.deepEqual(key === "document" ? JSON.parse(request.document) : request[key], value,
        `the checked producer and real consumer must exchange the same ${key}`);
    }
  }
  // The Python producer has already committed and verified retained state. A
  // transport disconnect supplies no reply at all, even though that state exists.
  if (probe.outcome === "lost") throw {code:3, message:"Connection lost after server processing"};
  if (!probe.envelope.success) throw clone(probe.envelope.error);
  return clone(probe.envelope.result);
};
panel._hass = {user:{id:"admin"}, callWS:send};
const baseline = clone(panel._configData);
const authored = clone(panel._draft);
const owner = captureConfigurationSubmission(panel);

if (probe.family === "import") {
  const source = JSON.stringify(probe.payload.document);
  controls.set("#import-document", {value:source});
  controls.set("#import-apply", {disabled:false});
  controls.set("#import-summary", {textContent:"Reviewed source"});
  controls.set('input[name="import-mode"]:checked', {value:probe.payload.mode});
  controls.set("#import-dialog", {close() { throw Error("Committed import presentation witness"); }});
  panel._importDocument = source; panel._importMode = probe.payload.mode; panel._importRevision = baseline.revision;
  await applyConfigurationImport(panel, panel.shadowRoot);
  if (probe.outcome === "rejected") {
    assert.equal(panel._importDocument, source, "a definite rejection retains its reviewed source");
    assert.equal(panel._eocImportRecovery, undefined);
  } else {
    assert.equal(panel._importDocument, null, "a committed or unknown import consumes the one-shot preview");
    assert.equal(controls.get("#import-document").value, source, "the raw source remains recoverable");
    await applyConfigurationImport(panel, panel.shadowRoot);
    assert.equal(sends, 1, "Apply cannot replay an acknowledged or unknown import");
    if (probe.outcome === "lost") {
      let reads = 0;
      panel._loadAgents = async () => { reads++; throw Error("Authoritative catalogue unavailable"); };
      await assert.rejects(refreshUncertainConfigurationImport(panel), /catalogue unavailable/);
      assert.ok(panel._eocImportRecovery, "failed refresh cannot authorize another import");
      panel._loadAgents = async () => { reads++; return {sequence:panel._eocCatalogReadSequence, userId:"admin"}; };
      await refreshUncertainConfigurationImport(panel);
      assert.equal(reads, 2);
      assert.equal(panel._eocImportRecovery, null);
    }
  }
} else if (probe.family === "memory_delete") {
  await routeAssetPromise("data-memory/memories");
  await routeAssetPromise("memory-browser");
  panel._page = "data-memory"; panel._subsection = "memories";
  panel._scopeId = probe.payload.scope_id;
  panel._result = {memories:clone(probe.before_memories)};
  await panel._deleteMemory(probe.before_memories[0].memory_id);
  assert.equal(panel._result.memories.length, probe.outcome === "committed" ? 0 : 1,
    "only a complete deletion acknowledgement removes the visible memory");
  if (probe.outcome !== "committed") assert.match(panel.toasts.at(-1).message, /Unable to delete memory/);
} else {
  if (collectionFamily === "rule") {
    panel._page = "capabilities"; panel._subsection = "request-rules";
    panel._result = clone(probe.rules);
  }
  let response, error;
  try {
    response = probe.family === "restore"
      ? await callBackupTransfer(panel, "import_restore", {})
      : await panel._call(probe.section, probe.action, probe.payload);
  } catch (failure) { error = failure; }
  if (probe.outcome === "lost" || probe.outcome === "committed_warning") {
    assert.equal(error?.mutationOutcome, probe.outcome === "lost" ? "uncertain" : "committed");
    assert.deepEqual(panel._configData, baseline, "no missing response becomes a saved configuration baseline");
    assert.deepEqual(panel._draft, authored, "unknown outcomes retain authored values");
    if (["function", "group", "rule"].includes(collectionFamily)) {
      await assert.rejects(panel._call(probe.section, probe.action, probe.payload), failure => failure.mutationOutcome === "uncertain");
      assert.equal(sends, 1, "the production mutation lane blocks a second write pending recovery");
    }
  } else if (probe.outcome === "rejected") {
    assert.ok(error, "the real backend rejected the command");
    assert.notEqual(error.mutationOutcome, "committed");
    assert.notEqual(error.mutationOutcome, "uncertain");
    assert.deepEqual(panel._configData, baseline);
    assert.deepEqual(panel._draft, authored);
  } else {
    assert.equal(error, undefined);
    if (["function", "group"].includes(collectionFamily)) {
      assert.equal(synchronizePersistedFunctions(panel, response, owner), true);
      assert.deepEqual(panel._configData.config.function_groups, response.function_groups);
      assert.deepEqual(panel._configData.config.functions, response.functions);
      assert.equal(panel._configData.revision, response.revision);
      if (probe.family.endsWith("_delete")) assert.deepEqual(panel._configData.config[collectionFamily === "function" ? "functions" : "function_groups"], []);
    } else if (probe.family === "configuration") {
      assert.equal(await completeConfigurationAcknowledgement(panel, response, owner, {message:"Changes saved"}), true);
      assert.equal(panel._configData.config.max_tokens, 723);
      assert.equal(panel._draft.max_tokens, 723);
    } else if (collectionFamily === "rule") {
      assert.equal(applyRequestRuleMutation(panel, probe.action, response, {ruleId:probe.payload.rule_id}), true);
      assert.equal(panel._result.rules.length, probe.action === "delete" ? 0 : 1);
      if (probe.action !== "delete") assert.equal(panel._result.rules[0].id, response.rule.id);
      assert.equal(panel._eocRequestRuleRevision.value, response.revision);
    } else assert.equal(response.status, "restored");
    assert.equal(await presentAcknowledgedMutation(panel, owner, "Changes saved", () => { throw Error("Optional display refresh witness"); }, response), true);
    assert.match(panel.toasts.at(-1).message, /Changes saved, but .*could not be refreshed/);
    assert.equal(sends, 1, "a presentation warning never replays the server mutation");
  }
}
assert.equal(sends, 1);
console.log(`${probe.family}/${probe.outcome}: retained backend evidence reached the production consumer`);
