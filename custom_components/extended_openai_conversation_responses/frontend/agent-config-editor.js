const clone = (value) => JSON.parse(JSON.stringify(value));
const bool = (value) => value ? "checked" : "";
const option = (panel, value, selected, label = null) => `<option value="${panel._e(value)}" ${value === selected ? "selected" : ""}>${panel._e(label || panel._titleCase(value))}</option>`;
const field = (panel, key, label, value, type = "text", description = "") => `<label data-field="${key}"><span>${label}</span><input data-config="${key}" data-type="${type}" type="${type === "number" ? "number" : "text"}" value="${panel._e(value)}">${description ? `<small>${description}</small>` : ""}<span class="field-error" data-error="${key}"></span></label>`;
const select = (panel, key, label, value, options, description = "") => `<label data-field="${key}"><span>${label}</span><select data-config="${key}">${options.map((item) => option(panel, typeof item === "string" ? item : item.value, value, typeof item === "string" ? null : item.label)).join("")}</select>${description ? `<small>${description}</small>` : ""}<span class="field-error" data-error="${key}"></span></label>`;
const toggle = (panel, key, label, value, description = "") => `<label class="config-toggle" data-field="${key}"><span><strong>${label}</strong>${description ? `<small>${description}</small>` : ""}</span><input data-config="${key}" data-type="boolean" type="checkbox" role="switch" ${bool(value)}></label>`;

