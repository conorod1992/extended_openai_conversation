import assert from "node:assert/strict";
import {renderConfiguration, renderConfigurationActions} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js";
import {renderTools, configurationDialogs} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools.js";
import {renderRequestRules} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js";
import {requestRulesDialog} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";
import {renderGuide} from "../custom_components/extended_openai_conversation_responses/frontend/guide-page.js";

import {routeAssetPromise} from "../custom_components/extended_openai_conversation_responses/frontend/management-route.js";
await Promise.all(["assistant/basics", "assistant/voice", "capabilities/home-assistant", "data-memory/conversations", "usage-maintenance/retention"].map((view) => routeAssetPromise(view)));
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"})[c]);
const panel = (config = {}) => ({_e:escape, _titleCase:String, _empty:escape,
  _agentId:"a", _draft:config, _result:{config, options:{}, defaults:{}}});

// Browser-capable and standalone environments use identical pure render paths.
// Any reintroduced production parse/serialize pass fails before output inspection.
globalThis.document = {createElement() { throw new Error("Rendering must not parse its own HTML"); }};
try {
  for (const enabled of [false, true]) for (const delayed of [false, true]) {
    const owner = panel({local_intents_enabled:enabled, local_intent_delayed_commands_to_ai:delayed, local_intent_exclusions:["HassTurnOn"]});
    owner._configSections = ["local"];
    owner._result.local_handling = {intents:[{intent:"HassTurnOn", label:"Lights <on>"}]};
    const html = renderConfiguration(owner);
    const actions = renderConfigurationActions(owner, owner._configSections);
    assert.doesNotMatch(html, /agent-actions-menu|config-toolbar|action-help/);
    assert.match(actions, /class="[^"]*\bagent-actions-menu\b[^"]*"/);
    assert.equal((html.match(/<details class="local-handling-help">/g) || []).length, 1);
    assert.doesNotMatch(html, /data-field="local_intent_delayed_commands_to_ai"/);
    const choice = html.match(/<div id="local-intent-list"[^>]*><label[^>]*><input[^>]+>/)?.[0];
    assert.ok(choice, "delayed device commands belong first in the exception list");
    assert.equal(choice.includes("checked"), delayed);
    assert.equal(choice.includes("disabled"), !enabled);
    assert.match(html, /Lights &lt;on&gt;/);
    assert.equal(renderConfiguration(owner), html, "rerender cannot duplicate controls");
  }

  const owner = panel({chat_model:"custom", reasoning_effort:"high", temperature:0.4, max_tokens:100, api_mode:"chat_completions", functions:[{spec:{name:"one"},function:{type:"native"}}]});
  owner._configSections = ["general", "model"];
  owner._result.model_capabilities = {supports_reasoning_effort:true, supports_temperature:true};
  owner._result.options.api_mode = [{value:"auto",label:"Auto"},{value:"responses",label:"Responses"},{value:"chat_completions",label:"Chat"}];
  owner._modelCatalogData = {requested_model:"custom",catalog_models:[{id:'model"',display_name:"<Model>",status:"deprecated"}],model_metadata:{status:"deprecated",lifecycle_note:"<Switch>",reasoning:{supported:true,efforts:["low","high"]},temperature:{support:"conditional",allowed_reasoning_efforts:["low"]},limits:{max_output_tokens:500},api:{responses:true,chat_completions:true},function_calling:{responses:true,chat_completions:false}}};
  let html = renderConfiguration(owner);
  assert.match(html, /value="high"[^>]*selected>High/);
  assert.match(html, /data-config="temperature"[^>]*disabled/);
  assert.match(html, /max="500"/);
  assert.match(html, /value="chat_completions" disabled/);
  assert.match(html, /&lt;Switch&gt;/);
  assert.match(html, /value="model&quot;" label="&lt;Model&gt; — Deprecated"/);
  owner._draft.reasoning_effort = "low";
  html = renderConfiguration(owner);
  assert.doesNotMatch(html, /data-config="temperature"[^>]*disabled/);
  owner._modelCatalogData.model_metadata.reasoning.supported = false;
  assert.doesNotMatch(renderConfiguration(owner), /data-config="reasoning_effort"/);

  const unsupportedOwner = panel({
    temperature:0.6,
    top_p:0.8,
    reasoning_effort:"high",
    service_tier:"flex",
    shorten_tool_call_id:false,
  });
  unsupportedOwner._configSections = ["model"];
  unsupportedOwner._viewKey = () => "assistant/model-responses";
  unsupportedOwner._result.model_capabilities = {
    supports_temperature:false,
    supports_top_p:false,
    supports_reasoning_effort:false,
    supports_service_tier:false,
  };
  unsupportedOwner._result.options.reasoning_effort = [{value:"low",label:"Low"},{value:"high",label:"High"}];
  unsupportedOwner._result.options.service_tier = [{value:"flex",label:"Flex"},{value:"default",label:"Default"}];
  const unsupportedHtml = renderConfiguration(unsupportedOwner);
  for (const key of ["temperature","top_p","reasoning_effort","service_tier"]) {
    assert.match(
      unsupportedHtml,
      new RegExp(`class="setting is-disabled eoc-unavailable-model-setting"[^>]*data-field="${key}"`),
      `${key} should be rendered at source as unavailable`,
    );
    assert.match(
      unsupportedHtml,
      new RegExp(`id="config-${key}"[^>]*disabled`),
      `${key} should preserve its saved value in a disabled control`,
    );
  }
  const modelOrder = ["temperature","top_p","reasoning_effort","service_tier","shorten_tool_call_id"]
    .map((key) => unsupportedHtml.indexOf(`data-field="${key}"`));
  assert.ok(modelOrder.every((position) => position >= 0));
  assert.deepEqual([...modelOrder].sort((a,b) => a - b), modelOrder);

  for (const enabled of [true,false]) {
    const owner = panel({functions:[{spec:{name:"one",description:"<Tool>"},function:{type:"native"},enabled:false}],function_groups:[{id:'g"',name:"<Group>",description:"& private",functions:["one"],enabled}]});
    const html = renderTools(owner);
    assert.equal((html.match(/class="group-enabled"/g)||[]).length, 1);
    assert.equal(html.includes("group-disabled-badge"), !enabled);
    assert.equal(/class="secondary edit-group"[^>]* disabled/.test(html), !enabled);
    assert.match(html, /aria-label="Edit Function Group &lt;Group&gt;"/);
    assert.match(html, /class="function-group-assignment"/);
    assert.match(html, /0 enabled · 1 disabled/);
    assert.match(html, /data-group-id="g&quot;"/);
    assert.equal(renderTools(owner), html);
    const dialogs = configurationDialogs(owner);
    assert.match(dialogs, /<ha-yaml-editor[^>]*hidden in-dialog/);
  }

  const rules = ["First", "Middle", "Last"].map((name, order) => ({id:String(order),name,order,phrases:[name],enabled:order !== 1,action_type:"local_action",action:{},match_type:"equals"}));
  const ownerRules = {...panel(), _result:{rules,diagnostics:{1:"<obsolete>"}}, _eocInPlaceRequestRuleSearch:true};
  Object.defineProperty(ownerRules, "_query", {get:()=>"Middle", set:()=>{throw new Error("Render mutated query state");}});
  html = renderRequestRules(ownerRules);
  assert.equal((html.match(/class="request-rule-card/g)||[]).length,3);
  assert.match(html, /id="rule-search"[^>]*value="Middle"/);
  assert.match(html, /Rule inactive:<\/strong> &lt;obsolete&gt;/);
  ownerRules._eocInPlaceRequestRuleSearch = false;
  html = renderRequestRules(ownerRules);
  assert.equal((html.match(/class="request-rule-card/g)||[]).length,1);
  assert.doesNotMatch(html, /class="secondary rule-move"[^>]* disabled/);
  const noMatchOwner = {...panel(), _result:{rules}, _query:"Nothing here", _eocInPlaceRequestRuleSearch:false};
  html = renderRequestRules(noMatchOwner);
  assert.match(html, /data-eoc-rule-search-empty ><h2>No rules match your search/);
  const emptyOwner = {...panel(), _result:{rules:[]}, _query:"", _eocInPlaceRequestRuleSearch:true};
  html = renderRequestRules(emptyOwner);
  assert.match(html, /id="rule-empty-add"/);
  assert.match(html, /data-eoc-rule-search-empty hidden/);
  assert.match(requestRulesDialog(), /id="rule-continue-to-ai"[^>]*checked/);
  assert.match(requestRulesDialog(), /id="rule-action-sequence-host"/);
  const guide = renderGuide({...panel(),_guideTopic:"backup-restore"});
  assert.match(guide, /id="guide-backup-restore" open/);
  assert.match(guide, /Treat captured requests as private/);
  assert.doesNotMatch(guide, /Request debugging and backups/);
} finally {
  delete globalThis.document;
}

// Cache hits stay useful, but drafts, assistants and refreshed data must miss.
const cached = panel({speech_processing_enabled:false});
cached._configSections = ["speech"];
const clean = renderConfiguration(cached);
const initialCache = cached._eocConfigRenderCache;
assert.equal(renderConfiguration(cached),clean);
assert.equal(cached._eocConfigRenderCache,initialCache);
cached._configDirty = true;
cached._draft.speech_processing_enabled = true;
assert.notEqual(renderConfiguration(cached),clean);
cached._configDirty = false;
cached._draft = {speech_processing_enabled:true};
assert.notEqual(renderConfiguration(cached),clean);
for (const change of [() => {cached._agentId="b";}, () => {cached._result={...cached._result};}, () => {cached._modelCatalogData={catalog_version:2};}, () => {cached._result.model_capabilities={};}]) {
  const previous = cached._eocConfigRenderCache;
  change();
  renderConfiguration(cached);
  assert.notEqual(cached._eocConfigRenderCache,previous);
}
cached._configSections = ["voice"];
cached._viewKey = () => "assistant/voice";
const voiceIdentity = (owner) => `<p>${owner._baseScopes?.[0]?.display_name || "No scope"}</p>`;
assert.match(renderConfiguration(cached, {voiceIdentity}), /No scope/);
cached._baseScopes = [{display_name:"Second assistant scope"}];
assert.match(renderConfiguration(cached, {voiceIdentity}), /Second assistant scope/);
assert.match(renderConfiguration(cached, {voiceIdentity:() => "<p>Updated voice renderer</p>"}), /Updated voice renderer/);
cached._configSections = ["capabilities"];
cached._viewKey = () => "capabilities/web-skills";
assert.doesNotMatch(renderConfiguration(cached), /data-config="knowledge_enabled"/);
cached._viewKey = () => "generic";
assert.match(renderConfiguration(cached), /data-config="knowledge_enabled"/);
console.log("Renderer composition state and no-reparse checks passed");
