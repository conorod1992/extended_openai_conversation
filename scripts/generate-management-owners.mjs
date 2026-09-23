import {writeFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import {SETTINGS_INDEX} from "../custom_components/extended_openai_conversation_responses/frontend/management-settings-index.js";

const owners = {};
for (const {configKey, page, section} of SETTINGS_INDEX) {
  if (configKey && !(configKey in owners)) owners[configKey] = `${page}/${section}`;
}

const output = fileURLToPath(new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/management-config-owners.js",
  import.meta.url,
));
await writeFile(output, `// Generated from SETTINGS_INDEX by scripts/generate-management-owners.mjs.\n`
  + `export const CONFIG_OWNER_BY_KEY = Object.freeze(${JSON.stringify(owners, null, 2)});\n`);

const modelParameters = SETTINGS_INDEX
  .filter((item) => item.page === "assistant" && item.section === "model-responses" && item.capability)
  .map((item) => ({key: item.configKey, capability: item.capability}));
await writeFile(fileURLToPath(new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/management-model-parameters.js",
  import.meta.url,
)), `// Generated from SETTINGS_INDEX by scripts/generate-management-owners.mjs.\n`
  + `export const MODEL_PARAMETERS = Object.freeze(${JSON.stringify(modelParameters, null, 2)});\n`);

const settingLookup = {};
for (const item of SETTINGS_INDEX) {
  if (item.configKey && !(item.configKey in settingLookup)) {
    settingLookup[item.configKey] = {
      label: item.label,
      aliases: `${item.label || ""} ${item.description || ""} ${item.terms || ""} ${item.configKey || ""}`.toLowerCase(),
    };
  }
}
await writeFile(fileURLToPath(new URL(
  "../custom_components/extended_openai_conversation_responses/frontend/management-setting-lookup.js",
  import.meta.url,
)), `// Generated from SETTINGS_INDEX by scripts/generate-management-owners.mjs.\n`
  + `export const SETTING_LOOKUP = Object.freeze(${JSON.stringify(settingLookup, null, 2)});\n`);
