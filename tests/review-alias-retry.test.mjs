import assert from "node:assert/strict";
import {bindRequestRuleEditor} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";

class Field extends EventTarget {
  value = "";
  checked = false;
  disabled = false;
  hidden = false;
  ownerDocument = {createElement: () => new Field()};
  replaceChildren(child) { this.child = child; }
  close() { this.closed = true; }
  contains() { return false; }
}

const step = (id, alias) => ({action:"extended_openai_conversation_responses.call_function", data:{function:"lookup", arguments:{}, step_id:id, result_alias:alias}});
for (const swap of [true, false]) {
  const fields = new Map();
  const q = selector => {
    if (!fields.has(selector)) fields.set(selector, new Field());
    return fields.get(selector);
  };
  const submitted = [];
  const panel = {
    shadowRoot:{querySelector:q, querySelectorAll:() => []}, _editingRuleId:"rule",
    _result:{rules:[{id:"rule", action:{actions:[step("one", "left"), step("two", "right")]}}]},
    _setSaving(button, saving) { button.disabled = saving; },
    async _call(section, action, message) {
      submitted.push(structuredClone(message.rule));
      throw new Error("Simulated save failure");
    },
  };
  bindRequestRuleEditor(panel);
  const actions = [step("one", swap ? "right" : "renamed"), step("two", swap ? "left" : "right"), {action:"review_probe.record", data:{message:"{left.value}"}}];
  panel._eocRuleEditorState.actionSelector.value = actions;
  q("#rule-action-type").value = "local_action";
  q("#rule-ai-input-mode").value = "original";
  q("#rule-phrases").value = "run";
  q("#rule-success").value = "First result: {left.value}";
  for (let attempt = 0; attempt < 3; attempt++) {
    q("#rule-form").dispatchEvent(new Event("submit", {cancelable:true}));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(q("#rule-success").value, "First result: {left.value}");
    assert.deepEqual(panel._eocRuleEditorState.actionSelector.value, actions);
  }
  assert.equal(submitted.length, 3);
  for (const rule of submitted) {
    const reference = swap ? "{right.value}" : "{renamed.value}";
    assert.equal(rule.action.success_response, `First result: ${reference}`);
    assert.equal(rule.action.actions[2].data.message, reference);
    assert.deepEqual(rule, submitted[0]);
  }
}
