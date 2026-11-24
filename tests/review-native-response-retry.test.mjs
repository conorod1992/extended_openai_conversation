import assert from "node:assert/strict";
import {bindRequestRuleEditor} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";
import {captureConfigurationOwner} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";

class Field extends EventTarget {
  value = "";
  checked = false;
  disabled = false;
  hidden = false;
  ownerDocument = {createElement: () => new Field()};
  replaceChildren(child) { this.child = child; }
  querySelectorAll() { return []; }
  close() { this.closed = true; }
  contains() { return false; }
}

const step = id => ({action:"extended_openai_conversation_responses.call_function", data:{function:"lookup", arguments:{}}, response_variable:"native_" + id});
for (const repair of [true, false]) {
  const fields = new Map();
  const q = selector => {
    if (!fields.has(selector)) fields.set(selector, new Field());
    return fields.get(selector);
  };
  const submitted = [];
  const panel = {
    shadowRoot:{querySelector:q, querySelectorAll:() => []}, _editingRuleId:"rule",
    _result:{rules:[{id:"rule"}], diagnostics:repair ? {rule:"Function response format changed. Review the response variable."} : {}},
    _setSaving(button, saving) { button.disabled = saving; },
    async _call(section, action, message) {
      submitted.push(structuredClone(message));
      throw new Error("Simulated save failure");
    },
  };
  bindRequestRuleEditor(panel);
  // Model the opening contract while this fixture exercises a failed save.
  panel._eocRuleEditorState.owner = captureConfigurationOwner(panel);
  q("#rule-dialog").open = true;
  const actions = [step("one"), step("two"), {set_conversation_response:"{{ native_one.result.value }}"}];
  panel._eocRuleEditorState.actionSelector.value = actions;
  q("#rule-action-type").value = "local_action";
  q("#rule-matching-repair").hidden = true;
  q("#rule-ai-input-mode").value = "original";
  q("#rule-phrases").value = "run";
  q("#rule-success").value = "Finished {device}";
  if (repair) {
    q("#rule-form").dispatchEvent(new Event("submit", {cancelable:true}));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(submitted.length, 0);
    assert.match(q("#rule-error").textContent, /Confirm that you reviewed/);
    q("#rule-native-response-reviewed").checked = true;
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    q("#rule-form").dispatchEvent(new Event("submit", {cancelable:true}));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(q("#rule-success").value, "Finished {device}");
    assert.deepEqual(panel._eocRuleEditorState.actionSelector.value, actions);
  }
  assert.equal(submitted.length, 3);
  for (const message of submitted) {
    assert.equal(message.confirm, repair ? true : undefined);
    assert.equal(message.rule.action.success_response, "Finished {device}");
    assert.deepEqual(message.rule.action.actions, actions);
    assert.deepEqual(message, submitted[0]);
  }
}
