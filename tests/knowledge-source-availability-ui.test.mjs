import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {knowledgeSourceAvailabilityBadge} from "../custom_components/extended_openai_conversation_responses/frontend/knowledge-presentation.js";

assert.match(knowledgeSourceAvailabilityBadge({enabled:true}), /availability-badge[^>]*>Available</);
assert.match(knowledgeSourceAvailabilityBadge({enabled:false}), /disabled-badge[^>]*>Unavailable</);
assert.match(knowledgeSourceAvailabilityBadge({}), /availability-badge[^>]*>Available/);

const source = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-dialogs.js", import.meta.url), "utf8") + await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-knowledge-feature.js", import.meta.url),
  "utf8",
);
assert.match(source, /id=\"knowledge-source-enabled\" type=\"checkbox\" role=\"switch\" checked/);
assert.match(source, /Available to the assistant/);
assert.match(source, /keep the source stored locally without including it in Knowledge retrieval/);
assert.doesNotMatch(source, /id=\"knowledge-enabled-toggle\"[^\n]*knowledge-source-enabled/);

assert.match(source, /_call\("knowledge", "set_enabled", \{enabled: desired\}\)/);
assert.doesNotMatch(source, /_call\("configuration", "get"\)/);
assert.doesNotMatch(source, /_call\("configuration", "validate"/);
assert.doesNotMatch(source, /_loadAgents\(panel\._agentId\)/);
assert.doesNotMatch(source, /_loadSection\(true\)/);

const {bindKnowledgeAvailability} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-knowledge-feature.js");
{
  let change;
  const input={checked:false, addEventListener:(_,handler)=>{change=handler;}};
  const panel={_agentId:"one", _loadToken:1, _viewKey:()=>"data-memory/knowledge",
    shadowRoot:{querySelector:()=>input}, _configData:{config:{prompt:"saved",knowledge_enabled:true},revision:"old"},
    _draft:{prompt:"unsaved",knowledge_enabled:true}, _draftTitle:"Unsaved name", _draftAgentId:"one", _configDirty:true,
    _call:async()=>({knowledge_enabled:false,revision:"new"}), _selectedAgent:()=>({}), _render:()=>{}, _toast:()=>{},
    _clearConfigDraft(){this._draft=null;this._configDirty=false;}, _syncConfigDirty(){}
  };
  bindKnowledgeAvailability(panel);
  await change();
  assert.equal(panel._draft?.prompt,"unsaved");
  assert.equal(panel._draftTitle,"Unsaved name");
  assert.equal(panel._draft.knowledge_enabled,false);
  assert.equal(panel._configData.config.knowledge_enabled,false);
  assert.equal(panel._configData.revision,"new");
  assert.equal(panel._configDirty,true);
}
