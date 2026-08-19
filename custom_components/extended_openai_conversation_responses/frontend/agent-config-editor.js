import { bindHelp, helpButton, helpPopover, helpSearchTerms } from "./agent-config-help.js";

const clone = (value) => JSON.parse(JSON.stringify(value));
const bool = (value) => value ? "checked" : "";
const option = (panel, value, selected, label = null) => `<option value="${panel._e(value)}" ${value === selected ? "selected" : ""}>${panel._e(label || panel._titleCase(value))}</option>`;
const CHOICE_LABELS = Object.freeze({
  conversation_continuity: Object.freeze({
    ha_default: "Use Home Assistant sessions",
    device: "Remember by voice device",
    user: "Remember by user across devices",
  }),
  voice_scope_policy: Object.freeze({
    unretained: "Do not retain personal data",
    shared: "Use shared household data",
    default_user: "Use the default user",
    device_mapping: "Use a device-to-user mapping",
  }),
  voice_unmapped_policy: Object.freeze({
    unretained: "Do not retain personal data",
    shared: "Use shared household data",
    default_user: "Use the default user",
    device_mapping: "Device mapping (no retained data)",
  }),
});
export const configurationChoiceLabel = (key, item) => CHOICE_LABELS[key]?.[item.value] || item.label;
const settingSearch = (label, description, key, helpKey = null) => `${label} ${description} ${key} ${helpKey ? helpSearchTerms(helpKey) : ""}`.toLowerCase();
const labelRow = (panel, label, key, helpKey = null, strong = false) => `<span class="setting-label-row"><label for="config-${key}">${strong ? `<strong>${label}</strong>` : label}</label>${helpKey ? helpButton(panel, helpKey) : ""}</span>`;
const field = (panel, key, label, value, type = "text", description = "", disabled = false, helpKey = null) => `<div class="setting" data-field="${key}" data-setting data-search="${panel._e(settingSearch(label, description, key, helpKey))}">${labelRow(panel, label, key, helpKey)}<input id="config-${key}" data-config="${key}" data-type="${type}" type="${type === "number" ? "number" : "text"}" value="${panel._e(value)}" ${disabled ? "disabled" : ""}>${description ? `<small>${description}</small>` : ""}<span class="field-error" data-error="${key}"></span></div>`;
const select = (panel, key, label, value, options, description = "", disabled = false, helpKey = null) => `<div class="setting" data-field="${key}" data-setting data-search="${panel._e(settingSearch(label, description, key, helpKey))}">${labelRow(panel, label, key, helpKey)}<select id="config-${key}" data-config="${key}" ${disabled ? "disabled" : ""}>${options.map((item) => option(panel, typeof item === "string" ? item : item.value, value, typeof item === "string" ? null : item.label)).join("")}</select>${description ? `<small>${description}</small>` : ""}<span class="field-error" data-error="${key}"></span></div>`;
const toggle = (panel, key, label, value, description = "", disabled = false, helpKey = null) => `<div class="config-toggle setting" data-field="${key}" data-setting data-search="${panel._e(settingSearch(label, description, key, helpKey))}"><span class="setting-copy">${labelRow(panel, label, key, helpKey, true)}${description ? `<small>${description}</small>` : ""}</span><label class="switch-control" for="config-${key}"><input id="config-${key}" data-config="${key}" data-type="boolean" type="checkbox" role="switch" ${bool(value)} ${disabled ? "disabled" : ""}><span class="switch-track" aria-hidden="true"></span></label></div>`;

export async function copyTextToClipboard(text, clipboardNavigator = globalThis.navigator, clipboardDocument = globalThis.document) {
  const writeText = clipboardNavigator?.clipboard?.writeText;
  if (typeof writeText === "function") {
    try {
      await writeText.call(clipboardNavigator.clipboard, text);
      return;
    } catch (err) {
      if (!clipboardDocument) throw err;
    }
  }
  if (!clipboardDocument?.body || typeof clipboardDocument.createElement !== "function" || typeof clipboardDocument.execCommand !== "function") {
    throw new Error("Clipboard access is unavailable in this browser context");
  }
  const textarea = clipboardDocument.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  clipboardDocument.body.appendChild(textarea);
  try {
    textarea.select();
    if (!clipboardDocument.execCommand("copy")) throw new Error("Browser copy command failed");
  } finally {
    textarea.remove();
  }
}

export const functionGroupIdFromName = (name) => String(name || "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").replace(/^[^a-z]+/, "").slice(0, 64);
export const isFunctionToolEnabled = (tool) => tool?.enabled !== false;

export function functionToolCountLabel(tools = []) {
  const enabled = tools.filter(isFunctionToolEnabled).length;
  const disabled = tools.length - enabled;
  if (!disabled) return `${enabled} ${enabled === 1 ? "function" : "functions"}`;
  return `${enabled} enabled · ${disabled} disabled`;
}

export function backupSummaryLines(summary = {}) {
  return [
    "Agent configuration",
    `${Number(summary.persistent_memories || 0)} persistent memories`,
    `${Number(summary.temporary_memories || 0)} active temporary memories`,
    `${Number(summary.knowledge_sources || 0)} Knowledge sources`,
    `${Number(summary.archive_sessions || 0)} archived conversations (${Number(summary.archive_turns || 0)} turns)`,
    `Usage history (${Number(summary.usage_runs || 0)} runs, ${Number(summary.usage_requests || 0)} requests)`,
  ];
}

export const canReplaceToolYamlWithoutConfirmation = (current, replaceable) => !String(current || "").trim() || current === replaceable;

const searchTokens = (value) => String(value || "")
  .normalize("NFKD")
  .replace(/[\u0300-\u036f]/g, "")
  .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, " ")
  .trim()
  .split(/\s+/)
  .filter(Boolean)
  .map((token) => {
    if (token.length > 4 && token.endsWith("ies")) return `${token.slice(0, -3)}y`;
    if (token.length > 4 && /(ches|shes|sses|xes|zes)$/.test(token)) return token.slice(0, -2);
    if (token.length > 3 && token.endsWith("s") && !token.endsWith("ss")) return token.slice(0, -1);
    return token;
  });

export function matchesFunctionSearch(query, searchableText) {
  const queryTokens = searchTokens(query);
  if (!queryTokens.length) return true;
  const textTokens = searchTokens(searchableText);
  return queryTokens.every((queryToken) => textTokens.some((textToken) =>
    textToken === queryToken || (
      Math.min(textToken.length, queryToken.length) >= 3
      && (textToken.startsWith(queryToken) || queryToken.startsWith(textToken))
    )
  ));
}

export function deleteFunctionGroup(config, groupId) {
  const result = clone(config);
  result.function_groups = (result.function_groups || []).filter((group) => group.id !== groupId);
  return result;
}

export function categorizeFunctionTools(config) {
  const tools = config.functions || [];
  const groups = config.function_groups || [];
  const assigned = new Set(groups.flatMap((group) => group.functions || []));
  return {
    alwaysAvailable: tools.filter((tool) => !assigned.has(tool.spec?.name)),
    groups: groups.map((group) => ({...group, tools: tools.filter((tool) => (group.functions || []).includes(tool.spec?.name))})),
  };
}

function section(panel, id, title, description, keywords, body) {
  return `<section id="config-${id}" class="config-section" data-config-section data-search="${panel._e(`${title} ${description} ${keywords}`.toLowerCase())}"><div class="config-section-heading"><p class="eyebrow">${title}</p><p>${description}</p></div>${body}</section>`;
}

function saveBar(panel) {
  return `<div class="save-bar"><span id="dirty-state" class="dirty-state">${panel._configDirty ? "Unsaved changes" : "All changes saved"}</span><div class="actions"><button type="button" class="secondary" id="revert-config" ${panel._configDirty ? "" : "disabled"}>Revert</button><button type="button" id="save-config" ${panel._configDirty ? "" : "disabled"}>Save configuration</button></div></div>`;
}

