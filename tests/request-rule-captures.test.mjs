import assert from "node:assert/strict";
import {capturedSlotNames, refreshRequestRuleSlotSelectors} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";

assert.deepEqual(capturedSlotNames("[please] set {room=kitchen|bedroom} to {level=0..100}"), ["room", "level"]);
assert.deepEqual(capturedSlotNames("remember {fact}\nremember {fact}"), ["fact"]);
assert.deepEqual(capturedSlotNames(String.raw`say \{literal\} {choice=a\|b|c\}d} {amount=-10..10}`), ["choice", "amount"]);
assert.deepEqual(capturedSlotNames("set { room =kitchen|bedroom} {not-finished"), ["room"]);
assert.deepEqual(capturedSlotNames(String.raw`set {choice={fake}|other}`), ["choice"]);

// Switching to constraints and reopening them must retain the existing binding.
const select = {
  value: "room", ownerDocument: {createElement: () => ({})},
  replaceChildren(...options) { this.options = options; },
};
const root = {querySelectorAll: () => [select]};
for (const source of ["set {room}", "set {room=kitchen|bedroom}", "set {room=0..100}"]) {
  refreshRequestRuleSlotSelectors(root, capturedSlotNames(source));
  assert.equal(select.value, "room");
  assert.deepEqual(select.options.map((option) => option.value), ["", "room"]);
}