function card(title, description, search, body, classes = "") {
  return `<section class="content-card config-card ${classes}" data-search="${search}"><div class="section-heading"><div><h2>${title}</h2><p>${description}</p></div></div>${body}</section>`;
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
  return `<div class="config-toolbar"><input id="config-search" type="search" placeholder="Find reasoning, archive, voice, context, regex…" aria-label="Search configuration"><div class="agent-actions"><button class="secondary" id="duplicate-agent">Duplicate</button><button class="secondary" id="export-agent">Export</button><button class="secondary" id="import-agent">Import</button></div></div>
    <div id="config-grid" class="config-grid">
      ${card("General", "Core model and conversation behaviour.", "general model api tokens function calls continue", `<div class="form-grid">${field(panel, "__title", "Agent name", panel._draftTitle ?? panel._result?.title ?? "")}${field(panel, "chat_model", "Chat model", config.chat_model)}${select(panel, "api_mode", "API mode", config.api_mode, [{value:"auto",label:"Auto"},{value:"chat_completions",label:"Chat Completions"},{value:"responses",label:"Responses"}])}${field(panel, "max_tokens", "Maximum output tokens", config.max_tokens, "number")}${field(panel, "max_function_calls_per_conversation", "Maximum function calls", config.max_function_calls_per_conversation, "number")}${select(panel, "continue_conversation", "Continue conversation", config.continue_conversation, ["ha_default","always","conditional"])}</div>`) }
      ${card("Prompt", "A roomy template-aware system prompt editor.", "prompt template system instructions", `<label><textarea id="prompt-editor" data-config="prompt" class="prompt-editor" spellcheck="false">${panel._e(config.prompt || "")}</textarea></label><div class="editor-meta"><span id="prompt-count">${(config.prompt || "").length.toLocaleString()} characters</span><button type="button" class="secondary compact-button" id="reset-prompt">Reset to default</button></div><span class="field-error" data-error="prompt"></span>`, "config-span")}
      ${card("Capabilities", "Enable only the data and provider features this agent needs.", "capabilities web search skills knowledge memory", `<div class="config-stack">${toggle(panel,"web_search","Web search",config.web_search,"Use the provider's hosted web search when supported.")}<div class="dependent ${config.web_search ? "" : "hidden"}" data-dependent="web_search">${select(panel,"web_search_context","Search context",config.web_search_context,["low","medium","high"])}</div>${toggle(panel,"knowledge_enabled","Knowledge Library",config.knowledge_enabled)}${select(panel,"memory_mode","Memory mode",config.memory_mode,["off","manual","automatic"])}${field(panel,"skills","Skills",(config.skills || []).join(", "),"text","Comma-separated installed skill names.")}</div>`)}
      ${card("Archive", "Retain conversations locally for search and continuity.", "archive retained retention shared session timeout", `<div class="config-stack">${toggle(panel,"archive_enabled","Conversation archive",archive)}<div class="dependent ${archive ? "" : "hidden"}" data-dependent="archive_enabled"><div class="form-grid">${select(panel,"archive_retention_days","Retention",config.archive_retention_days,[7,30,90,180,365].map(v=>({value:v,label:`${v} days`})))}${field(panel,"archive_session_timeout_minutes","Session timeout (minutes)",config.archive_session_timeout_minutes,"number")}</div>${toggle(panel,"archive_model_search_enabled","Allow model archive search",config.archive_model_search_enabled)}${toggle(panel,"shared_archive_enabled","Shared-household archive",config.shared_archive_enabled)}</div></div>`)}
      ${card("Voice, identity & speech", "Control data ownership and clean TTS without changing the visual response.", "voice identity speech tts markdown urls regex mappings shared memory", `<div class="form-grid">${select(panel,"voice_scope_policy","Unidentified voice policy",config.voice_scope_policy,["unretained","shared","default_user","device_mapping"])}${select(panel,"voice_unmapped_policy","Unmapped fallback",config.voice_unmapped_policy,["unretained","shared","default_user"])}${field(panel,"voice_default_user_id","Default voice owner",config.voice_default_user_id || "")}${select(panel,"shared_memory_mode","Shared memory mode",config.shared_memory_mode,["disabled","explicit","automatic"])}</div><details><summary>Advanced identity mappings</summary><div class="advanced-body"><label>Satellite mappings (JSON)<textarea id="voice-mappings" class="yaml-editor" spellcheck="false">${panel._e(JSON.stringify(config.voice_device_mappings || {}, null, 2))}</textarea></label><span class="field-error" data-error="voice_device_mappings"></span></div></details><div class="subsection">${toggle(panel,"speech_processing_enabled","Speech post-processing",speech,"Affects spoken output only; the visual response, archive, and memory context remain unchanged.")}<div class="dependent ${speech ? "" : "hidden"}" data-dependent="speech_processing_enabled">${toggle(panel,"speech_strip_markdown","Remove Markdown links and formatting",config.speech_strip_markdown)}${toggle(panel,"speech_strip_urls","Remove bare URLs",config.speech_strip_urls)}<details><summary>Custom regex replacements</summary><p class="help">Rules run in order on spoken output only. Invalid expressions are rejected when saving.</p><div id="regex-rules" class="rule-list">${regexRules.map((rule,index)=>regexRow(panel,rule,index)).join("")}</div><button type="button" class="secondary" id="add-regex">+ Add rule</button></details><div class="preview-grid"><label>Sample assistant text<textarea id="speech-sample" class="short-textarea" placeholder="Paste a response containing links or abbreviations"></textarea></label><label>Preview spoken text<textarea id="speech-output" class="short-textarea" readonly></textarea></label></div><button type="button" class="secondary" id="preview-speech">Preview spoken text</button></div></div>` , "config-span")}
      ${card("Context", "Choose when and how long conversations are condensed.", "context threshold truncate summarize recent clear", `<div class="form-grid">${field(panel,"context_threshold","Context threshold",config.context_threshold,"number")}${select(panel,"context_truncate_strategy","Truncation strategy",config.context_truncate_strategy,[{value:"keep_recent",label:"Keep recent messages"},{value:"clear",label:"Clear all messages"},{value:"summarize",label:"Summarize older messages"}])}</div>`) }
      ${card("Model parameters", "Only parameters supported by the selected model are shown.", "advanced model temperature top p reasoning effort service tier tool id memory retrieve", `<details open><summary>Advanced model options</summary><div class="advanced-body form-grid">${capabilities.supports_temperature ? field(panel,"temperature","Temperature",config.temperature,"number") : ""}${capabilities.supports_top_p ? field(panel,"top_p","Top P",config.top_p,"number") : ""}${capabilities.supports_reasoning_effort ? select(panel,"reasoning_effort","Reasoning effort",config.reasoning_effort || "low",["low","medium","high"]) : ""}${capabilities.supports_service_tier ? select(panel,"service_tier","Service tier",config.service_tier || "flex",["auto","default","flex","priority"]) : ""}${toggle(panel,"shorten_tool_call_id","Shorten tool-call IDs",config.shorten_tool_call_id,"Enable only for providers that require short IDs.")}${field(panel,"memory_auto_retrieve_limit","Automatic memory retrieval limit",config.memory_auto_retrieve_limit,"number")}</div></details><button type="button" class="secondary compact-button" id="reset-advanced">Reset model parameters</button>`) }
      ${card("Usage detail retention", "Aggregate totals remain; these settings control detailed request/run records.", "usage request run retention details", `<div class="form-grid">${select(panel,"usage_request_retention_days","Request details",config.usage_request_retention_days,[0,7,30,90,180,365].map(v=>({value:v,label:v?`${v} days`:"Disabled"})))}${select(panel,"usage_run_retention_days","Run details",config.usage_run_retention_days,[0,7,30,90,180,365].map(v=>({value:v,label:v?`${v} days`:"Disabled"})))}</div>`) }
    </div>${saveBar(panel)}<span class="sr-only" data-defaults="${panel._e(JSON.stringify(defaults))}"></span>`;
}