export function renderConfiguration(panel) {
  const config = panel._draft || panel._result?.config || {};
  const capabilities = panel._result?.model_capabilities || {};
  const defaults = panel._result?.defaults || {};
  const regexRules = config.speech_regex_replacements || [];
  const archive = config.archive_enabled;
  const speech = config.speech_processing_enabled;
  const choices = (key) => (panel._result?.options?.[key] || []).map((item) => ({...item, label: configurationChoiceLabel(key, item)}));
  const cleanOnly = panel._configDirty ? "disabled title=\"Save or revert the shared draft first\"" : "";
  const continuity = config.conversation_continuity !== "ha_default";
  const timeoutChoices = choices("conversation_timeout_minutes");
  const timeoutPreset = timeoutChoices.some((item) => Number(item.value) === Number(config.conversation_timeout_minutes)) ? String(config.conversation_timeout_minutes) : "custom";
  const timeoutControl = `<div class="setting" data-field="conversation_timeout_minutes" data-setting data-search="${panel._e(settingSearch("Conversation timeout", "Starts a fresh conversation after this much inactivity.", "conversation_timeout_minutes", "conversation_timeout"))}">${labelRow(panel,"Conversation timeout","conversation_timeout_minutes","conversation_timeout")}<select id="conversation-timeout-preset" ${continuity ? "" : "disabled"}>${timeoutChoices.map((item) => option(panel,String(item.value),timeoutPreset,item.label)).join("")}${option(panel,"custom",timeoutPreset,"Custom")}</select><input id="config-conversation_timeout_minutes" data-config="conversation_timeout_minutes" data-type="number" type="number" min="1" max="1440" value="${panel._e(config.conversation_timeout_minutes)}" ${continuity && timeoutPreset === "custom" ? "" : "hidden"} ${continuity ? "" : "disabled"}><small>Starts a fresh conversation after this much inactivity.</small><span class="field-error" data-error="conversation_timeout_minutes"></span></div>`;
  const jumps = [["general","General"],["conversation","Conversation"],["prompt","Prompt"],["capabilities","Capabilities"],["archive","Archive"],["voice","Voice"],["speech","Speech"],["context","Context"],["model","Model"],["retention","Retention"],["backup","Backup & Restore"]];
  return `<div class="config-toolbar"><input id="config-search" type="search" value="${panel._e(panel._configSearchQuery || "")}" placeholder="Search settings..." aria-label="Search configuration"><div class="agent-actions"><button type="button" class="secondary" id="duplicate-agent" ${cleanOnly}>Duplicate</button><button type="button" class="secondary" id="export-agent" ${cleanOnly}>Export configuration</button><button type="button" class="secondary" id="import-agent">Import configuration</button></div></div>${panel._configDirty ? `<p class="action-help">Duplicate and Export configuration use the saved configuration. Save or revert this shared draft to enable them.</p>` : ""}
    <nav class="config-jumps" aria-label="Jump to configuration section">${jumps.map(([id,label])=>`<a href="#config-${id}" data-jump="config-${id}">${label}</a>`).join("<span aria-hidden=\"true\">&middot;</span>")}</nav>
    <div class="content-card config-surface">
      ${section(panel,"general","General","Choose the model and basic response behaviour.","model api tokens function calls continue",`<div class="form-grid general-grid">${field(panel,"__title","Agent name",panel._draftTitle ?? panel._result?.title ?? "")}${field(panel,"chat_model","Chat model",config.chat_model)}${select(panel,"api_mode","Provider API format",config.api_mode,choices("api_mode"),"Choose how requests are formatted for the provider. Auto is recommended unless your provider requires a specific API.",false,"api_mode")}${field(panel,"max_tokens","Maximum response length (tokens)",config.max_tokens,"number","Sets the most tokens the model may use in one response.")}${field(panel,"max_function_calls_per_conversation","Maximum tool calls per conversation",config.max_function_calls_per_conversation,"number","Stops the assistant after this many tool calls in one conversation to prevent runaway actions.")}${select(panel,"continue_conversation","Listen for a follow-up",config.continue_conversation,choices("continue_conversation"),"Choose whether Home Assistant keeps listening for an immediate reply after the assistant responds.",false,"continue_conversation")}</div>`)}
      ${section(panel,"conversation","Conversation","Choose whether the assistant remembers the recent conversation when you speak to it again.","conversation continue resume cross-device timeout",`<div class="form-grid">${select(panel,"conversation_continuity","Remember recent conversation",config.conversation_continuity,choices("conversation_continuity"),"Lets a new Assist request continue from your recent conversation instead of starting from scratch.",false,"conversation_continuity")}<div class="dependent ${continuity ? "" : "is-disabled"}" data-dependent="conversation_continuity">${timeoutControl}</div></div>`)}
      ${section(panel,"prompt","Prompt & context","Instructions and live Home Assistant context used by this agent.","template instructions preview effective request date time exposed devices",`<div class="config-stack context-toggles">${toggle(panel,"current_datetime_enabled","Include current date & time",config.current_datetime_enabled,"Gives the assistant the current Home Assistant-local date and time.")}${toggle(panel,"exposed_entities_enabled","Include exposed devices",config.exposed_entities_enabled,"Provides the current names and states of entities exposed to Assist.")}</div><details class="advanced-context-formatting" data-setting data-search="advanced context formatting custom templates date devices"><summary>Advanced context formatting</summary><p class="help">Leave an override blank to use the integration-maintained default. Custom values use the prompt-template environment.</p><div class="config-stack"><label class="setting"><span class="setting-label-row"><span>Current date/time format</span><button type="button" class="secondary compact-button reset-context-template" data-template-key="current_datetime_template">Reset to default</button></span><textarea data-config="current_datetime_template" class="short-textarea" spellcheck="false" placeholder="Default integration-managed format">${panel._e(config.current_datetime_template || "")}</textarea><span class="field-error" data-error="current_datetime_template"></span></label><label class="setting"><span class="setting-label-row"><span>Device context format</span><button type="button" class="secondary compact-button reset-context-template" data-template-key="exposed_entities_template">Reset to default</button></span><textarea data-config="exposed_entities_template" class="short-textarea" spellcheck="false" placeholder="Default integration-managed format">${panel._e(config.exposed_entities_template || "")}</textarea><span class="field-error" data-error="exposed_entities_template"></span></label></div></details><label class="setting prompt-setting" data-setting data-search="prompt system instructions template"><span class="sr-only">System prompt</span><textarea id="prompt-editor" data-config="prompt" class="prompt-editor" spellcheck="false">${panel._e(config.prompt || "")}</textarea></label><div class="editor-meta"><span id="prompt-count">${(config.prompt || "").length.toLocaleString()} characters</span><span class="editor-meta-actions"><button type="button" class="secondary compact-button" id="preview-request">Preview effective request</button><button type="button" class="secondary compact-button" id="reset-prompt">Reset to default</button></span></div><span class="field-error" data-error="prompt"></span>`)}
      ${section(panel,"capabilities","Capabilities","Choose which information sources and memory features the assistant can use.","web search skills knowledge memory temporary ephemeral short-term eager",`<div class="config-stack">${toggle(panel,"web_search","Web search",config.web_search,"Lets the assistant search the web for current information.")}<div class="dependent ${config.web_search ? "" : "is-disabled"}" data-dependent="web_search">${select(panel,"web_search_context","Web search detail",config.web_search_context,choices("web_search_context"),"Choose how much supporting material the provider returns with each web search.",!config.web_search,"web_search_context")}</div>${toggle(panel,"knowledge_enabled","Knowledge Library",config.knowledge_enabled,"Lets the assistant search reference material you maintain locally; it is separate from memories and conversation history.",false,"knowledge_library")}${select(panel,"memory_mode","Long-term memory",config.memory_mode,choices("memory_mode"),"Choose whether the assistant can save durable facts for future conversations.",false,"memory_mode")}${select(panel,"temporary_memory","Short-term memory",config.temporary_memory,choices("temporary_memory"),"Choose how readily the assistant remembers useful temporary details until they expire.",false,"temporary_memory")}${field(panel,"skills","Skills",(config.skills || []).join(", "),"text","Enter comma-separated names of installed instruction sets the assistant may load when needed.")}</div>`)}
      ${section(panel,"archive","Conversation archive","Keep conversations locally so you can review them or let the assistant find earlier discussions.","retention shared session timeout model search",`<div class="config-stack">${toggle(panel,"archive_enabled","Save conversation history",archive,"Stores conversations locally for later review and optional search by the assistant.")}<div class="dependent ${archive ? "" : "is-disabled"}" data-dependent="archive_enabled"><div class="form-grid">${select(panel,"archive_retention_days","Keep archived conversations for",config.archive_retention_days,choices("archive_retention_days"),"Choose how long saved conversations remain available before they are deleted automatically.",!archive)}${field(panel,"archive_session_timeout_minutes","Start a new archive after (minutes)",config.archive_session_timeout_minutes,"number","After this much inactivity, the next message is saved as a new archived conversation.",!archive,"archive_session_timeout")}</div>${toggle(panel,"archive_model_search_enabled","Let the assistant search the archive",config.archive_model_search_enabled,"Lets the assistant search saved conversations when an earlier discussion may help answer you.",!archive,"archive_model_search")}${toggle(panel,"shared_archive_enabled","Save shared-household conversations",config.shared_archive_enabled,"Also saves eligible conversations assigned to the shared household; private user archives stay private.",!archive,"shared_archive")}</div></div>`)}
      ${section(panel,"voice","Voice & identity","Decide whose memories and conversation history should be used for requests from each voice device.","unidentified unmapped default owner shared memory satellite device mappings",`<div class="form-grid">${select(panel,"voice_scope_policy","When the speaker is not identified",config.voice_scope_policy,choices("voice_scope_policy"),"Choose whether the request uses no retained personal data, shared household data, a default user, or a device mapping.",false,"voice_scope_policy")}${select(panel,"voice_unmapped_policy","When a device has no mapping",config.voice_unmapped_policy,choices("voice_unmapped_policy"),"Choose whose data to use when a voice device is not assigned to a user.",false,"voice_unmapped_policy")}${field(panel,"voice_default_user_id","Default Home Assistant user ID",config.voice_default_user_id || "","text","Enter the user ID whose memories and history are used when a policy selects the default user.")}${select(panel,"shared_memory_mode","Shared household memory",config.shared_memory_mode,choices("shared_memory_mode"),"Choose whether the assistant can save long-term memories shared by everyone in the household.",false,"shared_memory_mode")}</div><div class="setting mappings-setting" data-setting data-search="${panel._e(settingSearch("Voice device assignments", "Assigns voice satellites and devices to a specific user or shared household data.", "voice_device_mappings", "voice_device_mappings"))}"><span class="setting-label-row"><label for="voice-mappings">Voice device assignments (JSON)</label>${helpButton(panel,"voice_device_mappings")}</span><textarea id="voice-mappings" class="yaml-editor mappings-editor" spellcheck="false">${panel._e(JSON.stringify(config.voice_device_mappings || {}, null, 2))}</textarea><small>Assign each satellite or device ID to a Home Assistant user, shared household data, or no retained data.</small><span class="field-error" data-error="voice_device_mappings"></span></div>`)}
      ${section(panel,"speech","Spoken response","Clean text before it is spoken while retaining the original assistant response for history and context.","tts markdown url regex replacements preview",`<div class="config-stack">${toggle(panel,"speech_processing_enabled","Speech post-processing",speech,"Cleans text before it is spoken without changing the retained original assistant response.")}<div class="dependent ${speech ? "" : "is-disabled"}" data-dependent="speech_processing_enabled">${toggle(panel,"speech_strip_markdown","Remove Markdown links and formatting",config.speech_strip_markdown,"Removes Markdown links and formatting from progressive and completed spoken output.",!speech)}${toggle(panel,"speech_strip_urls","Remove bare URLs",config.speech_strip_urls,"Prevents raw web addresses from being spoken, including during progressive speech.",!speech)}<div class="setting-group" data-setting data-search="${panel._e(settingSearch("Custom replacements", "Rules run on the completed response and affect spoken output only.", "speech_regex_replacements", "custom_replacements"))}"><div class="subheading"><span class="setting-label-row"><h3>Custom replacements</h3>${helpButton(panel,"custom_replacements")}</span><p>Rules run on the completed response. Progressive TTS is disabled when custom rules are configured.</p></div><div class="rule-headings" aria-hidden="true"><span>Pattern</span><span>Replacement</span><span>Actions</span></div><div id="regex-rules" class="rule-list">${regexRules.map((rule,index)=>regexRow(panel,rule,index,!speech)).join("")}</div><button type="button" class="secondary add-regex" id="add-regex" ${speech ? "" : "disabled"}>+ Add rule</button></div><div class="preview-grid setting-group" data-setting data-search="spoken text preview sample assistant"><label>Sample assistant text<textarea id="speech-sample" class="short-textarea" placeholder="Paste a response containing links or abbreviations" ${speech ? "" : "disabled"}></textarea></label><label>Preview spoken text<textarea id="speech-output" class="short-textarea" readonly></textarea></label></div><button type="button" class="secondary preview-button" id="preview-speech" ${speech ? "" : "disabled"}>Preview spoken text</button></div></div>`)}
      ${section(panel,"context","Conversation history limits","Choose what happens when the conversation becomes too large for the model's context window.","threshold truncate summarize recent clear",`<div class="form-grid">${field(panel,"context_threshold","Trim history after (input tokens)",config.context_threshold,"number","Older conversation content is reduced when the provider reports more than this many input tokens.",false,"context_threshold")}${select(panel,"context_truncate_strategy","Conversation history trimming",config.context_truncate_strategy,choices("context_truncate_strategy"),"Choose whether to keep recent messages, clear the history, or summarize older messages when the limit is reached.",false,"context_truncation")}</div>`)}
      ${section(panel,"model","Model parameters","Fine-tune supported model behaviour. Most users can keep the defaults.","temperature top p reasoning effort service tier tool id memory retrieve",`<div class="form-grid">${capabilities.supports_temperature ? field(panel,"temperature","Response creativity (temperature)",config.temperature,"number","Higher values make responses more varied; lower values make them more predictable.") : ""}${capabilities.supports_top_p ? field(panel,"top_p","Response diversity (Top P)",config.top_p,"number","Adjusts how widely the model samples possible words. Usually leave this at its default.") : ""}${capabilities.supports_reasoning_effort ? select(panel,"reasoning_effort","Reasoning effort",config.reasoning_effort || "low",choices("reasoning_effort"),"Choose how much work the model spends on difficult tasks; higher settings may be slower and cost more.",false,"reasoning_effort") : ""}${capabilities.supports_service_tier ? select(panel,"service_tier","Processing tier",config.service_tier || "flex",choices("service_tier"),"Choose the provider's service tier, which can affect request priority, availability, or cost.",false,"service_tier") : ""}${toggle(panel,"shorten_tool_call_id","Use shorter tool-call IDs",config.shorten_tool_call_id,"Enable only when your provider rejects the default tool-call identifiers.")}${field(panel,"memory_auto_retrieve_limit","Memories included per request",config.memory_auto_retrieve_limit,"number","Sets the maximum number of relevant long-term memories added to each request; it does not limit stored memories.")}</div><button type="button" class="secondary compact-button section-reset" id="reset-advanced">Reset model parameters</button>`)}
      ${section(panel,"retention","Usage detail retention","Aggregate and lifetime totals remain separate from these detailed records.","usage request run details totals",`<div class="form-grid">${select(panel,"usage_request_retention_days","Request details",config.usage_request_retention_days,choices("usage_request_retention_days"),"Controls how long detailed per-request usage records are retained.")}${select(panel,"usage_run_retention_days","Run details",config.usage_run_retention_days,choices("usage_run_retention_days"),"Controls how long detailed conversation-run records are retained.")}</div>`)}
      ${section(panel,"backup","Backup & Restore","Create a complete backup of this conversation agent, including its configuration and stored data, or restore one from a previous backup.","disaster recovery migration memories knowledge usage private",`<div class="backup-panel" data-setting data-search="full backup restore private memories knowledge usage archive"><p class="privacy-warning"><strong>Private backup:</strong> Full backups may contain private memories, prompts, Knowledge Base content, archived conversations and usage metadata. Store backup files securely.</p><p class="help">Need only the reusable agent configuration? Use <strong>Export configuration</strong> above.</p><div class="backup-actions"><button type="button" id="create-backup" ${cleanOnly}>Create full backup</button><button type="button" class="secondary" id="restore-backup">Restore backup</button><input id="backup-file" type="file" accept="application/json,.json" hidden></div>${panel._configDirty ? `<small>Save or revert configuration changes before creating a backup. Restoring will discard the unsaved draft after confirmation.</small>` : ""}</div>`)}
      ${saveBar(panel)}<span class="sr-only" data-defaults="${panel._e(JSON.stringify(defaults))}"></span>
    </div>`;
}

