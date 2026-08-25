import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {freshGuestPolicyDraft, GUEST_EXCLUSION_KEYS} from "../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js";

const panel = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url),
  "utf8",
);
const editor = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url),
  "utf8",
);

assert.match(panel, /capabilities\/guest-mode/);
assert.match(panel, /guest_mode", "update"/);
assert.match(panel, /guest_mode", "disable"/);
assert.match(panel, /Voice and model safety/);
assert.match(panel, /Capability safety/);
assert.match(panel, /side effects cannot be safely limited/);
assert.match(panel, /This is intentional/);
assert.match(panel, /Allow the assistant to activate Guest Mode/);
assert.match(panel, /guest_excluded_entities/);
assert.match(panel, /guest_control_excluded_entities/);
assert.match(panel, /guest_knowledge_source_ids/);
assert.match(panel, /guest_allowed_function_names/);
assert.match(panel, /guest_shared_memory_policy/);
assert.match(panel, /ha-selector/);
assert.match(panel, /save_policy/);
assert.match(panel, /"Labels"/);
assert.match(panel, /"Areas"/);
assert.match(panel, /"Domains"/);
assert.match(panel, /"Individual entities"/);
assert.ok(panel.indexOf('"Labels"') < panel.indexOf('"Areas"'));
assert.ok(panel.indexOf('"Areas"') < panel.indexOf('"Domains"'));
assert.ok(panel.indexOf('"Domains"') < panel.indexOf('"Individual entities"'));
assert.match(panel, /Make some visible entities read-only/);
assert.match(panel, /Previous Guest Mode settings found/);
assert.match(panel, /Review converted settings/);
assert.match(panel, /Start fresh/);
assert.match(panel, /old policy remains enforced until you save/i);
assert.match(panel, /Labels are usually the easiest way to manage Guest access/);
assert.match(panel, /Normally, anything a guest can see can also be controlled/);
assert.match(panel, /\(config\[key\] \|\| \[\]\)\.length} excluded/);
assert.match(panel, /class="guest-manager"/);
assert.match(panel, /class="manage-label">Manage/);
assert.match(panel, /config\.guest_separate_control_restrictions \? `<div class="guest-managers">/);
assert.match(panel, /All current Knowledge sources are available to guests. New sources added later will also be included/);
assert.match(panel, /All eligible enabled functions are available to guests. New eligible functions added later will also be included/);
assert.match(panel, /filter\(\(item\) => !item\.unsafe_in_guest_mode\)/);
assert.match(panel, /The assistant can never shorten or disable Guest Mode/);
assert.match(panel, /<summary>Advanced<\/summary><label class="toggle"><span>Allow the assistant to activate Guest Mode/);
assert.match(panel, /top:50%/);
assert.match(panel, /translateY\(-50%\)/);
assert.doesNotMatch(editor, /Guest Mode policy/);
assert.doesNotMatch(editor, /group-guest-allowed/);

const legacyDraft = {guest_excluded_entities:["light.private"], guest_mode_enabled:false, unrelated:"kept"};
const freshDraft = freshGuestPolicyDraft(legacyDraft);
assert.equal(legacyDraft.guest_excluded_entities.length, 1, "start fresh must not mutate the enforced legacy snapshot");
assert.ok(GUEST_EXCLUSION_KEYS.every((key) => freshDraft[key].length === 0));
assert.equal(freshDraft.guest_mode_enabled, true);
assert.equal(freshDraft.guest_knowledge_policy, "off");
assert.equal(freshDraft.guest_function_policy, "off");
assert.equal(freshDraft.guest_shared_memory_policy, "off");
assert.equal(freshDraft.unrelated, "kept");
