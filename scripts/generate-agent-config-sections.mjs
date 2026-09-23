import {readFile, writeFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";

const baseUrl = new URL("../custom_components/extended_openai_conversation_responses/frontend/", import.meta.url);
const source = await readFile(new URL("agent-config-editor-base.js", baseUrl), "utf8");
const lines = source.split(/\r?\n/);
const sections = new Map();
for (const line of lines) {
  const id = /^\s+\$\{section\(panel,"([a-z]+)"/.exec(line)?.[1];
  if (id) {
    if (sections.has(id)) throw new Error(`Duplicate configuration section: ${id}`);
    sections.set(id, line.trim());
  }
}

const timeoutLine = lines.find((line) => line.trimStart().startsWith("const timeoutControl = "))?.trim();
if (!timeoutLine || sections.size !== 12) throw new Error("Configuration section source has changed");
const timeoutExpression = timeoutLine.replace(/^const timeoutControl = /, "").replace(/;$/, "");

const commonImport = `import {section, field, select, toggle, option, labelRow, settingSearch,\n`
  + `  configurationChoiceLabel, renderLocalHandling, regexRow, modelDataControls, helpButton}\n`
  + `  from "./agent-config-editor-base.js";\n`;

const groups = {
  primary: {
    ids: ["general", "conversation", "context", "model"],
    setup: `  const view = panel._viewKey?.();
  const config = panel._draft || panel._result?.config || {};
  const capabilities = panel._result?.model_capabilities || {};
  const choices = (key) => (panel._result?.options?.[key] || []).map((item) => ({...item, label: configurationChoiceLabel(key, item)}));
  const continuity = config.conversation_continuity !== "ha_default";
  const timeoutControl = () => {
    const timeoutChoices = choices("conversation_timeout_minutes");
    const timeoutPreset = timeoutChoices.some((item) => Number(item.value) === Number(config.conversation_timeout_minutes)) ? String(config.conversation_timeout_minutes) : "custom";
    return ${timeoutExpression};
  };
  const capabilityField = (capability, renderer) => capabilities[capability] === true ? renderer(false) : capabilities[capability] === false ? renderer(true) : "";
`,
    replacements: [["${timeoutControl}", "${timeoutControl()}"]],
  },
  capabilities: {
    ids: ["local", "capabilities", "archive"],
    setup: `  const view = panel._viewKey?.();
  const webSkillsOnly = view === "capabilities/web-skills";
  const config = panel._draft || panel._result?.config || {};
  const archive = config.archive_enabled;
  const choices = (key) => (panel._result?.options?.[key] || []).map((item) => ({...item, label: configurationChoiceLabel(key, item)}));
`,
  },
  media: {
    ids: ["prompt", "voice", "speech"],
    setup: `  const config = panel._draft || panel._result?.config || {};
  const regexRules = config.speech_regex_replacements || [];
  const speech = config.speech_processing_enabled;
  const choices = (key) => (panel._result?.options?.[key] || []).map((item) => ({...item, label: configurationChoiceLabel(key, item)}));
`,
  },
  maintenance: {
    ids: ["retention", "backup"],
    setup: `  const config = panel._draft || panel._result?.config || {};
  const choices = (key) => (panel._result?.options?.[key] || []).map((item) => ({...item, label: configurationChoiceLabel(key, item)}));
`,
  },
};

for (const [name, group] of Object.entries(groups)) {
  let body = group.ids.map((id) => {
    const line = sections.get(id);
    if (!line) throw new Error(`Missing configuration section: ${id}`);
    return group.replacements?.reduce((value, [before, after]) => value.replaceAll(before, after), line) || line;
  }).join("\n    ");
  const output = `// Generated from agent-config-editor-base.js by scripts/generate-agent-config-sections.mjs.\n`
    + commonImport
    + `export function renderConfigurationSections(panel, {voiceIdentity = null, renderExposedAttributes = null, renderBackup = null} = {}) {\n`
    + group.setup
    + `  return \`\n    ${body}\n  \`;\n}\n`;
  await writeFile(fileURLToPath(new URL(`agent-config-sections-${name}.js`, baseUrl)), output);
}