function regexRow(panel, rule, index, disabled = false) {
  return `<article class="rule-row" data-regex-index="${index}"><label><span class="mobile-label">Pattern</span><input class="regex-pattern" value="${panel._e(rule.pattern || "")}" spellcheck="false" ${disabled ? "disabled" : ""}><span class="field-error" data-error="speech_regex_replacements[${index}].pattern"></span></label><label><span class="mobile-label">Replacement</span><input class="regex-replacement" value="${panel._e(rule.replacement || "")}" spellcheck="false" ${disabled ? "disabled" : ""}><span class="field-error" data-error="speech_regex_replacements[${index}].replacement"></span></label><div class="rule-actions"><button type="button" class="secondary move-regex" data-direction="-1" aria-label="Move rule up" ${disabled || index === 0 ? "disabled" : ""}>&uarr;</button><button type="button" class="secondary move-regex" data-direction="1" aria-label="Move rule down" ${disabled ? "disabled" : ""}>&darr;</button><button type="button" class="danger delete-regex" ${disabled ? "disabled" : ""}>Delete</button></div></article>`;
}

function readConfig(panel) {
  const root = panel.shadowRoot;
  const config = clone(panel._draft || panel._result.config);
  root.querySelectorAll("[data-config]").forEach((input) => {
    const key = input.dataset.config;
    if (key === "__title" || key === "prompt") return;
    let value = input.dataset.type === "boolean" ? input.checked : input.value;
    if (input.dataset.type === "number") value = Number(value);
    if (key === "skills") value = String(value).split(",").map(item => item.trim()).filter(Boolean);
    config[key] = value;
  });
  config.prompt = root.querySelector("#prompt-editor")?.value ?? config.prompt;
  config.speech_regex_replacements = [...root.querySelectorAll(".rule-row")].map((row) => ({pattern: row.querySelector(".regex-pattern").value, replacement: row.querySelector(".regex-replacement").value}));
  try { config.voice_device_mappings = JSON.parse(root.querySelector("#voice-mappings")?.value || "{}"); }
  catch (_) { config.voice_device_mappings = root.querySelector("#voice-mappings")?.value; }
  panel._draftTitle = root.querySelector('[data-config="__title"]')?.value ?? panel._draftTitle;
  panel._draft = config;
  return config;
}

