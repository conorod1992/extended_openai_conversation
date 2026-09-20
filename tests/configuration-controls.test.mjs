import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {CONFIGURATION_CONTROLS, configurationControlValue, applyConfigurationControl, readConfigurationDraft} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-controls.js";

const cases = [
  [{dataset:{config:"skills"}, value:"weather, indoor\r\n calendar\n\nlocal notes "}, ["weather, indoor","calendar","local notes"]],
  [{dataset:{config:"guest_readable_entities"}, value:"light.one, light.two ,"}, ["light.one","light.two"]],
  [{dataset:{config:"web_search",type:"boolean"}, checked:false, value:"on"}, false],
  [{dataset:{memoryConfig:"memory_auto_retrieve_limit",type:"number"}, value:"3"}, 3],
  [{dataset:{config:"temperature",type:"number"}, value:"0"}, 0],
  [{dataset:{config:"prompt"}, value:"  exact prompt\n"}, "  exact prompt\n"],
  [{dataset:{config:"__title"}, value:"New title"}, "New title"],
  [{dataset:{}, id:"voice-mappings", value:'{"device":"user"}'}, {device:"user"}],
  [{dataset:{}, id:"voice-mappings", value:'{unfinished'}, '{unfinished'],
];
for (const [control, expected] of cases) {
  assert.deepEqual(configurationControlValue(control), expected);
  const initial = {unrelated:{retained:true}};
  const incremental = {_draft:structuredClone(initial), _draftTitle:"Old"};
  const full = {_draft:structuredClone(initial), _draftTitle:"Old", shadowRoot:{querySelector:() => null, querySelectorAll:(selector) => { assert.equal(selector, CONFIGURATION_CONTROLS); return [control]; }}};
  const original = incremental._draft;
  assert.equal(applyConfigurationControl(incremental, control).changed, true);
  assert.equal(applyConfigurationControl(incremental, control).changed, false, "input + change must not assign twice");
  readConfigurationDraft(full);
  assert.equal(incremental._draft, original);
  assert.deepEqual(incremental._draft, full._draft);
  assert.equal(incremental._draftTitle, full._draftTitle);
}

const exclusions = {_draft:{local_intent_exclusions:["B","A"]}, _configData:{config:{local_intent_exclusions:["B","A"]}}};
const choice = {dataset:{localIntentExclusion:""}, type:"checkbox", value:"B", checked:false, matches:(selector) => selector === "[data-local-intent-exclusion]"};
applyConfigurationControl(exclusions, choice);
assert.deepEqual(exclusions._draft.local_intent_exclusions, ["A"]);
choice.checked = true;
applyConfigurationControl(exclusions, choice);
assert.deepEqual(exclusions._draft.local_intent_exclusions, ["B","A"]);

const rules = [{pattern:"a",replacement:"b"}, {pattern:"c",replacement:"d"}];
const regex = {_draft:{speech_regex_replacements:rules}};
const input = {dataset:{}, value:"edited", matches:(selector) => selector === ".regex-pattern,.regex-replacement", classList:{contains:(name) => name === "regex-pattern"}, closest:() => ({dataset:{regexIndex:"0"}})};
assert.deepEqual(applyConfigurationControl(regex, input), {key:"speech_regex_replacements", changed:true});
assert.equal(regex._draft.speech_regex_replacements, rules);
assert.deepEqual(rules, [{pattern:"edited",replacement:"b"}, {pattern:"c",replacement:"d"}]);
input.closest = () => ({dataset:{regexIndex:"-1"}});
assert.equal(applyConfigurationControl(regex, input), null);

for (const file of ["configuration-inputs.js", "agent-config-editor-model-v2.js", "management-renderer.js"]) {
  const source = await readFile(new URL(`../custom_components/extended_openai_conversation_responses/frontend/${file}`, import.meta.url), "utf8");
  assert.doesNotMatch(source, /stopImmediatePropagation/, `${file} must not suppress competing configuration handlers`);
}
