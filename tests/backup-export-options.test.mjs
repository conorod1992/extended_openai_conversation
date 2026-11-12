import assert from "node:assert/strict";
import {bindExportOptions} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-shell.js";

const nodes = new Map();
for (const id of ["transfer-export-mode", "transfer-reviewed-custom", "transfer-export-description", "transfer-sharing-options", "transfer-custom-options", "transfer-custom-warning", "transfer-private-warning"]) {
  nodes.set(`#${id}`, Object.assign(new EventTarget(), {value:"setup", checked:false, hidden:false, textContent:""}));
}
const root = {querySelector: selector => nodes.get(selector)};
const mode = root.querySelector("#transfer-export-mode");
const custom = root.querySelector("#transfer-reviewed-custom");
const description = () => root.querySelector("#transfer-export-description").textContent;
const visible = id => !root.querySelector(`#transfer-${id}`).hidden;
bindExportOptions(root);
assert.match(description(), /designated credentials.*excluded by default/);
assert.equal(custom.checked, false);
assert.equal(visible("sharing-options"), true);
assert.equal(visible("custom-options"), false);
assert.equal(visible("private-warning"), false);
assert.equal(visible("custom-warning"), false);

for (const included of [true, false, true]) {
  custom.checked = included;
  custom.dispatchEvent(new Event("change"));
  assert.equal(visible("custom-warning"), included);
}
for (const value of ["full", "custom", "setup"]) {
  mode.value = value;
  mode.dispatchEvent(new Event("change"));
  assert.equal(visible("sharing-options"), value === "setup");
  assert.equal(visible("custom-options"), value === "custom");
  assert.equal(visible("private-warning"), value !== "setup");
  assert.equal(visible("custom-warning"), value === "setup");
  assert.equal(custom.checked, true, "mode switches preserve the custom-content choice");
  if (value === "full") assert.match(description(), /private configuration, credentials.*supported saved assistant data/);
  if (value === "custom") assert.equal(description(), "Choose which private sections to back up.");
}
bindExportOptions(root); // The transfer module activates without resetting the existing controls.
assert.equal(custom.checked, true);
assert.equal(visible("custom-warning"), true);
console.log("Export option descriptions, warnings and retained toggle state passed");