function dirty(panel) {
  readConfig(panel);
  panel._setConfigDirty(true);
  const state = panel.shadowRoot.querySelector("#dirty-state");
  if (state) state.textContent = "Unsaved changes";
  panel.shadowRoot.querySelector("#save-config")?.removeAttribute("disabled");
  panel.shadowRoot.querySelector("#revert-config")?.removeAttribute("disabled");
}

function showErrors(panel, errors = {}) {
  panel.shadowRoot.querySelectorAll(".field-error").forEach((item) => item.textContent = "");
  Object.entries(errors).forEach(([key, message]) => {
    const target = panel.shadowRoot.querySelector(`[data-error="${CSS.escape(key)}"]`) || panel.shadowRoot.querySelector(`[data-error="${CSS.escape(key.split("[")[0])}"]`);
    if (target) target.textContent = message;
  });
}

function applySearch(panel) {
  const root = panel.shadowRoot;
  const query = (panel._configSearchQuery || "").trim().toLowerCase();
  root.querySelectorAll("[data-setting]").forEach((item) => {
    const text = `${item.dataset.search || ""} ${item.textContent || ""}`.toLowerCase();
    item.classList.toggle("search-dim", Boolean(query) && !text.includes(query));
  });
  root.querySelectorAll("[data-config-section]").forEach((item) => {
    const own = `${item.dataset.search || ""} ${item.querySelector(".config-section-heading")?.textContent || ""}`.toLowerCase();
    const matchedSetting = [...item.querySelectorAll("[data-setting]")].some((setting) => !setting.classList.contains("search-dim"));
    item.classList.toggle("search-dim", Boolean(query) && !own.includes(query) && !matchedSetting);
  });
}

function setDependent(root, key, enabled) {
  const container = root.querySelector(`[data-dependent="${key}"]`);
  if (!container) return;
  container.classList.toggle("is-disabled", !enabled);
  container.querySelectorAll("input:not([readonly]),select,textarea:not([readonly]),button:not(.help-button)").forEach((control) => control.disabled = !enabled);
}

function renderRegexRules(panel, focusIndex = null) {
  const list = panel.shadowRoot.querySelector("#regex-rules");
  if (!list) return;
  const disabled = !panel._draft.speech_processing_enabled;
  list.innerHTML = panel._draft.speech_regex_replacements.map((rule,index) => regexRow(panel,rule,index,disabled)).join("");
  bindRegexRules(panel);
  if (focusIndex !== null) {
    const input = list.querySelector(`[data-regex-index="${focusIndex}"] .regex-pattern`);
    input?.focus();
    input?.scrollIntoView({block:"nearest"});
  }
}

function bindRegexRules(panel) {
  const root = panel.shadowRoot;
  root.querySelectorAll(".regex-pattern,.regex-replacement").forEach((input) => input.addEventListener("input", () => dirty(panel)));
  root.querySelectorAll(".delete-regex").forEach((button) => button.addEventListener("click", () => {
    const index = Number(button.closest(".rule-row").dataset.regexIndex);
    readConfig(panel);
    panel._draft.speech_regex_replacements.splice(index, 1);
    panel._setConfigDirty(true);
    renderRegexRules(panel, Math.min(index, panel._draft.speech_regex_replacements.length - 1));
  }));
  root.querySelectorAll(".move-regex").forEach((button) => button.addEventListener("click", () => {
    const index = Number(button.closest(".rule-row").dataset.regexIndex);
    const target = index + Number(button.dataset.direction);
    readConfig(panel);
    if (target < 0 || target >= panel._draft.speech_regex_replacements.length) return;
    [panel._draft.speech_regex_replacements[index], panel._draft.speech_regex_replacements[target]] = [panel._draft.speech_regex_replacements[target], panel._draft.speech_regex_replacements[index]];
    panel._setConfigDirty(true);
    renderRegexRules(panel, target);
  }));
}

