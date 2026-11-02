import assert from "node:assert/strict";
import {ExtendedOpenAIManagementPanel as Panel} from "./management-ownership-harness.mjs";
import {TestEventTarget} from "./frontend-test-helpers.mjs";
import {renderGroupCollectionRepair, bindGroupCollectionRepair} from "../custom_components/extended_openai_conversation_responses/frontend/function-group-collection-repair.js";

let input="";
for await (const chunk of process.stdin) input+=chunk;
const probe=JSON.parse(input), clone=structuredClone;
const panel=new Panel(), controls=new Map();
const element = properties => Object.assign(new TestEventTarget(), {isConnected:true}, properties);
const editor=element({value:probe.payload.groups});
const guests=element({value:JSON.stringify(probe.payload.guest_allowed_group_ids)});
const button=element({disabled:false});
controls.set("#group-collection-editor",editor);
controls.set("#group-collection-guests",guests);
controls.set("#group-collection-save",button);
controls.set("#group-collection-discard",element({disabled:false}));
controls.set("#group-collection-error",{textContent:""});
Object.assign(panel, {
  isConnected:true, _agentId:"agent-1", _draftAgentId:"agent-1",
  _page:"capabilities", _subsection:"functions", _scopeId:"user:admin",
  _data:{is_admin:true,agents:[{entry_id:"entry-1",subentry_id:"agent-1"}]},
  _configData:clone(probe.before), _result:clone(probe.before),
  _draft:clone(probe.before.config), _draftTitle:probe.before.title,
  _render() {if(probe.outcome === "refresh_failure") throw Error("Optional renderer failed");},
  toasts:[], _toast(message,warning) {this.toasts.push({message,warning});},
});
panel._draft.prompt="A separately authored prompt remains unsaved";
panel.shadowRoot.querySelector = selector => controls.get(selector) || null;
panel.shadowRoot.querySelectorAll = () => [];
let sends=0;
panel._hass={user:{id:"admin"},callWS:async request => {
  sends++;
  assert.equal(request.section,"function_repair");
  assert.equal(request.action,"save_groups");
  for (const [key,value] of Object.entries(probe.payload)) assert.deepEqual(request[key],value);
  // The real Python producer has already verified whether the write committed.
  if(probe.outcome === "lost") throw {code:3,message:"Connection lost after write"};
  if(!probe.envelope.success) throw clone(probe.envelope.error);
  const response=clone(probe.envelope.result);
  if(probe.outcome === "incomplete") delete response.function_groups;
  return response;
}};
const originalFunctions=clone(panel._configData.config.functions);
renderGroupCollectionRepair(panel);
bindGroupCollectionRepair(panel);
await button.emit("click");
assert.equal(sends,1);
assert.equal(editor.value,probe.payload.groups);
assert.equal(guests.value,JSON.stringify(probe.payload.guest_allowed_group_ids));
assert.equal(panel._draft.prompt,"A separately authored prompt remains unsaved");
assert.deepEqual(panel._configData.config.functions,originalFunctions);
if(["lost","incomplete"].includes(probe.outcome)) {
  assert.match(controls.get("#group-collection-error").textContent,/outcome is unknown/);
  assert.equal(panel._configData.revision,probe.before.revision);
  await button.emit("click");
  assert.equal(sends,1,"the actual mutation lane blocks another write until a fresh read");
} else if(probe.outcome === "rejected") {
  assert.equal(panel._configData.revision,probe.before.revision);
  assert.match(controls.get("#group-collection-error").textContent,/unknown function/);
} else {
  const saved=probe.envelope.result;
  assert.equal(panel._configData.revision,saved.revision);
  assert.deepEqual(panel._configData.config.function_groups,saved.function_groups);
  assert.deepEqual(panel._configData.config.guest_allowed_group_ids,[]);
  assert.deepEqual(panel._groupCollectionDraft.baseline,{groups:editor.value,guests:guests.value});
  if(probe.outcome === "refresh_failure") assert.match(panel.toasts.at(-1).message,/Function Groups repaired, but .*could not be refreshed/);
  else assert.equal(panel.toasts.at(-1).message,"Function Groups repaired");
}
