import assert from "node:assert/strict";
import {bindExportOptions} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-shell.js";

const nodes = new Map();
for (const id of ["transfer-export-mode", "transfer-export-description", "transfer-custom-options", "transfer-private-warning"]) {
  nodes.set(`#${id}`, Object.assign(new EventTarget(), {value:"setup", checked:false, hidden:false, textContent:""}));
}
const root = {querySelector: selector => nodes.get(selector)};
const mode = root.querySelector("#transfer-export-mode");
const description = () => root.querySelector("#transfer-export-description").textContent;
const visible = id => !root.querySelector(`#transfer-${id}`).hidden;
bindExportOptions(root);
assert.match(description(), /provider credentials and custom content are omitted/);
assert.equal(visible("custom-options"), false);
assert.equal(visible("private-warning"), false);

for (const value of ["full", "custom", "setup"]) {
  mode.value = value;
  mode.dispatchEvent(new Event("change"));
  assert.equal(visible("custom-options"), value === "custom");
  assert.equal(visible("private-warning"), value !== "setup");
  if (value === "full") assert.match(description(), /private configuration, credentials.*supported saved assistant data/);
  if (value === "custom") assert.equal(description(), "Choose which private sections to back up.");
}
bindExportOptions(root); // The transfer module activates without resetting the existing controls.
console.log("Export option descriptions and private backup warnings passed");