export function bindConfiguration(panel) {
  const root = panel.shadowRoot;
  bindHelp(panel);
  root.querySelectorAll("[data-config],#voice-mappings").forEach((input) => input.addEventListener("input", () => {
    dirty(panel);
    if (input.dataset.type === "boolean") setDependent(root, input.dataset.config, input.checked);
    if (input.dataset.config === "conversation_continuity") setDependent(root, "conversation_continuity", input.value !== "ha_default");
    if (input.id === "prompt-editor") root.querySelector("#prompt-count").textContent = `${input.value.length.toLocaleString()} characters`;
  }));
  bindRegexRules(panel);
  root.querySelector("#config-search")?.addEventListener("input", (event) => { panel._configSearchQuery = event.target.value; applySearch(panel); });
  applySearch(panel);
  root.querySelectorAll("[data-jump]").forEach((link) => link.addEventListener("click", (event) => { event.preventDefault(); root.querySelector(`#${link.dataset.jump}`)?.scrollIntoView({behavior:"smooth",block:"start"}); }));
  root.querySelector('[data-config="chat_model"]')?.addEventListener("change", async () => {
    try {
      const validation = await panel._call("configuration", "validate", {config: readConfig(panel)});
      if (validation.valid) {
        panel._result.model_capabilities = validation.model_capabilities;
        panel._setConfigDirty(true);
        panel._configRestoreFocus = '[data-config="chat_model"]';
        panel._render();
      }
    } catch (err) { panel._toast(`Unable to inspect model options: ${err.message || String(err)}`, true); }
  });
  root.querySelector("#conversation-timeout-preset")?.addEventListener("change", (event) => {
    const input = root.querySelector('[data-config="conversation_timeout_minutes"]');
    const custom = event.target.value === "custom";
    input.hidden = !custom;
    if (!custom) input.value = event.target.value;
    dirty(panel);
    if (custom) input.focus();
  });
  root.querySelector("#reset-prompt")?.addEventListener("click", () => { const editor=root.querySelector("#prompt-editor"); editor.value=panel._result.defaults.prompt; for(const key of ["current_datetime_enabled","exposed_entities_enabled"]){const input=root.querySelector(`[data-config="${key}"]`);if(input)input.checked=true;} for(const key of ["current_datetime_template","exposed_entities_template"]){const input=root.querySelector(`[data-config="${key}"]`);if(input)input.value="";} editor.focus(); dirty(panel); root.querySelector("#prompt-count").textContent=`${editor.value.length.toLocaleString()} characters`; });
  root.querySelectorAll(".reset-context-template").forEach((button)=>button.addEventListener("click",()=>{const input=root.querySelector(`[data-config="${button.dataset.templateKey}"]`);if(input){input.value="";dirty(panel);input.focus();}}));
  root.querySelector("#preview-request")?.addEventListener("click", async () => {
    const dialog=root.querySelector("#prompt-preview-dialog");
    const status=root.querySelector("#prompt-preview-status");
    const sections=root.querySelector("#request-preview-sections");
    const notes=root.querySelector("#prompt-preview-notes");
    const copyButton=root.querySelector("#copy-prompt-preview");
    root.querySelector("#request-footprint").textContent="Calculating..."; root.querySelector("#function-group-savings").textContent=""; sections.innerHTML=""; notes.innerHTML=""; status.textContent="Assembling current Home Assistant request..."; status.className="validation"; copyButton.disabled=true; dialog.showModal();
    try {
      const result=await panel._call("configuration","request_preview",{config:readConfig(panel)});
      panel._effectiveRequestPreview=result;
      root.querySelector("#request-footprint").textContent=`Previewed Request Content: ${Number(result.total_character_count||0).toLocaleString()} characters`;
      const savings=result.function_group_savings||{};
      root.querySelector("#function-group-savings").textContent=`Saved by Function Groups: ${Number(savings.characters||0).toLocaleString()} characters (${Number(savings.percent||0)}%)`;
      sections.innerHTML=(result.sections||[]).map((section,index)=>`<details class="request-preview-section" ${index===0?"open":""}><summary><span>${panel._e(section.label)}</span><span class="request-section-meta">${Number(section.character_count||0).toLocaleString()} characters <button type="button" class="secondary compact-button copy-request-section" data-section-index="${index}">Copy section</button></span></summary><textarea class="yaml-editor request-preview-output" readonly spellcheck="false">${panel._e(section.content||"")}</textarea></details>`).join("");
      root.querySelectorAll(".copy-request-section").forEach((button)=>button.addEventListener("click",async(event)=>{event.preventDefault();event.stopPropagation();const section=result.sections[Number(button.dataset.sectionIndex)];try{await copyTextToClipboard(section.content||"");panel._toast(`${section.label} copied`);}catch(err){panel._toast(`Unable to copy section: ${err.message||String(err)}`,true);}}));
      notes.innerHTML=(result.notes||[]).map((note)=>`<li>${panel._e(note)}</li>`).join("");
      status.textContent="Resolved using current Home Assistant state and production request assembly";
      status.className="validation valid";
      copyButton.disabled=false;
    } catch(err) {
      status.textContent=err.message||String(err);
      status.className="validation invalid";
    }
  });
  root.querySelector("#prompt-preview-close")?.addEventListener("click",()=>root.querySelector("#prompt-preview-dialog").close());
  root.querySelector("#copy-prompt-preview")?.addEventListener("click",async()=>{try{const text=(panel._effectiveRequestPreview?.sections||[]).map((section)=>`## ${section.label}\n${section.content}`).join("\n\n");await copyTextToClipboard(text);panel._toast("Effective request copied");}catch(err){panel._toast(`Unable to copy request: ${err.message||String(err)}`,true);}});
  root.querySelector("#reset-advanced")?.addEventListener("click", () => { ["temperature","top_p","reasoning_effort","service_tier","shorten_tool_call_id","memory_auto_retrieve_limit"].forEach((key) => { panel._draft[key]=clone(panel._result.defaults[key]); const input=root.querySelector(`[data-config="${key}"]`); if(input) input.dataset.type==="boolean" ? input.checked=panel._draft[key] : input.value=panel._draft[key]; }); panel._setConfigDirty(true); dirty(panel); });
  root.querySelector("#add-regex")?.addEventListener("click", () => { readConfig(panel); panel._draft.speech_regex_replacements.push({pattern:"",replacement:""}); panel._setConfigDirty(true); renderRegexRules(panel,panel._draft.speech_regex_replacements.length-1); });
  root.querySelector("#revert-config")?.addEventListener("click", () => { panel._draft=clone(panel._configData.config); panel._draftTitle=panel._configData.title; panel._setConfigDirty(false); panel._render(); });
  root.querySelector("#save-config")?.addEventListener("click", async () => {
    const button=root.querySelector("#save-config"); panel._setSaving(button,true);
    try { const config=readConfig(panel); const validation=await panel._call("configuration","validate",{config}); showErrors(panel,validation.errors); if(!validation.valid){panel._toast("Fix the highlighted configuration errors",true);return;} const saved=await panel._call("configuration","update",{config,title:panel._draftTitle}); panel._configData={...panel._configData,...saved}; panel._result=panel._configData; panel._draft=clone(saved.config); panel._draftTitle=saved.title; panel._setConfigDirty(false); panel._toast("Configuration saved"); await panel._loadAgents(panel._agentId); }
    catch(err){panel._toast(`Unable to save configuration: ${err.message||String(err)}`,true);} finally{panel._setSaving(button,false);}
  });
  root.querySelector("#preview-speech")?.addEventListener("click", async () => { try { const response=await panel._call("configuration","speech_preview",{config:readConfig(panel),sample_text:root.querySelector("#speech-sample").value}); root.querySelector("#speech-output").value=response.speech_text; } catch(err){panel._toast(`Unable to preview speech: ${err.message||String(err)}`,true);} });
  root.querySelector("#duplicate-agent")?.addEventListener("click", async () => { if(panel._configDirty)return; try { const result=await panel._call("configuration","duplicate"); await panel._loadAgents(result.subentry_id); panel._toast(`Created ${result.title}`); } catch(err){panel._toast(`Unable to duplicate agent: ${err.message||String(err)}`,true);} });
  root.querySelector("#export-agent")?.addEventListener("click", async () => { if(panel._configDirty)return; if(!await panel._confirm("Export saved agent configuration?","Export applies best-effort secret redaction, but Function Tool definitions may contain embedded credentials. Review the downloaded file before sharing it.","Export"))return; const result=await panel._call("configuration","export"); const blob=new Blob([result.json],{type:"application/json"}); const url=URL.createObjectURL(blob); const link=document.createElement("a"); link.href=url; link.download=`${(panel._draftTitle||"agent").replace(/[^a-z0-9]+/gi,"-").toLowerCase()}.json`; link.click(); URL.revokeObjectURL(url); });
  root.querySelector("#import-agent")?.addEventListener("click", () => root.querySelector("#import-dialog")?.showModal());
  root.querySelector("#import-preview")?.addEventListener("click", async () => { try { const result=await panel._call("configuration","import_preview",{document:root.querySelector("#import-document").value}); panel._importDocument=root.querySelector("#import-document").value; root.querySelector("#import-summary").textContent=`${result.title} / ${result.summary.model} / ${result.summary.tools} tools / ${result.summary.function_groups} function groups / ${result.summary.speech_rules} speech rules`; root.querySelector("#import-apply").disabled=false; } catch(err){root.querySelector("#import-summary").textContent=err.message||String(err);root.querySelector("#import-apply").disabled=true;} });
  root.querySelector("#import-apply")?.addEventListener("click", async () => { const mode=root.querySelector('input[name="import-mode"]:checked').value; if(mode==="current"&&!await panel._confirm("Overwrite this agent?",`The saved configuration will be replaced.${panel._configDirty ? " Your unsaved shared draft will be discarded." : ""} Retained history and parent-entry credentials are not affected.`,"Overwrite"))return; try { await panel._call("configuration","import",{document:panel._importDocument,mode,confirm:mode==="current"}); root.querySelector("#import-dialog").close(); if(mode==="current")panel._clearConfigDraft(); await panel._loadAgents(panel._agentId); panel._toast(mode==="current"?"Configuration imported":"Agent created from import; your current draft is preserved"); } catch(err){panel._toast(`Unable to import: ${err.message||String(err)}`,true);} });
  root.querySelector("#import-cancel")?.addEventListener("click",()=>root.querySelector("#import-dialog").close());
  root.querySelector("#create-backup")?.addEventListener("click", async () => {
    if(panel._configDirty)return;
    const button=root.querySelector("#create-backup"); panel._setSaving(button,true);
    try { const result=await panel._call("backup","create"); const blob=new Blob([result.json],{type:"application/json"}); const url=URL.createObjectURL(blob); const link=document.createElement("a"); link.href=url; link.download=result.filename||"conversation-agent-full-backup.json"; link.click(); setTimeout(()=>URL.revokeObjectURL(url),0); panel._toast("Full backup created"); }
    catch(err){panel._toast(`Unable to create backup: ${err.message||String(err)}`,true);} finally{panel._setSaving(button,false);}
  });
  root.querySelector("#restore-backup")?.addEventListener("click",()=>{ const input=root.querySelector("#backup-file"); input.value=""; input.click(); });
  root.querySelector("#backup-file")?.addEventListener("change",async(event)=>{
    const file=event.target.files?.[0]; if(!file)return;
    const apply=root.querySelector("#restore-apply");
    if(file.size>16*1024*1024){panel._toast("The backup file exceeds the 16 MB size limit",true);return;}
    try { const document=await file.text(); const result=await panel._call("backup","inspect",{document}); panel._backupDocument=document; root.querySelector("#restore-backup-name").textContent=result.title; root.querySelector("#restore-backup-meta").textContent=`Created ${new Date(result.summary.created_at).toLocaleString()} with integration ${result.summary.integration_version}`; root.querySelector("#restore-summary").innerHTML=backupSummaryLines(result.summary).map(line=>`<li>${panel._e(line)}</li>`).join(""); apply.disabled=false; root.querySelector("#restore-dialog").showModal(); }
    catch(err){panel._backupDocument=null;apply.disabled=true;panel._toast(err.message||String(err),true);}
  });
  root.querySelector("#restore-cancel")?.addEventListener("click",()=>root.querySelector("#restore-dialog").close());
  root.querySelector("#restore-apply")?.addEventListener("click",async()=>{
    if(!panel._backupDocument)return; const button=root.querySelector("#restore-apply"); panel._setSaving(button,true);
    try { await panel._call("backup","restore",{document:panel._backupDocument,confirm:true}); root.querySelector("#restore-dialog").close(); panel._backupDocument=null; panel._clearConfigDraft(); await panel._loadAgents(panel._agentId); panel._toast("Full backup restored"); }
    catch(err){panel._toast(`Unable to restore backup: ${err.message||String(err)}`,true);} finally{panel._setSaving(button,false);}
  });
  if (panel._configRestoreFocus) { const selector=panel._configRestoreFocus; panel._configRestoreFocus=null; requestAnimationFrame(()=>root.querySelector(selector)?.focus({preventScroll:true})); }
}

