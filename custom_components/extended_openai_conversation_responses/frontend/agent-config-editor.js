import { bindHelp, helpButton, helpPopover, helpSearchTerms } from "./agent-config-help.js";

const clone = (value) => JSON.parse(JSON.stringify(value));
const bool = (value) => value ? "checked" : "";
const option = (panel, value, selected, label = null) => `<option value="${panel._e(value)}" ${value === selected ? "selected" : ""}>${panel._e(label || panel._titleCase(value))}</option>`;
const settingSearch = (label, description, key, helpKey = null) => `${label} ${description} ${key} ${helpKey ? helpSearchTerms(helpKey) : ""}`.toLowerCase();
const labelRow = (panel, label, key, helpKey = null, strong = false) => `<span class="setting-label-row"><label for="config-${key}">${strong ? `<strong>${label}</strong>` : label}</label>${helpKey ? helpButton(panel, helpKey) : ""}</span>`;
const field = (panel, key, label, value, type = "text", description = "", disabled = false, helpKey = null) => `<div class="setting" data-field="${key}" data-setting data-search="${panel._e(settingSearch(label, description, key, helpKey))}">${labelRow(panel, label, key, helpKey)}<input id="config-${key}" data-config="${key}" data-type="${type}" type="${type === "number" ? "number" : "text"}" value="${panel._e(value)}" ${disabled ? "disabled" : ""}>${description ? `<small>${description}</small>` : ""}<span class="field-error" data-error="${key}"></span></div>`;
const select = (panel, key, label, value, options, description = "", disabled = false, helpKey = null) => `<div class="setting" data-field="${key}" data-setting data-search="${panel._e(settingSearch(label, description, key, helpKey))}">${labelRow(panel, label, key, helpKey)}<select id="config-${key}" data-config="${key}" ${disabled ? "disabled" : ""}>${options.map((item) => option(panel, typeof item === "string" ? item : item.value, value, typeof item === "string" ? null : item.label)).join("")}</select>${description ? `<small>${description}</small>` : ""}<span class="field-error" data-error="${key}"></span></div>`;
const toggle = (panel, key, label, value, description = "", disabled = false, helpKey = null) => `<div class="config-toggle setting" data-field="${key}" data-setting data-search="${panel._e(settingSearch(label, description, key, helpKey))}"><span class="setting-copy">${labelRow(panel, label, key, helpKey, true)}${description ? `<small>${description}</small>` : ""}</span><label class="switch-control" for="config-${key}"><input id="config-${key}" data-config="${key}" data-type="boolean" type="checkbox" role="switch" ${bool(value)} ${disabled ? "disabled" : ""}><span class="switch-track" aria-hidden="true"></span></label></div>`;

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
  const choices = (key) => panel._result?.options?.[key] || [];
  const cleanOnly = panel._configDirty ? "disabled title=\"Save or revert the shared draft first\"" : "";
  const jumps = [["general","General"],["prompt","Prompt"],["capabilities","Capabilities"],["archive","Archive"],["voice","Voice"],["speech","Speech"],["context","Context"],["model","Model"],["retention","Retention"]];
  return `<div class="config-toolbar"><input id="config-search" type="search" value="${panel._e(panel._configSearchQuery || "")}" placeholder="Search settings..." aria-label="Search configuration"><div class="agent-actions"><button type="button" class="secondary" id="duplicate-agent" ${cleanOnly}>Duplicate</button><button type="button" class="secondary" id="export-agent" ${cleanOnly}>Export</button><button type="button" class="secondary" id="import-agent">Import</button></div></div>${panel._configDirty ? `<p class="action-help">Duplicate and Export use the saved configuration. Save or revert this shared draft to enable them.</p>` : ""}
    <nav class="config-jumps" aria-label="Jump to configuration section">${jumps.map(([id,label])=>`<a href="#config-${id}" data-jump="config-${id}">${label}</a>`).join("<span aria-hidden=\"true\">&middot;</span>")}</nav>
    <div class="content-card config-surface">
      ${section(panel,"general","General","Core model and conversation behaviour.","model api tokens function calls continue",`<div class="form-grid general-grid">${field(panel,"__title","Agent name",panel._draftTitle ?? panel._result?.title ?? "")}${field(panel,"chat_model","Chat model",config.chat_model)}${select(panel,"api_mode","API mode",config.api_mode,choices("api_mode"),"Chooses which OpenAI-compatible API style is used for this agent.",false,"api_mode")}${field(panel,"max_tokens","Maximum output tokens",config.max_tokens,"number","Limits the number of tokens the model may generate in a response.")}${field(panel,"max_function_calls_per_conversation","Maximum function calls",config.max_function_calls_per_conversation,"number","Limits tool calls in one conversation to prevent runaway execution.")}${select(panel,"continue_conversation","Continue conversation",config.continue_conversation,choices("continue_conversation"),"Controls whether Home Assistant should keep listening for an immediate follow-up after a response.",false,"continue_conversation")}</div>`)}
      ${section(panel,"prompt","Prompt","System instructions used by this agent.","template instructions",`<label class="setting prompt-setting" data-setting data-search="prompt system instructions template"><span class="sr-only">System prompt</span><textarea id="prompt-editor" data-config="prompt" class="prompt-editor" spellcheck="false">${panel._e(config.prompt || "")}</textarea></label><div class="editor-meta"><span id="prompt-count">${(config.prompt || "").length.toLocaleString()} characters</span><button type="button" class="secondary compact-button" id="reset-prompt">Reset to default</button></div><span class="field-error" data-error="prompt"></span>`)}
      ${section(panel,"capabilities","Capabilities","Provider and data features available to the agent.","web search skills knowledge memory",`<div class="config-stack">${toggle(panel,"web_search","Web search",config.web_search,"Allows the agent to search the web for current information.")}<div class="dependent ${config.web_search ? "" : "is-disabled"}" data-dependent="web_search">${select(panel,"web_search_context","Search context",config.web_search_context,choices("web_search_context"),"Controls how much information the provider returns from web searches.",!config.web_search,"web_search_context")}</div>${toggle(panel,"knowledge_enabled","Knowledge Library",config.knowledge_enabled,"Allows the agent to search locally maintained reference sources; this is separate from persistent memory and conversation history.",false,"knowledge_library")}${select(panel,"memory_mode","Memory mode",config.memory_mode,choices("memory_mode"),"Controls whether durable facts can be remembered and reused across future conversations.",false,"memory_mode")}${field(panel,"skills","Skills",(config.skills || []).join(", "),"text","Selects reusable installed instruction sets that the agent can load when needed; enter comma-separated names.")}</div>`)}
      ${section(panel,"archive","Archive","Retain conversations locally for search and continuity.","retention shared session timeout model search",`<div class="config-stack">${toggle(panel,"archive_enabled","Conversation archive",archive,"Stores conversation history locally for later review and optional retrieval.")}<div class="dependent ${archive ? "" : "is-disabled"}" data-dependent="archive_enabled"><div class="form-grid">${select(panel,"archive_retention_days","Archive retention",config.archive_retention_days,choices("archive_retention_days"),"Controls how long retained conversations are kept.",!archive)}${field(panel,"archive_session_timeout_minutes","Session timeout (minutes)",config.archive_session_timeout_minutes,"number","Controls how long inactivity can last before a new conversation archive session is started.",!archive,"archive_session_timeout")}</div>${toggle(panel,"archive_model_search_enabled","Allow model archive search",config.archive_model_search_enabled,"Allows the agent itself to search retained conversations when prior discussions may be relevant.",!archive,"archive_model_search")}${toggle(panel,"shared_archive_enabled","Shared-household archive",config.shared_archive_enabled,"Makes eligible retained conversations available in the shared household scope.",!archive,"shared_archive")}</div></div>`)}
      ${section(panel,"voice","Voice & identity","Control how voice requests map to owners and shared data.","unidentified unmapped default owner shared memory satellite device mappings",`<div class="form-grid">${select(panel,"voice_scope_policy","Unidentified voice policy",config.voice_scope_policy,choices("voice_scope_policy"),"Chooses where data from a voice request belongs when the speaker cannot be identified.",false,"voice_scope_policy")}${select(panel,"voice_unmapped_policy","Unmapped fallback",config.voice_unmapped_policy,choices("voice_unmapped_policy"),"Chooses what happens when a voice or device mapping does not resolve to a known owner.",false,"voice_unmapped_policy")}${field(panel,"voice_default_user_id","Default voice owner",config.voice_default_user_id || "","text","Selects the Home Assistant user ID that receives ownership when a policy uses a default user.")}${select(panel,"shared_memory_mode","Shared memory mode",config.shared_memory_mode,choices("shared_memory_mode"),"Controls whether persistent memories may be stored in the shared household scope.",false,"shared_memory_mode")}</div><div class="setting mappings-setting" data-setting data-search="${panel._e(settingSearch("Satellite/device mappings", "Maps voice satellite and device IDs to specific user or shared scopes.", "voice_device_mappings", "voice_device_mappings"))}"><span class="setting-label-row"><label for="voice-mappings">Satellite/device mappings (JSON)</label>${helpButton(panel,"voice_device_mappings")}</span><textarea id="voice-mappings" class="yaml-editor mappings-editor" spellcheck="false">${panel._e(JSON.stringify(config.voice_device_mappings || {}, null, 2))}</textarea><small>Maps voice satellite and device IDs to specific user or shared scopes.</small><span class="field-error" data-error="voice_device_mappings"></span></div>`)}
      ${section(panel,"speech","Spoken response","Clean text before it is spoken while retaining the original assistant response for history and context.","tts markdown url regex replacements preview",`<div class="config-stack">${toggle(panel,"speech_processing_enabled","Speech post-processing",speech,"Cleans text before it is spoken without changing the retained original assistant response.")}<div class="dependent ${speech ? "" : "is-disabled"}" data-dependent="speech_processing_enabled">${toggle(panel,"speech_strip_markdown","Remove Markdown links and formatting",config.speech_strip_markdown,"Removes Markdown links and formatting from progressive and completed spoken output.",!speech)}${toggle(panel,"speech_strip_urls","Remove bare URLs",config.speech_strip_urls,"Prevents raw web addresses from being spoken, including during progressive speech.",!speech)}<div class="setting-group" data-setting data-search="${panel._e(settingSearch("Custom replacements", "Rules run on the completed response and affect spoken output only.", "speech_regex_replacements", "custom_replacements"))}"><div class="subheading"><span class="setting-label-row"><h3>Custom replacements</h3>${helpButton(panel,"custom_replacements")}</span><p>Rules run on the completed response. Progressive TTS is disabled when custom rules are configured.</p></div><div class="rule-headings" aria-hidden="true"><span>Pattern</span><span>Replacement</span><span>Actions</span></div><div id="regex-rules" class="rule-list">${regexRules.map((rule,index)=>regexRow(panel,rule,index,!speech)).join("")}</div><button type="button" class="secondary add-regex" id="add-regex" ${speech ? "" : "disabled"}>+ Add rule</button></div><div class="preview-grid setting-group" data-setting data-search="spoken text preview sample assistant"><label>Sample assistant text<textarea id="speech-sample" class="short-textarea" placeholder="Paste a response containing links or abbreviations" ${speech ? "" : "disabled"}></textarea></label><label>Preview spoken text<textarea id="speech-output" class="short-textarea" readonly></textarea></label></div><button type="button" class="secondary preview-button" id="preview-speech" ${speech ? "" : "disabled"}>Preview spoken text</button></div></div>`)}
      ${section(panel,"context","Context","Choose when and how long conversations are condensed.","threshold truncate summarize recent clear",`<div class="form-grid">${field(panel,"context_threshold","Context threshold",config.context_threshold,"number","Controls how large a conversation can become before older context is reduced.",false,"context_threshold")}${select(panel,"context_truncate_strategy","Truncation strategy",config.context_truncate_strategy,choices("context_truncate_strategy"),"Chooses what happens to older conversation content once the context threshold is reached.",false,"context_truncation")}</div>`)}
      ${section(panel,"model","Model parameters","Only parameters supported by the selected model are shown.","temperature top p reasoning effort service tier tool id memory retrieve",`<div class="form-grid">${capabilities.supports_temperature ? field(panel,"temperature","Temperature",config.temperature,"number","Controls response randomness and variation when supported by the selected model.") : ""}${capabilities.supports_top_p ? field(panel,"top_p","Top P",config.top_p,"number","Controls token sampling diversity when supported by the selected model.") : ""}${capabilities.supports_reasoning_effort ? select(panel,"reasoning_effort","Reasoning effort",config.reasoning_effort || "low",choices("reasoning_effort"),"Controls how much reasoning the model uses, typically trading latency and cost for harder-task performance.",false,"reasoning_effort") : ""}${capabilities.supports_service_tier ? select(panel,"service_tier","Service tier",config.service_tier || "flex",choices("service_tier"),"Selects the provider processing tier when supported.",false,"service_tier") : ""}${toggle(panel,"shorten_tool_call_id","Shorten tool-call IDs",config.shorten_tool_call_id,"Compatibility option for providers that require shorter tool-call identifiers. Leave disabled unless the provider requires it.")}${field(panel,"memory_auto_retrieve_limit","Automatic memory retrieval limit",config.memory_auto_retrieve_limit,"number","Limits how many relevant persistent memories are included with each request; it is not a storage or total-memory limit.")}</div><button type="button" class="secondary compact-button section-reset" id="reset-advanced">Reset model parameters</button>`)}
      ${section(panel,"retention","Usage detail retention","Aggregate and lifetime totals remain separate from these detailed records.","usage request run details totals",`<div class="form-grid">${select(panel,"usage_request_retention_days","Request details",config.usage_request_retention_days,choices("usage_request_retention_days"),"Controls how long detailed per-request usage records are retained.")}${select(panel,"usage_run_retention_days","Run details",config.usage_run_retention_days,choices("usage_run_retention_days"),"Controls how long detailed conversation-run records are retained.")}</div>`)}
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
  root.querySelector("#reset-prompt")?.addEventListener("click", () => { const editor=root.querySelector("#prompt-editor"); editor.value=panel._result.defaults.prompt; editor.focus(); dirty(panel); root.querySelector("#prompt-count").textContent=`${editor.value.length.toLocaleString()} characters`; });
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
  root.querySelector("#import-preview")?.addEventListener("click", async () => { try { const result=await panel._call("configuration","import_preview",{document:root.querySelector("#import-document").value}); panel._importDocument=root.querySelector("#import-document").value; root.querySelector("#import-summary").textContent=`${result.title} / ${result.summary.model} / ${result.summary.tools} tools / ${result.summary.speech_rules} speech rules`; root.querySelector("#import-apply").disabled=false; } catch(err){root.querySelector("#import-summary").textContent=err.message||String(err);root.querySelector("#import-apply").disabled=true;} });
  root.querySelector("#import-apply")?.addEventListener("click", async () => { const mode=root.querySelector('input[name="import-mode"]:checked').value; if(mode==="current"&&!await panel._confirm("Overwrite this agent?",`The saved configuration will be replaced.${panel._configDirty ? " Your unsaved shared draft will be discarded." : ""} Retained history and parent-entry credentials are not affected.`,"Overwrite"))return; try { await panel._call("configuration","import",{document:panel._importDocument,mode,confirm:mode==="current"}); root.querySelector("#import-dialog").close(); if(mode==="current")panel._clearConfigDraft(); await panel._loadAgents(panel._agentId); panel._toast(mode==="current"?"Configuration imported":"Agent created from import; your current draft is preserved"); } catch(err){panel._toast(`Unable to import: ${err.message||String(err)}`,true);} });
  root.querySelector("#import-cancel")?.addEventListener("click",()=>root.querySelector("#import-dialog").close());
  if (panel._configRestoreFocus) { const selector=panel._configRestoreFocus; panel._configRestoreFocus=null; requestAnimationFrame(()=>root.querySelector(selector)?.focus({preventScroll:true})); }
}

export function renderTools(panel) {
  const tools = panel._draft?.functions || panel._result?.config?.functions || [];
  return `<section class="content-card tools-surface"><div class="section-heading"><div><span class="setting-label-row"><h2>Function Tools</h2>${helpButton(panel,"function_tools")}</span><p>Browse configured tools and edit each complete definition as backend-validated YAML.</p></div><button type="button" id="add-tool">+ Add Function Tool</button></div><div class="list tool-list">${tools.map((tool,index)=>`<article class="list-card tool-card" data-tool-index="${index}"><div class="card-main"><div class="tool-title"><h3>${panel._e(tool.spec?.name||"Unnamed tool")}</h3><span class="type-badge">${panel._e(tool.function?.type||"Unknown type")}</span></div><p class="description">${panel._e(tool.spec?.description||"No description")}</p></div><div class="actions"><button type="button" class="secondary edit-tool" data-index="${index}">Edit</button><button type="button" class="secondary duplicate-tool" data-index="${index}">Duplicate</button><button type="button" class="danger delete-tool" data-index="${index}">Delete</button></div></article>`).join("")||panel._empty("No function tools configured.")}</div><div class="section-actions tools-actions"><button type="button" class="secondary" id="validate-tools">Validate all</button><button type="button" id="save-tools" ${panel._configDirty?"":"disabled"}>Save tools</button><span id="tool-status" class="validation" aria-live="polite"></span></div></section>`;
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
  dialog.showModal();
  try {
    const response=index===null ? await panel._call("tools","starter") : await panel._call("tools","serialize",{tool:panel._draft.functions[index]});
    root.querySelector("#tool-yaml").value=response.yaml;
    panel._toolInitialYaml=response.yaml;
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

export function bindTools(panel) {
  const root=panel.shadowRoot;
  bindHelp(panel);
  root.querySelector("#add-tool")?.addEventListener("click",()=>openTool(panel));
  root.querySelectorAll(".edit-tool").forEach(button=>button.addEventListener("click",()=>openTool(panel,Number(button.dataset.index))));
  root.querySelectorAll(".duplicate-tool").forEach(button=>button.addEventListener("click",()=>{const tool=clone(panel._draft.functions[Number(button.dataset.index)]);let base=`${tool.spec.name}_copy`,name=base,n=2;const names=new Set(panel._draft.functions.map(item=>item.spec?.name));while(names.has(name))name=`${base}_${n++}`;tool.spec.name=name;panel._draft.functions.push(tool);panel._setConfigDirty(true);panel._render();}));
  root.querySelectorAll(".delete-tool").forEach(button=>button.addEventListener("click",async()=>{if(!await panel._confirm("Delete function tool?","The tool will be removed from this local draft. Save tools to apply the change.","Delete"))return;panel._draft.functions.splice(Number(button.dataset.index),1);panel._setConfigDirty(true);panel._render();}));
  root.querySelector("#validate-tools")?.addEventListener("click",async()=>{const status=root.querySelector("#tool-status");try{const result=await panel._call("tools","validate",{tools:panel._draft.functions});status.className=`validation ${result.valid?"valid":"invalid"}`;status.textContent=result.valid?"All tools are valid":toolErrorText(result.errors);}catch(err){status.className="validation invalid";status.textContent=err.message||String(err);}});
  root.querySelector("#save-tools")?.addEventListener("click",async()=>{try{const draft=clone(panel._draft);const title=panel._draftTitle;const result=await panel._call("configuration","update",{config:{functions:draft.functions}});panel._configData={...panel._configData,...result};panel._result=panel._configData;panel._draft={...draft,functions:clone(result.config.functions)};panel._draftTitle=title;panel._syncConfigDirty();panel._toast("Function tools saved; other draft changes are preserved");panel._render();}catch(err){panel._toast(`Unable to save tools: ${err.message||String(err)}`,true);}});
  root.querySelector("#tool-yaml")?.addEventListener("input",()=>{root.querySelector("#tool-error").className="validation";root.querySelector("#tool-error").textContent="YAML changed; validate to refresh metadata.";});
  root.querySelector("#tool-cancel")?.addEventListener("click",()=>root.querySelector("#tool-dialog").close());
  root.querySelector("#tool-dialog")?.addEventListener("cancel",()=>{panel._toolIndex=null;});
  root.querySelector("#tool-validate")?.addEventListener("click",()=>validateDialogTool(panel));
  root.querySelector("#tool-save")?.addEventListener("click",async()=>{const tool=await validateDialogTool(panel);if(!tool)return;if(panel._toolIndex===null)panel._draft.functions.push(tool);else panel._draft.functions[panel._toolIndex]=tool;panel._setConfigDirty(true);root.querySelector("#tool-dialog").close();panel._render();});
}

export function configurationDialogs(panel) {
  return `<dialog id="import-dialog" class="editor-dialog wide" aria-labelledby="import-dialog-title"><div class="dialog-header"><h2 id="import-dialog-title">Import agent configuration</h2></div><div class="dialog-body">${panel._configDirty ? `<div class="notice"><strong>Unsaved shared draft</strong><p>Creating a new agent preserves this draft. Overwriting the current agent discards it after confirmation.</p></div>` : ""}<label>Exported JSON or YAML<textarea id="import-document" class="yaml-editor" spellcheck="false"></textarea></label><div class="mode-row"><label><input type="radio" name="import-mode" value="new" checked> Create a new agent</label><label><input type="radio" name="import-mode" value="current"> Overwrite current agent</label></div><div id="import-summary" class="validation" aria-live="polite">Validate the document to preview it.</div></div><div class="dialog-actions"><button type="button" class="secondary" id="import-cancel">Cancel</button><button type="button" class="secondary" id="import-preview">Validate & preview</button><button type="button" id="import-apply" disabled>Import</button></div></dialog>
  <dialog id="tool-dialog" class="editor-dialog tool-dialog" aria-labelledby="tool-dialog-title"><div class="dialog-header"><div><span class="setting-label-row"><h2 id="tool-dialog-title">Function Tool</h2>${helpButton(panel,"function_tools")}</span><p id="tool-dialog-meta" class="dialog-meta">YAML</p></div></div><div class="dialog-body tool-dialog-body"><label class="tool-editor-label"><span class="sr-only">Function Tool YAML</span><textarea id="tool-yaml" class="yaml-editor tool-yaml-editor" spellcheck="false" wrap="off" aria-describedby="tool-error"></textarea></label><div id="tool-error" class="validation" role="status" aria-live="polite"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="tool-cancel">Cancel</button><button type="button" class="secondary" id="tool-validate">Validate</button><button type="button" id="tool-save">Keep in draft</button></div></dialog>${helpPopover()}`;
}
