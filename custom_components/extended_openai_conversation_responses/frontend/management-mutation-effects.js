// Frontend command knowledge lives here; backend authorization remains independent.
const mutation = Object.freeze({});
const configuration = Object.freeze({configuration:true});
const configurationAndRules = Object.freeze({configuration:true, requestRules:true});
const tools = Object.freeze({...configurationAndRules, revision:"tools"});
const rules = Object.freeze({revision:"request_rules", collectionView:"capabilities/request-rules"});
const knowledge = Object.freeze({collectionView:"data-memory/knowledge"});
const memories = Object.freeze({scopeCatalog:true});

const commands = {
  backup: {restore:Object.freeze({configuration:true, restore:true})},
  quiet_hours: {update:mutation},
  configuration: {update:configurationAndRules, save:configurationAndRules, import:configurationAndRules},
  function_repair: {save:configurationAndRules, save_one:configurationAndRules, delete_one:configurationAndRules, configuration_save:configurationAndRules},
  conversations: {delete:mutation, end_active:mutation},
  guest_mode: {save_policy:configuration, update:mutation, disable:mutation},
  knowledge: {create:knowledge, update:knowledge, delete:knowledge, set_enabled:Object.freeze({...knowledge, configuration:true})},
  memories: {add:memories, update:memories, delete:memories, clear:memories, temporary_add:memories, temporary_delete:memories, temporary_update:memories, temporary_clear:memories, reassign_legacy:memories},
  request_rules: {settings:rules, defaults:rules, wording_groups:rules, groups:rules, create:rules, update:rules, delete:rules, duplicate:rules, move:Object.freeze({...rules, requestRules:true}), rule_pack_import:rules},
  settings: {update:configuration},
  tools: {save:tools, delete:tools, set_enabled:tools, save_group:tools, delete_group:tools, ha_add:tools},
  usage: {clear_details:Object.freeze({usageWindows:true})},
};
for (const actions of Object.values(commands)) Object.freeze(actions);
Object.freeze(commands);

// Only declared panel mutations participate here; other commands retain their
// existing feature-owned behavior. Never inherit object keys.
export function mutationEffects(section, action) {
  if (typeof section !== "string" || typeof action !== "string" || !Object.hasOwn(commands, section)) return null;
  const actions = commands[section];
  return Object.hasOwn(actions, action) ? actions[action] : null;
}

export function isAgentMutation(section, action) {
  return mutationEffects(section, action) !== null;
}