function functionToolCard(panel, tool, allTools) {
  const index = allTools.indexOf(tool);
  const enabled = isFunctionToolEnabled(tool);
  return `<article class="list-card tool-card ${enabled ? "" : "is-disabled"}" data-tool-index="${index}" data-tool-search="${panel._e(`${tool.spec?.name || ""} ${tool.spec?.description || ""} ${tool.function?.type || ""} ${enabled ? "enabled" : "disabled"}`.toLowerCase())}"><div class="card-main"><div class="tool-title"><h4>${panel._e(tool.spec?.name||"Unnamed tool")}</h4><span class="type-badge">${panel._e(tool.function?.type||"Unknown type")}</span>${enabled ? "" : '<span class="disabled-badge">Disabled</span>'}</div><p class="description">${panel._e(tool.spec?.description||"No description")}</p></div><div class="actions tool-card-actions"><label class="tool-enabled-control"><span>Enabled</span><span class="switch-control"><input class="tool-enabled" data-index="${index}" type="checkbox" role="switch" aria-label="Enable ${panel._e(tool.spec?.name||"Function Tool")}" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></span></label><button type="button" class="secondary edit-tool" data-index="${index}">Edit</button><button type="button" class="secondary duplicate-tool" data-index="${index}">Duplicate</button><button type="button" class="danger delete-tool" data-index="${index}">Delete</button></div></article>`;
}

function functionGroupCard(panel, group, tools) {
  const mode = group.loading_mode === "on_demand" ? "Load when needed" : "Always available";
  const members = group.tools.map((tool) => functionToolCard(panel,tool,tools)).join("");
  return `<article class="function-group-card" data-group-id="${panel._e(group.id)}" data-group-search="${panel._e(`${group.name} ${group.description} ${group.id}`.toLowerCase())}"><div class="function-group-heading"><div><div class="tool-title"><h3>${panel._e(group.name)}</h3><span class="availability-badge ${group.loading_mode === "on_demand" ? "on-demand" : ""}">${mode}</span><span class="function-count">${functionToolCountLabel(group.tools)}</span></div><p>${panel._e(group.description)}</p><code>${panel._e(group.id)}</code></div><div class="actions"><button type="button" class="secondary edit-group" data-group-id="${panel._e(group.id)}">Edit group</button><button type="button" class="danger delete-group" data-group-id="${panel._e(group.id)}">Delete group</button></div></div><details><summary>Show member functions</summary><div class="list tool-list">${members || panel._empty("This group has no functions yet. Edit it to choose functions.")}</div></details></article>`;
}

export function renderTools(panel) {
  const config = panel._draft || panel._result?.config || {};
  const tools = config.functions || [];
  const categories = categorizeFunctionTools(config);
  const ungrouped = categories.alwaysAvailable.map((tool)=>functionToolCard(panel,tool,tools)).join("");
  return `<section class="content-card tools-surface"><div class="section-heading"><div><span class="setting-label-row"><h2>Tool groups</h2>${helpButton(panel,"function_tools")}</span><p>Organise related function tools and choose whether their full instructions are sent on every request or loaded only when needed.</p></div><div class="actions"><button type="button" class="secondary" id="add-group">+ Create group</button><button type="button" id="add-tool">+ Add Function Tool</button></div></div><div class="notice function-groups-help"><strong>Loading groups only when needed</strong><p>The assistant initially sees each group's name and description, then loads its full tool instructions if the current task needs them. This reduces input-token usage but may add one model round-trip the first time a group is used.</p></div><label class="tool-search"><span class="sr-only">Search functions and groups</span><input id="tool-search" type="search" placeholder="Search functions and groups..." aria-label="Search functions and groups"></label><div class="function-groups"><article class="function-group-card always-card" data-group-search="always available ungrouped general"><div class="function-group-heading"><div><div class="tool-title"><h3>Available on every request</h3><span class="availability-badge">Always available</span><span class="function-count">${functionToolCountLabel(categories.alwaysAvailable)}</span></div><p>These ungrouped functions send their full instructions with every request, so the assistant can use them immediately.</p></div></div><details open><summary>Show included functions</summary><div class="list tool-list">${ungrouped || panel._empty("No ungrouped functions.")}</div></details></article>${categories.groups.map((group)=>functionGroupCard(panel,group,tools)).join("")||panel._empty("No groups yet. Existing functions remain available on every request until you create one.")}</div><div class="section-actions tools-actions"><button type="button" class="secondary" id="validate-tools">Check tool configuration</button><button type="button" id="save-tools" ${panel._configDirty?"":"disabled"}>Save tools and groups</button><span id="tool-status" class="validation" aria-live="polite"></span></div></section>`;
}

async function openTool(panel,index=null) {
  panel._toolIndex=index;
  const root=panel.shadowRoot;
  const dialog=root.querySelector("#tool-dialog");
  root.querySelector("#tool-dialog-title").textContent=index===null?"Add Function Tool":"Edit Function Tool";
  root.querySelector("#tool-dialog-meta").textContent=index===null?"New tool / YAML":"Loading YAML...";
  root.querySelector("#tool-yaml").value="";
  root.querySelector("#tool-error").className="validation";
  root.querySelector("#tool-error").textContent="Loading editor...";
  const picker=root.querySelector("#built-in-picker");
  picker.hidden=index!==null;
  dialog.showModal();
  try {
    let response;
    if(index===null){
      const [starter,catalog]=await Promise.all([panel._call("tools","starter"),panel._call("tools","built_in_catalog",{tools:panel._draft.functions||[]})]);
      response=starter;
      panel._builtInFunctions=catalog.functions||[];
      const selector=root.querySelector("#built-in-function");
      selector.innerHTML='<option value="">Insert Built-in Function…</option>'+panel._builtInFunctions.map((preset)=>`<option value="${panel._e(preset.implementation)}" ${preset.already_configured?"disabled":""}>${panel._e(preset.label)}${preset.already_configured?" — Already configured":""}</option>`).join("");
    } else response=await panel._call("tools","serialize",{tool:panel._draft.functions[index]});
    root.querySelector("#tool-yaml").value=response.yaml;
    panel._toolInitialYaml=response.yaml;
    panel._toolReplaceableYaml=response.yaml;
    root.querySelector("#tool-dialog-meta").textContent=index===null?"New tool / YAML":`${panel._draft.functions[index].spec?.name||"Unnamed"} / ${panel._draft.functions[index].function?.type||"unknown"}`;
    root.querySelector("#tool-error").textContent="Edit the YAML, then validate or keep it in the shared draft.";
    root.querySelector("#tool-yaml").focus();
  } catch(err) { root.querySelector("#tool-error").className="validation invalid"; root.querySelector("#tool-error").textContent=err.message||String(err); }
}