function regexRow(panel, rule, index) {
  return `<article class="rule-row" data-regex-index="${index}"><label>Pattern<input class="regex-pattern" value="${panel._e(rule.pattern || "")}" spellcheck="false"></label><label>Replacement<input class="regex-replacement" value="${panel._e(rule.replacement || "")}" spellcheck="false"></label><div class="rule-actions"><button type="button" class="secondary move-regex" data-direction="-1" aria-label="Move rule up">↑</button><button type="button" class="secondary move-regex" data-direction="1" aria-label="Move rule down">↓</button><button type="button" class="danger delete-regex">Delete</button></div><span class="field-error" data-error="speech_regex_replacements[${index}].pattern"></span></article>`;
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
  try {
    config.voice_device_mappings = JSON.parse(root.querySelector("#voice-mappings")?.value || "{}");
  } catch (_) {
    config.voice_device_mappings = root.querySelector("#voice-mappings")?.value;
  }
  panel._draftTitle = root.querySelector('[data-config="__title"]')?.value ?? panel._draftTitle;
  panel._draft = config;
  return config;
}

function dirty(panel) {
  readConfig(panel);
  panel._configDirty = true;
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

export function bindConfiguration(panel) {
  const root = panel.shadowRoot;
  root.querySelectorAll("[data-config],#voice-mappings,.regex-pattern,.regex-replacement").forEach((input) => input.addEventListener("input", () => {
    dirty(panel);
    if (input.dataset.type === "boolean") root.querySelector(`[data-dependent="${input.dataset.config}"]`)?.classList.toggle("hidden", !input.checked);
    if (input.id === "prompt-editor") root.querySelector("#prompt-count").textContent = `${input.value.length.toLocaleString()} characters`;
  }));
  root.querySelector("#config-search")?.addEventListener("input", (event) => {
    const query = event.target.value.trim().toLowerCase();
    root.querySelectorAll(".config-card").forEach((item) => item.hidden = Boolean(query) && !item.dataset.search.includes(query) && !item.textContent.toLowerCase().includes(query));
  });
  root.querySelector('[data-config="chat_model"]')?.addEventListener("change", async () => {
    try {
      const validation = await panel._call("configuration", "validate", { config: readConfig(panel) });
      if (validation.valid) {
        panel._result.model_capabilities = validation.model_capabilities;
        panel._configDirty = true;
        panel._render();
      }
    } catch (err) {
      panel._toast(`Unable to inspect model options: ${err.message || String(err)}`, true);
    }
  });
  root.querySelector("#reset-prompt")?.addEventListener("click", () => { root.querySelector("#prompt-editor").value = panel._result.defaults.prompt; dirty(panel); panel._render(); });
  root.querySelector("#reset-advanced")?.addEventListener("click", () => { ["temperature","top_p","reasoning_effort","service_tier","shorten_tool_call_id","memory_auto_retrieve_limit"].forEach(key => panel._draft[key] = clone(panel._result.defaults[key])); panel._configDirty = true; panel._render(); });
  root.querySelector("#add-regex")?.addEventListener("click", () => { readConfig(panel); panel._draft.speech_regex_replacements.push({pattern:"",replacement:""}); panel._configDirty = true; panel._render(); });
  root.querySelectorAll(".delete-regex").forEach((button) => button.addEventListener("click", () => { const index=Number(button.closest(".rule-row").dataset.regexIndex); readConfig(panel); panel._draft.speech_regex_replacements.splice(index,1); panel._configDirty=true; panel._render(); }));
  root.querySelectorAll(".move-regex").forEach((button) => button.addEventListener("click", () => { const index=Number(button.closest(".rule-row").dataset.regexIndex); const target=index+Number(button.dataset.direction); readConfig(panel); if(target<0||target>=panel._draft.speech_regex_replacements.length)return; [panel._draft.speech_regex_replacements[index],panel._draft.speech_regex_replacements[target]]=[panel._draft.speech_regex_replacements[target],panel._draft.speech_regex_replacements[index]]; panel._configDirty=true; panel._render(); }));
  root.querySelector("#revert-config")?.addEventListener("click", () => { panel._draft=clone(panel._result.config); panel._draftTitle=panel._result.title; panel._configDirty=false; panel._render(); });
  root.querySelector("#save-config")?.addEventListener("click", async () => {
    const button=root.querySelector("#save-config"); panel._setSaving(button,true);
    try { const config=readConfig(panel); const validation=await panel._call("configuration","validate",{config}); showErrors(panel,validation.errors); if(!validation.valid){panel._toast("Fix the highlighted configuration errors",true);return;} const saved=await panel._call("configuration","update",{config,title:panel._draftTitle}); panel._result={...panel._result,...saved}; panel._draft=clone(saved.config); panel._configDirty=false; panel._toast("Configuration saved"); await panel._loadAgents(panel._agentId); }
    catch(err){panel._toast(`Unable to save configuration: ${err.message||String(err)}`,true);} finally{panel._setSaving(button,false);}
  });
  root.querySelector("#preview-speech")?.addEventListener("click", async () => { try { const response=await panel._call("configuration","speech_preview",{config:readConfig(panel),sample_text:root.querySelector("#speech-sample").value}); root.querySelector("#speech-output").value=response.speech_text; } catch(err){panel._toast(`Unable to preview speech: ${err.message||String(err)}`,true);} });
  root.querySelector("#duplicate-agent")?.addEventListener("click", async () => { try { const result=await panel._call("configuration","duplicate"); await panel._loadAgents(result.subentry_id); panel._toast(`Created ${result.title}`); } catch(err){panel._toast(`Unable to duplicate agent: ${err.message||String(err)}`,true);} });
  root.querySelector("#export-agent")?.addEventListener("click", async () => { const result=await panel._call("configuration","export"); const blob=new Blob([result.json],{type:"application/json"}); const url=URL.createObjectURL(blob); const link=document.createElement("a"); link.href=url; link.download=`${(panel._draftTitle||"agent").replace(/[^a-z0-9]+/gi,"-").toLowerCase()}.json`; link.click(); URL.revokeObjectURL(url); });
  root.querySelector("#import-agent")?.addEventListener("click", () => root.querySelector("#import-dialog")?.showModal());
  root.querySelector("#import-preview")?.addEventListener("click", async () => { try { const result=await panel._call("configuration","import_preview",{document:root.querySelector("#import-document").value}); panel._importDocument=root.querySelector("#import-document").value; root.querySelector("#import-summary").textContent=`${result.title} · ${result.summary.model} · ${result.summary.tools} tools · ${result.summary.speech_rules} speech rules`; root.querySelector("#import-apply").disabled=false; } catch(err){root.querySelector("#import-summary").textContent=err.message||String(err);root.querySelector("#import-apply").disabled=true;} });
  root.querySelector("#import-apply")?.addEventListener("click", async () => { const mode=root.querySelector('input[name="import-mode"]:checked').value; if(mode==="current"&&!await panel._confirm("Overwrite this agent?","The current agent configuration will be replaced. Retained history and credentials are not affected.","Overwrite"))return; try { const result=await panel._call("configuration","import",{document:panel._importDocument,mode,confirm:mode==="current"}); root.querySelector("#import-dialog").close(); await panel._loadAgents(result.subentry_id); panel._toast(mode==="current"?"Configuration imported":"Agent created from import"); } catch(err){panel._toast(`Unable to import: ${err.message||String(err)}`,true);} });
  root.querySelector("#import-cancel")?.addEventListener("click",()=>root.querySelector("#import-dialog").close());
}

export function renderTools(panel) {
  const tools = panel._draft?.functions || panel._result?.config?.functions || [];
  return `<section class="content-card"><div class="section-heading"><div><h2>Function Tools</h2><p>Structured editing for common fields, with YAML for complete control.</p></div><button type="button" id="add-tool">+ Add Function Tool</button></div><div class="list">${tools.map((tool,index)=>`<article class="list-card"><div class="card-main"><h3>${panel._e(tool.spec?.name||"Unnamed tool")}</h3><p class="description">${panel._e(tool.spec?.description||"No description")}</p><p class="meta">${panel._e(tool.function?.type||"Unknown type")}</p></div><div class="actions"><button class="secondary edit-tool" data-index="${index}">Edit</button><button class="secondary duplicate-tool" data-index="${index}">Duplicate</button><button class="danger delete-tool" data-index="${index}">Delete</button></div></article>`).join("")||panel._empty("No function tools configured.")}</div><div class="section-actions"><button type="button" class="secondary" id="validate-tools">Validate tools</button><button type="button" id="save-tools" ${panel._configDirty?"":"disabled"}>Save tools</button><span id="tool-status" class="validation" aria-live="polite"></span></div></section>`;
}

function openTool(panel,index=null){ panel._toolIndex=index; const tool=index===null?{spec:{name:"",description:"",parameters:{type:"object",properties:{}}},function:{type:"native",name:""}}:clone(panel._draft.functions[index]); const root=panel.shadowRoot; root.querySelector("#tool-dialog-title").textContent=index===null?"Add Function Tool":"Edit Function Tool"; root.querySelector("#tool-name").value=tool.spec?.name||""; root.querySelector("#tool-description").value=tool.spec?.description||""; root.querySelector("#tool-type").value=tool.function?.type||"native"; root.querySelector("#tool-function-json").value=JSON.stringify(tool.function||{},null,2); root.querySelector("#tool-yaml").value=JSON.stringify([tool],null,2); root.querySelector("#tool-error").textContent=""; root.querySelector("#tool-dialog").showModal(); }

export function bindTools(panel) {
  const root=panel.shadowRoot;
  root.querySelector("#add-tool")?.addEventListener("click",()=>openTool(panel));
  root.querySelectorAll(".edit-tool").forEach(button=>button.addEventListener("click",()=>openTool(panel,Number(button.dataset.index))));
  root.querySelectorAll(".duplicate-tool").forEach(button=>button.addEventListener("click",()=>{const tool=clone(panel._draft.functions[Number(button.dataset.index)]);let base=`${tool.spec.name}_copy`,name=base,n=2;const names=new Set(panel._draft.functions.map(item=>item.spec?.name));while(names.has(name))name=`${base}_${n++}`;tool.spec.name=name;panel._draft.functions.push(tool);panel._configDirty=true;panel._render();}));
  root.querySelectorAll(".delete-tool").forEach(button=>button.addEventListener("click",async()=>{if(!await panel._confirm("Delete function tool?","The tool will be removed from this local draft. Save tools to apply the change.","Delete"))return;panel._draft.functions.splice(Number(button.dataset.index),1);panel._configDirty=true;panel._render();}));
  root.querySelector("#validate-tools")?.addEventListener("click",async()=>{const status=root.querySelector("#tool-status");try{const result=await panel._call("tools","validate",{tools:panel._draft.functions});status.className=`validation ${result.valid?"valid":"invalid"}`;status.textContent=result.valid?"All tools are valid":Object.values(result.errors).join(" ");}catch(err){status.className="validation invalid";status.textContent=err.message||String(err);}});
  root.querySelector("#save-tools")?.addEventListener("click",async()=>{try{const result=await panel._call("configuration","update",{config:{functions:panel._draft.functions}});panel._result={...panel._result,...result};panel._draft=clone(result.config);panel._configDirty=false;panel._toast("Function tools saved");panel._render();}catch(err){panel._toast(`Unable to save tools: ${err.message||String(err)}`,true);}});
  root.querySelectorAll('[name="tool-mode"]').forEach(input=>input.addEventListener("change",()=>{root.querySelector("#tool-structured").hidden=input.value!=="form";root.querySelector("#tool-yaml-wrap").hidden=input.value!=="yaml";}));
  root.querySelector("#tool-cancel")?.addEventListener("click",()=>root.querySelector("#tool-dialog").close());
  root.querySelector("#tool-validate")?.addEventListener("click",()=>validateDialogTool(panel));
  root.querySelector("#tool-save")?.addEventListener("click",async()=>{const tool=await validateDialogTool(panel);if(!tool)return;if(panel._toolIndex===null)panel._draft.functions.push(tool);else panel._draft.functions[panel._toolIndex]=tool;panel._configDirty=true;root.querySelector("#tool-dialog").close();panel._render();});
}

async function validateDialogTool(panel){const root=panel.shadowRoot;const yamlMode=root.querySelector('[name="tool-mode"]:checked').value==="yaml";let candidate;if(yamlMode){candidate=root.querySelector("#tool-yaml").value;}else{try{const functionConfig=JSON.parse(root.querySelector("#tool-function-json").value||"{}");functionConfig.type=root.querySelector("#tool-type").value;const existing=panel._toolIndex===null?{}:panel._draft.functions[panel._toolIndex];candidate=[{...clone(existing),spec:{...(existing.spec||{}),name:root.querySelector("#tool-name").value.trim(),description:root.querySelector("#tool-description").value},function:functionConfig}];}catch(err){root.querySelector("#tool-error").textContent=`Invalid function JSON: ${err.message}`;return null;}}try{const result=await panel._call("tools","validate",{tools:candidate});if(!result.valid){root.querySelector("#tool-error").textContent=Object.entries(result.errors).map(([key,value])=>`${key}: ${value}`).join(" ");return null;}root.querySelector("#tool-error").textContent="Valid tool configuration";return result.config[0];}catch(err){root.querySelector("#tool-error").textContent=err.message||String(err);return null;}}

export function configurationDialogs(panel) {
  const types=panel._result?.function_types||[];
  return `<dialog id="import-dialog" class="editor-dialog wide"><div class="dialog-header"><h2>Import agent configuration</h2></div><div class="dialog-body"><label>Exported JSON or YAML<textarea id="import-document" class="yaml-editor" spellcheck="false"></textarea></label><div class="mode-row"><label><input type="radio" name="import-mode" value="new" checked> Create a new agent</label><label><input type="radio" name="import-mode" value="current"> Overwrite current agent</label></div><div id="import-summary" class="validation" aria-live="polite">Validate the document to preview it.</div></div><div class="dialog-actions"><button type="button" class="secondary" id="import-cancel">Cancel</button><button type="button" class="secondary" id="import-preview">Validate & preview</button><button type="button" id="import-apply" disabled>Import</button></div></dialog>
  <dialog id="tool-dialog" class="editor-dialog wide"><div class="dialog-header"><h2 id="tool-dialog-title">Function Tool</h2></div><div class="dialog-body"><div class="mode-row"><label><input type="radio" name="tool-mode" value="form" checked> Form</label><label><input type="radio" name="tool-mode" value="yaml"> YAML</label></div><div id="tool-structured" class="config-stack"><div class="form-grid"><label>Name<input id="tool-name" required></label><label>Function type<select id="tool-type">${types.map(type=>option(panel,type,null)).join("")}</select></label></div><label>Description<textarea id="tool-description" class="short-textarea"></textarea></label><label>Function configuration (JSON)<textarea id="tool-function-json" class="yaml-editor" spellcheck="false"></textarea></label><p class="help">The backend's function implementation validates type-specific fields.</p></div><label id="tool-yaml-wrap" hidden>Complete tool YAML<textarea id="tool-yaml" class="yaml-editor tall" spellcheck="false"></textarea></label><div id="tool-error" class="validation" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary" id="tool-cancel">Cancel</button><button type="button" class="secondary" id="tool-validate">Validate</button><button type="button" id="tool-save">Keep in draft</button></div></dialog>`;
}
