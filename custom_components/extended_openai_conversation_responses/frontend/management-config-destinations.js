import {SETTINGS_INDEX} from "./frontend-navigation.js";

const EXTRA_CONFIG_OWNERS = Object.freeze({
  functions: ["capabilities", "functions"],
  function_groups: ["capabilities", "functions"],
});

const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

const SETTING_OWNER_BY_KEY = new Map();
for (const item of SETTINGS_INDEX) {
  if (item.configKey && !SETTING_OWNER_BY_KEY.has(item.configKey)) {
    SETTING_OWNER_BY_KEY.set(item.configKey, [item.page, item.section]);
  }
}

function ownerForKey(key) {
  return SETTING_OWNER_BY_KEY.get(key) || EXTRA_CONFIG_OWNERS[key] || null;
}

export function dirtyConfigurationKeys(panel) {
  if (!panel?._configData?.config || !panel?._draft || panel._draftAgentId !== panel._agentId) return new Set();
  if (panel._eocDirtyConfigKeys instanceof Set) return new Set(panel._eocDirtyConfigKeys);
  const baseline = panel._configData.config;
  const draft = panel._draft;
  const keys = new Set([...Object.keys(baseline), ...Object.keys(draft)]);
  const changed = new Set([...keys].filter((key) => !same(baseline[key], draft[key])));
  if (panel._draftTitle !== panel._configData.title) changed.add("__title");
  return changed;
}

export function configurationDestinations(panel) {
  const destinations = new Set();
  const unknown = [];
  for (const key of dirtyConfigurationKeys(panel)) {
    const owner = ownerForKey(key);
    if (owner) destinations.add(`${owner[0]}/${owner[1]}`);
    else unknown.push(key);
  }
  if (unknown.length && panel?._configDirty && panel?._page && panel?._subsection) {
    destinations.add(`${panel._page}/${panel._subsection}`);
  }
  return destinations;
}

export function dirtyConfigurationDestinations(panel) {
  return new Set([
    ...configurationDestinations(panel),
    ...(panel?._unsavedState?.destinations() || []),
  ]);
}