function toolErrorText(errors={}) { return Object.entries(errors).map(([key,value])=>`${key}: ${value}`).join(" "); }

async function validateDialogTool(panel) {
  const root=panel.shadowRoot;
  const status=root.querySelector("#tool-error");
  status.className="validation";
  status.textContent="Validating...";
  try {
    const result=await panel._call("tools","validate_yaml",{yaml:root.querySelector("#tool-yaml").value});
    if(!result.valid){status.className="validation invalid";status.textContent=`Function configuration is invalid: ${toolErrorText(result.errors)}`;return null;}
    status.className="validation valid";
    status.textContent=`Valid function tool / Name: ${result.name} / Type: ${result.type}`;
    root.querySelector("#tool-dialog-meta").textContent=`${result.name} / ${result.type}`;
    return result.config;
  } catch(err){status.className="validation invalid";status.textContent=err.message||String(err);return null;}
}

function renderGroupFunctionChoices(panel, selected = []) {
  const root=panel.shadowRoot;
  const selectedNames=new Set(selected);
  const tools=panel._draft.functions || [];
  const list=root.querySelector("#group-functions");
  list.innerHTML=tools.map((tool)=>{const name=tool.spec?.name||"";const enabled=isFunctionToolEnabled(tool);return `<label class="group-function-choice ${enabled?"":"is-disabled"}" data-choice-search="${panel._e(`${name} ${tool.spec?.description||""} ${enabled?"enabled":"disabled"}`.toLowerCase())}"><input type="checkbox" value="${panel._e(name)}" ${selectedNames.has(name)?"checked":""}><span><strong>${panel._e(name)}${enabled?"":" · Disabled"}</strong><small>${panel._e(tool.spec?.description||"No description")}</small></span></label>`;}).join("") || panel._empty("Add function tools before assigning them to a group.");
}

function openFunctionGroup(panel, groupId = null) {
  const root=panel.shadowRoot;
  const group=(panel._draft.function_groups||[]).find((item)=>item.id===groupId);
  panel._groupOriginalId=group?.id||null;
  panel._groupIdEdited=Boolean(group);
  root.querySelector("#group-dialog-title").textContent=group?"Edit function group":"Create function group";
  root.querySelector("#group-name").value=group?.name||"";
  root.querySelector("#group-id").value=group?.id||"";
  root.querySelector("#group-description").value=group?.description||"";
  root.querySelector("#group-loading-mode").value=group?.loading_mode||"on_demand";
  root.querySelector("#group-function-search").value="";
  root.querySelector("#group-error").textContent="";
  renderGroupFunctionChoices(panel,group?.functions||[]);
  root.querySelector("#group-dialog").showModal();
  root.querySelector("#group-name").focus();
}

function saveFunctionGroup(panel) {
  const root=panel.shadowRoot;
  const name=root.querySelector("#group-name").value.trim();
  const id=root.querySelector("#group-id").value.trim();
  const description=root.querySelector("#group-description").value.trim();
  const loading_mode=root.querySelector("#group-loading-mode").value;
  const functions=[...root.querySelectorAll("#group-functions input:checked")].map((input)=>input.value);
  const error=root.querySelector("#group-error");
  if(!name){error.textContent="Group name is required.";return;}
  if(!/^[a-z][a-z0-9_-]{0,63}$/.test(id)){error.textContent="Group ID must start with a lowercase letter and use only lowercase letters, numbers, underscores, or hyphens.";return;}
  if(!description){error.textContent="Add a concise description so the model knows when this group is relevant.";return;}
  const others=(panel._draft.function_groups||[]).filter((group)=>group.id!==panel._groupOriginalId);
  if(others.some((group)=>group.id===id)){error.textContent="That group ID is already in use.";return;}
  if(others.some((group)=>String(group.name).toLowerCase()===name.toLowerCase())){error.textContent="That group name is already in use.";return;}
  const selected=new Set(functions);
  panel._draft.function_groups=[...others.map((group)=>({...group,functions:(group.functions||[]).filter((functionName)=>!selected.has(functionName))})),{id,name,description,loading_mode,functions}];
  panel._setConfigDirty(true);
  root.querySelector("#group-dialog").close();
  panel._render();
}

export function bindTools(panel) {
  const root=panel.shadowRoot;
  bindHelp(panel);
  root.querySelector("#add-tool")?.addEventListener("click",()=>openTool(panel));
  root.querySelector("#add-group")?.addEventListener("click",()=>openFunctionGroup(panel));
  root.querySelectorAll(".edit-group").forEach((button)=>button.addEventListener("click",()=>openFunctionGroup(panel,button.dataset.groupId)));
  root.querySelectorAll(".delete-group").forEach((button)=>button.addEventListener("click",async()=>{const group=(panel._draft.function_groups||[]).find((item)=>item.id===button.dataset.groupId);if(!await panel._confirm("Delete function group?",`The group “${group?.name||button.dataset.groupId}” will be removed. Its functions will not be deleted; they will move to Always available.`,"Delete group"))return;panel._draft=deleteFunctionGroup(panel._draft,button.dataset.groupId);panel._setConfigDirty(true);panel._render();}));
  root.querySelector("#tool-search")?.addEventListener("input",(event)=>{const query=event.target.value;root.querySelectorAll(".function-group-card").forEach((card)=>{const groupMatch=Boolean(query.trim())&&matchesFunctionSearch(query,card.dataset.groupSearch);let toolMatch=false;card.querySelectorAll(".tool-card").forEach((tool)=>{const matches=!query.trim()||groupMatch||matchesFunctionSearch(query,tool.dataset.toolSearch);tool.hidden=!matches;toolMatch=toolMatch||matches;});card.hidden=Boolean(query.trim())&&!groupMatch&&!toolMatch;if(query.trim()&&toolMatch)card.querySelector("details")?.setAttribute("open","");});});
  root.querySelectorAll(".edit-tool").forEach(button=>button.addEventListener("click",()=>openTool(panel,Number(button.dataset.index))));
  root.querySelectorAll(".tool-enabled").forEach((input)=>input.addEventListener("change",()=>{panel._draft.functions[Number(input.dataset.index)].enabled=input.checked;panel._setConfigDirty(true);panel._render();}));
  root.querySelectorAll(".duplicate-tool").forEach(button=>button.addEventListener("click",()=>{const tool=clone(panel._draft.functions[Number(button.dataset.index)]);let base=`${tool.spec.name}_copy`,name=base,n=2;const names=new Set(panel._draft.functions.map(item=>item.spec?.name));while(names.has(name))name=`${base}_${n++}`;tool.spec.name=name;panel._draft.functions.push(tool);panel._setConfigDirty(true);panel._render();}));
  root.querySelectorAll(".delete-tool").forEach(button=>button.addEventListener("click",async()=>{if(!await panel._confirm("Delete function tool?","The tool will be removed from this local draft and from any group assignment. Save tools to apply the change.","Delete"))return;const [removed]=panel._draft.functions.splice(Number(button.dataset.index),1);panel._draft.function_groups=(panel._draft.function_groups||[]).map((group)=>({...group,functions:(group.functions||[]).filter((name)=>name!==removed?.spec?.name)}));panel._setConfigDirty(true);panel._render();}));
  root.querySelector("#validate-tools")?.addEventListener("click",async()=>{const status=root.querySelector("#tool-status");try{const result=await panel._call("configuration","validate",{config:{functions:panel._draft.functions,function_groups:panel._draft.function_groups||[]}});status.className=`validation ${result.valid?"valid":"invalid"}`;status.textContent=result.valid?"All tools and groups are valid":toolErrorText(result.errors);}catch(err){status.className="validation invalid";status.textContent=err.message||String(err);}});
  root.querySelector("#save-tools")?.addEventListener("click",async()=>{try{const draft=clone(panel._draft);const title=panel._draftTitle;const result=await panel._call("configuration","update",{config:{functions:draft.functions,function_groups:draft.function_groups||[]}});panel._configData={...panel._configData,...result};panel._result=panel._configData;panel._draft={...draft,functions:clone(result.config.functions),function_groups:clone(result.config.function_groups)};panel._draftTitle=title;panel._syncConfigDirty();panel._toast("Function tools and groups saved; other draft changes are preserved");panel._render();}catch(err){panel._toast(`Unable to save tools: ${err.message||String(err)}`,true);}});
  root.querySelector("#tool-yaml")?.addEventListener("input",()=>{root.querySelector("#tool-error").className="validation";root.querySelector("#tool-error").textContent="YAML changed; validate to refresh metadata.";});
  root.querySelector("#built-in-function")?.addEventListener("change",async(event)=>{const preset=(panel._builtInFunctions||[]).find((item)=>item.implementation===event.target.value);if(!preset)return;const editor=root.querySelector("#tool-yaml");const replaceable=canReplaceToolYamlWithoutConfirmation(editor.value,panel._toolReplaceableYaml);if(!replaceable&&!await panel._confirm("Replace current YAML with this built-in function preset?","Your current Function Tool YAML will be replaced in the editor. Nothing is saved until you keep the tool in the draft.","Replace YAML")){event.target.value="";return;}editor.value=preset.yaml;panel._toolReplaceableYaml=preset.yaml;await validateDialogTool(panel);});
  root.querySelector("#tool-cancel")?.addEventListener("click",()=>root.querySelector("#tool-dialog").close());
  root.querySelector("#tool-dialog")?.addEventListener("cancel",()=>{panel._toolIndex=null;});
  root.querySelector("#tool-validate")?.addEventListener("click",()=>validateDialogTool(panel));
  root.querySelector("#tool-save")?.addEventListener("click",async()=>{const tool=await validateDialogTool(panel);if(!tool)return;if(panel._toolIndex===null)panel._draft.functions.push(tool);else{const oldName=panel._draft.functions[panel._toolIndex]?.spec?.name;panel._draft.functions[panel._toolIndex]=tool;if(oldName&&oldName!==tool.spec?.name)panel._draft.function_groups=(panel._draft.function_groups||[]).map((group)=>({...group,functions:(group.functions||[]).map((name)=>name===oldName?tool.spec.name:name)}));}panel._setConfigDirty(true);root.querySelector("#tool-dialog").close();panel._render();});
  root.querySelector("#group-name")?.addEventListener("input",(event)=>{if(!panel._groupIdEdited)root.querySelector("#group-id").value=functionGroupIdFromName(event.target.value);});
  root.querySelector("#group-id")?.addEventListener("input",()=>{panel._groupIdEdited=true;});
  root.querySelector("#group-function-search")?.addEventListener("input",(event)=>{const query=event.target.value;root.querySelectorAll(".group-function-choice").forEach((choice)=>{choice.hidden=!matchesFunctionSearch(query,choice.dataset.choiceSearch);});});
  root.querySelector("#group-cancel")?.addEventListener("click",()=>root.querySelector("#group-dialog").close());
  root.querySelector("#group-save")?.addEventListener("click",()=>saveFunctionGroup(panel));
}

