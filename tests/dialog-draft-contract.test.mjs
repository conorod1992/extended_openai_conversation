import assert from "node:assert/strict";
import test from "node:test";
import {dialogState, editorControls} from "../custom_components/extended_openai_conversation_responses/frontend/management-dialogs.js";
import {dialogHasUnsavedChanges, openDialogBaseline} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";

function dialog(controls) {
  return {id:"group-dialog", querySelectorAll:() => controls};
}

test("save comparison and dismissal use the same authored controls", () => {
  const text = {id:"group-name", type:"text", value:" Group ", checked:false};
  const member = {name:"member", type:"checkbox", checked:true, value:"tool"};
  const search = {id:"group-function-search", type:"search", value:""};
  const controls = [text, member, search, ...["button", "submit", "reset", "file"].map(type => ({type}))];
  const group = dialog(controls), panel = {};
  openDialogBaseline(panel, group);
  const authored = dialogState(group), baseline = panel._eocDialogBaselines.get(group.id);
  assert.deepEqual(authored, [
    {id:"group-name", type:"text", value:" Group ", checked:false},
    {id:"member", type:"checkbox", checked:true, value:"tool"},
  ]);
  assert.deepEqual(editorControls(group), [text, member]);
  search.value = "a changed filter is not an authored membership edit";
  assert.deepEqual(dialogState(group), authored);
  assert.equal(dialogHasUnsavedChanges(group, baseline), false);
  member.checked = false;
  assert.equal(dialogHasUnsavedChanges(group, authored), true);
  member.checked = true;
  text.value = "Group";
  assert.equal(dialogHasUnsavedChanges(group, authored), true, "raw whitespace belongs to the pending submission");
});

test("native selector values are detached and invalid YAML remains recovery data", () => {
  const selector = {id:"native", tagName:"HA-SELECTOR", value:{entity:["light.one"]}, __eocYamlDraft:() => ["invalid: ["]};
  const state = dialogState(dialog([selector]));
  assert.deepEqual(state, [{id:"native", type:"HA-SELECTOR", checked:undefined, value:{entity:["light.one"]}, yaml:["invalid: ["]}]);
  selector.value.entity.push("light.two");
  assert.deepEqual(state[0].value, {entity:["light.one"]});
});

test("native draft readers keep their own raw representation", () => {
  const raw = {json:"{", yaml:"sequence: ["};
  const editor = {__eocReadDraft:() => raw, querySelectorAll() {throw Error("native editor must supply its own draft");}};
  assert.equal(dialogState(editor), raw);
  assert.deepEqual(dialogState(null), []);
});