export function restoreDialog(panel) {
  return `<dialog id="restore-dialog" class="editor-dialog" aria-labelledby="restore-dialog-title"><div class="dialog-header"><h2 id="restore-dialog-title">Restore full backup</h2></div><div class="dialog-body"><div><strong id="restore-backup-name"></strong><p id="restore-backup-meta" class="meta"></p></div><ul id="restore-summary" class="restore-summary"></ul><div class="notice"><strong>Replace existing agent state?</strong><p>This restores everything shown above and replaces the current configuration, memories, Knowledge content, archive and usage history. This cannot be merged or undone from this screen.</p></div>${panel._configDirty ? `<p class="inline-error">Your unsaved Configuration/Tools draft will also be discarded.</p>` : ""}</div><div class="dialog-actions"><button type="button" class="secondary" id="restore-cancel">Cancel</button><button type="button" class="danger" id="restore-apply" disabled>Restore everything</button></div></dialog>`;
}

export function configurationDialogs(panel) {
  return `<dialog id="prompt-preview-dialog" class="editor-dialog wide prompt-preview-dialog" aria-labelledby="prompt-preview-title"><div class="dialog-header"><div><h2 id="prompt-preview-title">Preview effective request</h2><p class="dialog-meta">Locally assembled fresh-request content</p></div></div><div class="dialog-body prompt-preview-body"><div class="notice"><strong>Current request preview</strong><p>Shows locally assembled content that would accompany a brand-new message now. Current templates and Home Assistant context are resolved. User input and conversation history are excluded.</p><p>This may contain private entity state, memory data, Knowledge context, user instructions, and Function Tool schemas. Provider-internal framing and hidden server-side content cannot be inspected.</p></div><div class="request-preview-metrics"><strong id="request-footprint">Calculating...</strong><span id="function-group-savings"></span></div><div id="request-preview-sections" class="request-preview-sections"></div><ul id="prompt-preview-notes" class="prompt-preview-notes"></ul><div id="prompt-preview-status" class="validation" role="status" aria-live="polite"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="prompt-preview-close">Close</button><button type="button" id="copy-prompt-preview" disabled>Copy all</button></div></dialog>
  <dialog id="import-dialog" class="editor-dialog wide" aria-labelledby="import-dialog-title"><div class="dialog-header"><h2 id="import-dialog-title">Import agent configuration</h2></div><div class="dialog-body">${panel._configDirty ? `<div class="notice"><strong>Unsaved shared draft</strong><p>Creating a new agent preserves this draft. Overwriting the current agent discards it after confirmation.</p></div>` : ""}<label>Exported JSON or YAML<textarea id="import-document" class="yaml-editor" spellcheck="false"></textarea></label><div class="mode-row"><label><input type="radio" name="import-mode" value="new" checked> Create a new agent</label><label><input type="radio" name="import-mode" value="current"> Overwrite current agent</label></div><div id="import-summary" class="validation" aria-live="polite">Validate the document to preview it.</div></div><div class="dialog-actions"><button type="button" class="secondary" id="import-cancel">Cancel</button><button type="button" class="secondary" id="import-preview">Validate & preview</button><button type="button" id="import-apply" disabled>Import</button></div></dialog>
  <dialog id="tool-dialog" class="editor-dialog tool-dialog" aria-labelledby="tool-dialog-title"><div class="dialog-header"><div><span class="setting-label-row"><h2 id="tool-dialog-title">Function Tool</h2>${helpButton(panel,"function_tools")}</span><p id="tool-dialog-meta" class="dialog-meta">YAML</p></div></div><div class="dialog-body tool-dialog-body"><div id="built-in-picker" class="built-in-picker"><label for="built-in-function">Insert Built-in Function</label><select id="built-in-function"><option value="">Insert Built-in Function…</option></select><small>Built-in functions are implemented directly by Extended OpenAI Conversation. Selecting one inserts an editable Function Tool that exposes the capability to the model.</small></div><label class="tool-editor-label"><span class="sr-only">Function Tool YAML</span><textarea id="tool-yaml" class="yaml-editor tool-yaml-editor" spellcheck="false" wrap="off" aria-describedby="tool-error"></textarea></label><div id="tool-error" class="validation" role="status" aria-live="polite"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="tool-cancel">Cancel</button><button type="button" class="secondary" id="tool-validate">Validate</button><button type="button" id="tool-save">Keep in draft</button></div></dialog>
  <dialog id="group-dialog" class="editor-dialog group-dialog" aria-labelledby="group-dialog-title"><div class="dialog-header"><div><h2 id="group-dialog-title">Function group</h2><p>Create a compact capability the model can load only when needed.</p></div></div><div class="dialog-body group-dialog-body"><div class="form-grid"><label>Group name<input id="group-name" maxlength="100" autocomplete="off" placeholder="Reminders"></label><label>Group ID<input id="group-id" maxlength="64" autocomplete="off" spellcheck="false" placeholder="reminders"><small>Stable lowercase ID used by the model. Advanced users may edit it.</small></label></div><label>Description<textarea id="group-description" class="short-textarea" maxlength="500" placeholder="Create and manage scheduled, recurring, and triggered reminders."></textarea><small>Keep this concise; it is included in the compact catalogue sent with normal requests.</small></label><label>Availability<select id="group-loading-mode"><option value="on_demand">Load when needed</option><option value="always">Always available</option></select><small>On-demand groups add one model round-trip the first time they are needed in an active conversation.</small></label><fieldset class="group-functions-fieldset"><legend>Functions</legend><input id="group-function-search" type="search" placeholder="Search functions..." aria-label="Search functions to assign"><small>Selecting a function moves it from any other group. Unselected functions are not deleted.</small><div id="group-functions" class="group-function-choices"></div></fieldset><div id="group-error" class="validation invalid" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="group-cancel">Cancel</button><button type="button" id="group-save">Keep in draft</button></div></dialog>${helpPopover()}`;
}
