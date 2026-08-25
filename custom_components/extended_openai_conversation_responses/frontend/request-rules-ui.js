const sensitivityLabel = (value) => value >= 93 ? "Conservative" : value >= 88 ? "Normal" : "Tolerant";
const sensitivityValue = (value) => value === "Conservative" ? 94 : value === "Tolerant" ? 84 : 90;
const matchLabel = (value) => ({equals:"Equals",starts_with:"Starts with",ends_with:"Ends with",contains:"Contains",sentence_pattern:"Sentence pattern"}[value] || value);
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
export const FRIENDLY_TARGET_KEYS = ["entity_id", "device_id", "area_id", "floor_id", "label_id"];

export function friendlyFieldChange(value) {
  return value === undefined || value === null || value === "" ? {operation:"delete"} : {operation:"set", value};
}

export function friendlyFieldChangesForService(previousService, nextService, changes) {
  return previousService === nextService ? changes : {};
}

export function parseAdvancedActionConfig(value) {
  let parsed;
  try { parsed = JSON.parse(value || "{}"); } catch (_err) { throw new Error("Advanced action configuration must be valid JSON."); }
  if (!isObject(parsed)) throw new Error("Advanced action configuration must be a JSON object.");
  const target = parsed.target ?? {}, data = parsed.data ?? {};
  if (!isObject(target)) throw new Error("Advanced action target must be a JSON object.");
  if (!isObject(data)) throw new Error("Advanced action data must be a JSON object.");
  return {target, data};
}

export function mergeFriendlyActionValue(action, targetKind, targetValue, advancedText, originalKind = "", originalValue = [], friendlyDataChanges = {}) {
  const advanced = parseAdvancedActionConfig(advancedText), target = {...advanced.target}, data = {...advanced.data};
  const present = (item) => item !== undefined && item !== null && item !== "";
  const value = Array.isArray(targetValue) ? targetValue.filter(present) : present(targetValue) ? [targetValue] : [];
  const original = Array.isArray(originalValue) ? originalValue : present(originalValue) ? [originalValue] : [];
  if (targetKind !== originalKind || JSON.stringify(value) !== JSON.stringify(original)) {
    for (const key of FRIENDLY_TARGET_KEYS) delete target[key];
    if (targetKind && value.length) target[targetKind] = value;
  }
  for (const [key, change] of Object.entries(friendlyDataChanges)) {
    if (change.operation === "delete") delete data[key];
    else if (change.operation === "set") data[key] = change.value;
  }
  return {domain:action.domain, service:action.service, target, data};
}

export function mergeActionEditorValue(action, entityText, advancedText, originalEntityText = "") {
  const values = (text) => text.split(",").map((item) => item.trim()).filter(Boolean);
  return mergeFriendlyActionValue(action, "entity_id", values(entityText), advancedText, "entity_id", values(originalEntityText));
}

const matchingControls = (prefix, values, hidden = false) => `<div id="${prefix}-matching-controls" class="matching-settings" ${hidden ? "hidden" : ""}><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Normalize word forms</span><small>Treats simple variations such as “light” and “lights” as the same.</small></span><input id="${prefix}-word-forms" type="checkbox" ${values.word_forms ? "checked" : ""}></label><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Wording alternatives</span><small>Uses your saved alternative phrases, such as “switch on” matching “turn on”.</small></span><input id="${prefix}-wording" type="checkbox" ${values.wording_alternatives ? "checked" : ""}></label><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Fuzzy matching</span><small>Allows small speech-recognition or typing mistakes when no normal match succeeds.</small></span><input id="${prefix}-fuzzy" type="checkbox" ${values.fuzzy ? "checked" : ""}></label><label class="matching-setting fuzzy-sensitivity ${values.fuzzy ? "" : "is-disabled"}"><span class="matching-copy"><span class="matching-title">Fuzzy sensitivity</span><small>Controls how close a phrase must be before fuzzy matching is accepted. Conservative is least likely to match the wrong rule.</small></span><span class="matching-control"><select id="${prefix}-threshold" ${values.fuzzy ? "" : "disabled"}><option ${sensitivityLabel(values.fuzzy_threshold) === "Conservative" ? "selected" : ""}>Conservative</option><option ${sensitivityLabel(values.fuzzy_threshold) === "Normal" ? "selected" : ""}>Normal</option><option ${sensitivityLabel(values.fuzzy_threshold) === "Tolerant" ? "selected" : ""}>Tolerant</option></select></span></label></div>`;

const wordingEditor = (panel, groups) => `<details class="wording-editor"><summary>Wording alternatives</summary><p class="help">Add different ways of saying the same thing. Separate multiple alternatives with commas.</p><div id="wording-groups">${groups.map((group) => `<div class="wording-group"><label>Main phrase<input class="wording-canonical" maxlength="100" value="${panel._e(group.canonical)}"></label><label>Other ways to say it<input class="wording-alternatives" value="${panel._e(group.alternatives.join(", "))}" placeholder="Comma-separated alternatives"></label><button type="button" class="icon wording-remove" aria-label="Remove wording alternative">×</button></div>`).join("")}</div><div class="section-actions"><button type="button" class="secondary" id="wording-add">Add wording alternative</button><button type="button" id="wording-save">Save wording alternatives</button></div></details>`;

function renderRequestRulesLegacy(panel) {
  const result = panel._result || {}, rules = result.rules || [];
  const defaults = {...{word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90}, ...(result.defaults || {})};
  const query = String(panel._query || "").trim().toLowerCase();
  const filtered = rules.filter((rule) => !query || `${rule.name} ${rule.phrases.join(" ")} ${rule.action_type}`.toLowerCase().includes(query));
  const description = (rule) => rule.action_type === "local_action" ? `${rule.action.actions.length} local action${rule.action.actions.length === 1 ? "" : "s"} · says “${panel._e(rule.action.success_response)}”` : rule.action.reset ? "Return this conversation to configured defaults" : `${rule.action.model || "Current model"}${rule.action.reasoning_effort ? ` · ${rule.action.reasoning_effort} reasoning` : ""} · ${rule.action.scope === "conversation" ? "rest of conversation" : "this request"}`;
  return `<section class="page-intro"><div><h1>Request Rules</h1><p>Create fast voice shortcuts and route AI requests before they reach OpenAI.</p></div><button type="button" id="rule-add">Create rule</button></section><section class="notice on"><strong>Local commands skip the AI/API call</strong><p>They normally respond faster. AI routing rules keep using the AI but can change the model or reasoning for one request or the active conversation.</p></section><section class="content-card rule-settings"><details><summary>Default matching settings</summary><p class="help">Most rules inherit these defaults. Strict matches always win over fuzzy matches.</p>${matchingControls("rules-default", defaults)}<div class="section-actions"><button type="button" id="rules-default-save">Save defaults</button></div></details>${wordingEditor(panel, result.wording_groups || [])}</section><div class="search-row"><input id="rule-search" type="search" value="${panel._e(panel._query || "")}" placeholder="Search rules" aria-label="Search Request Rules"><span class="count">${rules.length} rule${rules.length === 1 ? "" : "s"}</span></div><section class="rule-list">${filtered.map((rule) => `<article class="request-rule-card ${rule.enabled ? "" : "disabled"}"><div class="rule-card-heading"><div><span class="type-badge ${rule.action_type === "local_action" ? "local" : "routing"}">${rule.action_type === "local_action" ? "Local command" : "AI routing"}</span><h2>${panel._e(rule.name)}</h2></div><label class="switch-label"><span class="sr-only">Enable ${panel._e(rule.name)}</span><input class="rule-enabled" data-id="${panel._e(rule.id)}" type="checkbox" ${rule.enabled ? "checked" : ""}></label></div><div class="phrase-chips">${rule.phrases.slice(0,4).map((phrase) => `<span><b>${matchLabel(rule.match_type)}</b> ${panel._e(phrase)}</span>`).join("")}</div><p>${description(rule)}</p><p class="meta">Matching behavior: ${rule.match_type === "sentence_pattern" ? "Hassil grammar; fuzzy and normalization settings do not apply" : rule.matching_behavior === "defaults" ? "Uses default settings" : "Custom settings"}</p>${rule.sensitive_matching_warning && rule.match_type !== "sentence_pattern" ? `<p class="sensitive-warning">Review tolerant matching carefully: this rule controls a potentially sensitive Home Assistant domain.</p>` : ""}<div class="actions"><button type="button" class="secondary rule-edit" data-id="${panel._e(rule.id)}">Edit</button><button type="button" class="secondary rule-duplicate" data-id="${panel._e(rule.id)}">Duplicate</button><button type="button" class="danger secondary-danger rule-delete" data-id="${panel._e(rule.id)}">Delete</button></div></article>`).join("") || `<section class="content-card empty-state"><h2>${rules.length ? "No rules match your search" : "Create your first Request Rule"}</h2><p>${rules.length ? "Try a different phrase or rule name." : "Add a fast local command such as “good night”."}</p>${rules.length ? "" : `<button type="button" id="rule-empty-add">Create rule</button>`}</section>`}</section>`;
}

export function renderRequestRules(panel) {
  return `${renderRequestRulesLegacy(panel).replace("Most rules inherit these defaults. Strict matches always win over fuzzy matches.", "These settings apply to rules unless a rule has its own custom matching settings. Normal matches are always preferred before fuzzy matching is tried.")}<section class="content-card"><h2>Test a request</h2><p>Send text through this agent's real processing path, just like the <code>extended_openai_conversation_responses.process</code> action.</p><div class="notice"><strong>Testing a request can perform Home Assistant actions.</strong><p>This is a real request, not a dry run.</p></div><div class="search-row"><input id="rule-test-text" type="text" placeholder="Turn off the kitchen light" aria-label="Request to test"><button type="button" id="rule-test">Process request</button></div><pre id="rule-test-result" aria-live="polite"></pre></section>`;
}

export function requestRulesDialog() {
  return `<dialog id="rule-dialog" class="editor-dialog wide request-rule-dialog" aria-labelledby="rule-dialog-title"><form id="rule-form"><div class="dialog-header"><h2 id="rule-dialog-title">Create Request Rule</h2><button type="button" class="icon rule-close" aria-label="Close">×</button></div><div class="dialog-body"><div class="form-grid"><label>Rule name<input id="rule-name" required maxlength="120" placeholder="Shopping list"></label><label class="toggle"><span>Enabled</span><input id="rule-enabled-edit" type="checkbox" checked></label></div><section><h3>1. What will you say?</h3><label>Trigger phrases or patterns<textarea id="rule-phrases" required placeholder="Add {item} to my shopping list"></textarea><small>Put each alternative on a new line. Alternatives must use the same variable names.</small></label><div id="rule-slot-help" class="notice" hidden><strong>Variable values</strong><p>Variable values let part of the request change each time. You can use the captured value in actions or responses.</p><p id="rule-slot-list"></p></div><label>How should it match?<select id="rule-match"><option value="equals">Equals</option><option value="starts_with">Starts with</option><option value="ends_with">Ends with</option><option value="contains">Contains</option><option value="sentence_pattern">Home Assistant sentence pattern</option></select></label><div id="sentence-pattern-help" class="notice" hidden><strong>Sentence-pattern syntax</strong><p>Use <code>[optional words]</code>, <code>(one|two)</code>, and variable values such as <code>{room}</code>. Named expansions such as <code>&lt;name&gt;</code> are not supported.</p></div></section><section><h3>2. What should happen?</h3><label>Behaviour<select id="rule-action-type"><option value="local_action">Run actions locally</option><option value="model_routing">Route through AI with different settings</option></select></label><div id="rule-local-config"><p class="help">Home Assistant actions and enabled ExtendedOpenAI functions run in order without asking the AI model.</p><datalist id="rule-service-list"></datalist><div id="rule-actions"></div><div class="section-actions"><button type="button" class="secondary" id="rule-action-add">Add Home Assistant action</button><button type="button" class="secondary" id="rule-function-add">Add ExtendedOpenAI function</button></div></div><div id="rule-routing-config" hidden><div class="form-grid"><label>Model<input id="rule-model" placeholder="gpt-5-mini"></label><label>Reasoning effort<select id="rule-reasoning"><option value="">Keep current</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><label>Scope<select id="rule-scope"><option value="request">This request only</option><option value="conversation">Rest of this conversation</option></select></label><label class="toggle"><span>Reset to configured defaults</span><input id="rule-reset" type="checkbox"></label></div></div></section><section><h3>3. What should the assistant say?</h3><div id="rule-local-responses" class="form-grid"><label>Success response<input id="rule-success" value="Done"><small>You can include a captured value such as <code>{item}</code>.</small></label><label>Failure response<input id="rule-failure" value="Sorry, that did not work"></label></div><label id="rule-routing-response" hidden>Acknowledgement<input id="rule-routing-success" value="Updated"></label></section><details id="rule-advanced" class="advanced-context-formatting"><summary>Advanced matching and action configuration</summary><label>Matching behaviour<select id="rule-matching-behavior"><option value="defaults">Use default settings</option><option value="custom">Customize for this rule</option></select></label>${matchingControls("rule", {word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90}, true)}<p class="help">Sentence patterns always use Hassil grammar matching, so these controls do not apply. Advanced Home Assistant JSON can use <code>{slot}</code> in text values.</p></details><div id="rule-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary rule-close">Cancel</button><button type="submit" id="rule-save">Save rule</button></div></form></dialog>`;
}

const targetInfo = (action) => {
  for (const kind of ["entity_id", "device_id", "area_id"]) if (action.target?.[kind]) return {kind, value:Array.isArray(action.target[kind]) ? action.target[kind] : [action.target[kind]]};
  return {kind:"entity_id", value:[]};
};

function actionRow(panel, action = {}) {
  const row = document.createElement("div"), target = targetInfo(action);
  row.className = "ha-action-row"; row._targetValue = [...target.value]; row._originalTargetValue = [...target.value]; row._friendlyDataChanges = {}; row._friendlyService = action.domain && action.service ? `${action.domain}.${action.service}` : ""; row.dataset.originalTargetKind = target.kind;
  row.innerHTML = `<label class="service-picker">Home Assistant action<input class="ha-service-picker" list="rule-service-list" required value="${panel._e(action.domain && action.service ? `${action.domain}.${action.service}` : "")}" placeholder="Start typing, for example light.turn_on"></label><label>Target type<select class="ha-target-kind"><option value="entity_id" ${target.kind === "entity_id" ? "selected" : ""}>Entity</option><option value="device_id" ${target.kind === "device_id" ? "selected" : ""}>Device</option><option value="area_id" ${target.kind === "area_id" ? "selected" : ""}>Area</option><option value="">No target</option></select></label><label class="ha-target-label">Target<ha-selector class="ha-target-selector"></ha-selector></label><button type="button" class="icon ha-action-remove" aria-label="Remove action">×</button><div class="ha-service-fields"></div><details class="ha-action-advanced"><summary>Advanced JSON</summary><label>Target and action data<textarea class="ha-advanced" spellcheck="false">${panel._e(JSON.stringify({target:action.target || {},data:action.data || {}}, null, 2))}</textarea></label><small>Existing target keys and action data remain intact.</small></details>`;
  row.querySelector(".ha-action-remove").addEventListener("click", () => row.remove()); return row;
}

const capturedSlotNames = (text) => [...new Set([...String(text || "").matchAll(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g)].map((match) => match[1]))];
const slotOptions = (panel, selected = "") => `<option value="">Choose a captured value</option>${capturedSlotNames(panel.shadowRoot.querySelector("#rule-phrases")?.value).map((name) => `<option value="${panel._e(name)}" ${name === selected ? "selected" : ""}>${panel._e(name)}</option>`).join("")}`;

export function refreshRequestRuleSlotSelectors(root, slotNames) {
  for (const select of root.querySelectorAll(".ha-slot-picker, .function-slot")) {
    const selected = select.value;
    const options = ["", ...slotNames].map((name) => {
      const option = select.ownerDocument.createElement("option");
      option.value = name;
      option.textContent = name || "Choose a captured value";
      return option;
    });
    select.replaceChildren(...options);
    select.value = slotNames.includes(selected) ? selected : "";
    select._refreshSlotBinding?.();
  }
}

function functionActionRow(panel, action = {}) {
  const row = document.createElement("div"), catalog = panel._result?.function_catalog || [];
  row.className = "function-action-row";
  row._arguments = action.arguments || {};
  row.innerHTML = `<label>ExtendedOpenAI function<select class="function-picker" required><option value="">Choose a function</option>${catalog.map((tool) => `<option value="${panel._e(tool.name)}" ${tool.name === action.function ? "selected" : ""}>${panel._e(tool.name)}</option>`).join("")}</select></label><button type="button" class="icon function-action-remove" aria-label="Remove function action">×</button><p class="help function-description"></p><div class="function-fields"></div>`;
  row.querySelector(".function-action-remove").addEventListener("click", () => row.remove());
  const renderFields = () => {
    const tool = catalog.find((item) => item.name === row.querySelector(".function-picker").value), container = row.querySelector(".function-fields");
    container.replaceChildren(); row.querySelector(".function-description").textContent = tool?.description || "";
    if (!tool) return;
    const properties = tool.parameters?.properties || {}, required = new Set(tool.parameters?.required || []);
    for (const [name, schema] of Object.entries(properties)) {
      if (name === "delay") continue;
      const binding = row._arguments[name] || {source:"fixed", value:schema.default ?? ""}, wrapper = document.createElement("div");
      wrapper.className = "function-field"; wrapper.dataset.name = name; wrapper.dataset.schemaType = schema.type || "string";
      const fixedInput = schema.enum ? `<select class="function-fixed">${schema.enum.map((choice) => `<option value="${panel._e(String(choice))}" ${choice === binding.value ? "selected" : ""}>${panel._e(String(choice))}</option>`).join("")}</select>` : schema.type === "boolean" ? `<input class="function-fixed" type="checkbox" ${binding.value === true ? "checked" : ""}>` : `<input class="function-fixed" type="${["number","integer"].includes(schema.type) ? "number" : "text"}" value="${panel._e(Array.isArray(binding.value) ? binding.value.join(", ") : String(binding.value ?? ""))}">`;
      wrapper.innerHTML = `<label>${panel._e(schema.title || name.replaceAll("_", " "))}${required.has(name) ? " *" : ""}<select class="function-source"><option value="fixed" ${binding.source !== "slot" ? "selected" : ""}>Fixed value</option><option value="slot" ${binding.source === "slot" ? "selected" : ""}>Value from request</option></select></label><div class="function-fixed-wrap">${fixedInput}</div><label class="function-slot-wrap">Captured value<select class="function-slot">${slotOptions(panel, binding.slot)}</select></label>${schema.description ? `<small>${panel._e(schema.description)}</small>` : ""}`;
      const refresh = () => { const fromSlot = wrapper.querySelector(".function-source").value === "slot"; wrapper.querySelector(".function-fixed-wrap").hidden = fromSlot; wrapper.querySelector(".function-slot-wrap").hidden = !fromSlot; };
      wrapper.querySelector(".function-source").addEventListener("change", refresh); refresh(); container.append(wrapper);
    }
  };
  row.querySelector(".function-picker").addEventListener("change", () => { row._arguments = {}; renderFields(); }); renderFields(); return row;
}

function readFunctionAction(row) {
  const args = {};
  for (const field of row.querySelectorAll(".function-field")) {
    const source = field.querySelector(".function-source").value, name = field.dataset.name;
    if (source === "slot") { const slot = field.querySelector(".function-slot").value; if (!slot) throw new Error(`Choose a captured value for ${name}.`); args[name] = {source:"slot",slot}; continue; }
    const input = field.querySelector(".function-fixed"), type = field.dataset.schemaType; let value = input.type === "checkbox" ? input.checked : input.value;
    if (["number","integer"].includes(type) && value !== "") value = type === "integer" ? Number.parseInt(value, 10) : Number(value);
    if (type === "array") value = String(value).split(",").map((item) => item.trim()).filter(Boolean);
    if (value !== "") args[name] = {source:"fixed",value};
  }
  const name = row.querySelector(".function-picker").value; if (!name) throw new Error("Choose an ExtendedOpenAI function.");
  return {type:"function",function:name,arguments:args};
}

function setupActionRow(panel, row) {
  if (row._refreshServiceFields) { row._refreshServiceFields(); return; }
  const serviceInput = row.querySelector(".ha-service-picker"), kindInput = row.querySelector(".ha-target-kind"), selector = row.querySelector(".ha-target-selector");
  const configureTarget = () => { const kind = kindInput.value; selector.hass = panel.hass; selector.selector = kind === "entity_id" ? {entity:{multiple:true}} : kind === "device_id" ? {device:{multiple:true}} : kind === "area_id" ? {area:{multiple:true}} : {select:{options:[]}}; selector.value = row._targetValue || []; row.querySelector(".ha-target-label").hidden = !kind; };
  const configureFields = () => {
    const [domain, service] = serviceInput.value.trim().split(".", 2), fields = panel._serviceCatalog?.[domain]?.[service]?.fields || {}, container = row.querySelector(".ha-service-fields"); container.replaceChildren();
    for (const [name, field] of Object.entries(fields)) { if (!field.selector) continue; const existing = parseAdvancedActionConfig(row.querySelector(".ha-advanced").value).data[name] ?? field.default, wrapper = document.createElement("div"), label = document.createElement("label"), source = document.createElement("select"), input = document.createElement("ha-selector"), slot = document.createElement("select"); wrapper.className = "ha-service-field"; label.textContent = field.name || name.replaceAll("_", " "); source.innerHTML = `<option value="fixed">Fixed value</option><option value="slot">Value from request</option>`; slot.className = "ha-slot-picker"; slot.innerHTML = slotOptions(panel, existing?.slot); input.hass = panel.hass; input.selector = field.selector; input.value = existing?.value_from === "slot" ? field.default : existing; let initialized = false; const refreshSource = () => { const fromSlot = source.value === "slot"; input.hidden = fromSlot; slot.hidden = !fromSlot; if (fromSlot || initialized) row._friendlyDataChanges[name] = friendlyFieldChange(fromSlot ? {value_from:"slot",slot:slot.value} : input.value); initialized = true; }; slot._refreshSlotBinding = () => { if (source.value === "slot") refreshSource(); }; source.value = existing?.value_from === "slot" ? "slot" : "fixed"; input.addEventListener("value-changed", (event) => { input.value = event.detail.value; row._friendlyDataChanges[name] = friendlyFieldChange(event.detail.value); }); source.addEventListener("change", refreshSource); slot.addEventListener("change", refreshSource); label.append(source, input, slot); wrapper.append(label); container.append(wrapper); refreshSource(); }
  };
  const changeService = () => { const nextService = serviceInput.value.trim(); row._friendlyDataChanges = friendlyFieldChangesForService(row._friendlyService, nextService, row._friendlyDataChanges); row._friendlyService = nextService; configureFields(); };
  row._refreshServiceFields = configureFields;
  selector.addEventListener("value-changed", (event) => { row._targetValue = event.detail.value ?? []; }); kindInput.addEventListener("change", () => { row._targetValue = []; configureTarget(); }); serviceInput.addEventListener("change", changeService); configureTarget(); configureFields();
}

const setFuzzyState = (root, prefix) => { const toggle = root.querySelector(`#${prefix}-fuzzy`), select = root.querySelector(`#${prefix}-threshold`); if (!toggle || !select) return; select.disabled = !toggle.checked; select.closest(".fuzzy-sensitivity")?.classList.toggle("is-disabled", !toggle.checked); };

export function bindRequestRules(panel) {
  const root = panel.shadowRoot, q = (selector) => root.querySelector(selector), result = panel._result || {}, rules = result.rules || [];
  root.addEventListener("click", async (event) => {
    const trigger = event.target.closest("#rule-add, #rule-empty-add, .rule-edit");
    if (!trigger || panel._serviceCatalog) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    open(trigger.dataset.id || null);
    const dialog = q("#rule-dialog"), save = q("#rule-save"), add = q("#rule-action-add"), error = q("#rule-error");
    save.disabled = true;
    add.disabled = true;
    error.textContent = "Loading Home Assistant actions…";
    try {
      const catalog = await panel._loadServiceCatalog();
      if (!dialog.open) return;
      const entries = Object.entries(catalog).flatMap(([domain, services]) => Object.entries(services).map(([service, description]) => ({domain, service, description})));
      q("#rule-service-list").replaceChildren(...entries.map(({domain, service, description}) => { const option = document.createElement("option"); option.value = `${domain}.${service}`; option.label = description.name || option.value; return option; }));
      q("#rule-actions").querySelectorAll(".ha-action-row").forEach((row) => setupActionRow(panel, row));
      error.textContent = "";
      save.disabled = false;
      add.disabled = false;
    } catch (err) {
      if (dialog.open) error.textContent = `Unable to load Home Assistant actions: ${err.message || String(err)}`;
    }
  }, true);
  const refresh = () => { const local = q("#rule-action-type").value === "local_action", grammar = q("#rule-match").value === "sentence_pattern", slots = capturedSlotNames(q("#rule-phrases").value); refreshRequestRuleSlotSelectors(root, slots); q("#rule-local-config").hidden = !local; q("#rule-routing-config").hidden = local; q("#rule-local-responses").hidden = !local; q("#rule-routing-response").hidden = local; q("#sentence-pattern-help").hidden = !grammar; q("#rule-slot-help").hidden = !slots.length; q("#rule-slot-list").textContent = slots.length ? `Captured values: ${slots.join(", ")}` : ""; q("#rule-matching-behavior").disabled = grammar; q("#rule-matching-controls").hidden = grammar || q("#rule-matching-behavior").value !== "custom"; if (slots.length) q("#rule-match").value = "sentence_pattern"; if (!local && ["equals","sentence_pattern"].includes(q("#rule-match").value)) q("#rule-scope").value = "conversation"; };
  const open = (id = null) => {
    const rule = rules.find((item) => item.id === id); panel._editingRuleId = id; q("#rule-dialog-title").textContent = rule ? "Edit Request Rule" : "Create Request Rule"; q("#rule-name").value = rule?.name || ""; q("#rule-enabled-edit").checked = rule?.enabled ?? true; q("#rule-phrases").value = (rule?.phrases || []).join("\n"); q("#rule-match").value = rule?.match_type || "equals"; q("#rule-action-type").value = rule?.action_type || "local_action"; q("#rule-success").value = rule?.action?.success_response || "Done"; q("#rule-failure").value = rule?.action?.failure_response || "Sorry, that did not work"; q("#rule-model").value = rule?.action?.model || ""; q("#rule-reasoning").value = rule?.action?.reasoning_effort || ""; q("#rule-scope").value = rule?.action?.scope || "request"; q("#rule-reset").checked = rule?.action?.reset || false; q("#rule-routing-success").value = rule?.action?.success_response || "Updated"; q("#rule-matching-behavior").value = rule?.matching_behavior || "defaults"; q("#rule-word-forms").checked = rule?.matching?.word_forms ?? true; q("#rule-wording").checked = rule?.matching?.wording_alternatives ?? true; q("#rule-fuzzy").checked = rule?.matching?.fuzzy ?? false; q("#rule-threshold").value = sensitivityLabel(rule?.matching?.fuzzy_threshold ?? 90); q("#rule-actions").replaceChildren(...(rule?.action?.actions || [{domain:"script",service:"turn_on",target:{}}]).map((item) => item.type === "function" ? functionActionRow(panel, item) : actionRow(panel, item))); q("#rule-actions").querySelectorAll(".ha-action-row").forEach((row) => setupActionRow(panel, row)); refresh(); setFuzzyState(root, "rule"); q("#rule-error").textContent = ""; q("#rule-dialog").showModal();
  };
  const entries = Object.entries(panel._serviceCatalog || {}).flatMap(([domain, services]) => Object.entries(services).map(([service, description]) => ({domain,service,description}))); q("#rule-service-list")?.replaceChildren(...entries.map(({domain,service,description}) => { const option = document.createElement("option"); option.value = `${domain}.${service}`; option.label = description.name || option.value; return option; }));
  q("#rule-add")?.addEventListener("click", () => open()); q("#rule-empty-add")?.addEventListener("click", () => open()); q("#rule-search")?.addEventListener("input", (event) => { panel._query = event.target.value; panel._render(); }); root.querySelectorAll(".rule-edit").forEach((button) => button.addEventListener("click", () => open(button.dataset.id))); root.querySelectorAll(".rule-duplicate").forEach((button) => button.addEventListener("click", async () => { await panel._call("request_rules", "duplicate", {rule_id:button.dataset.id}); await panel._loadSection(); })); root.querySelectorAll(".rule-delete").forEach((button) => button.addEventListener("click", async () => { if (!await panel._confirm("Delete Request Rule?", "This cannot be undone.", "Delete")) return; await panel._call("request_rules", "delete", {rule_id:button.dataset.id,confirm:true}); await panel._loadSection(); })); root.querySelectorAll(".rule-enabled").forEach((input) => input.addEventListener("change", async () => { const rule = rules.find((item) => item.id === input.dataset.id); await panel._call("request_rules", "update", {rule_id:rule.id,rule:{...rule,enabled:input.checked,sensitive_matching_warning:undefined}}); await panel._loadSection(true); }));
  q("#rules-default-fuzzy")?.addEventListener("change", () => setFuzzyState(root, "rules-default")); q("#rule-fuzzy")?.addEventListener("change", () => setFuzzyState(root, "rule")); setFuzzyState(root, "rules-default"); q("#rules-default-save")?.addEventListener("click", async () => { await panel._call("request_rules", "defaults", {defaults:{word_forms:q("#rules-default-word-forms").checked,wording_alternatives:q("#rules-default-wording").checked,fuzzy:q("#rules-default-fuzzy").checked,fuzzy_threshold:sensitivityValue(q("#rules-default-threshold").value)}}); await panel._loadSection(); });
  q("#rule-test")?.addEventListener("click", async () => { const output = q("#rule-test-result"), text = q("#rule-test-text").value.trim(); if (!text) return; output.textContent = "Processing…"; try { const response = await panel._call("request_rules", "test", {text}), captured = Object.entries(response.captured_values || {}); output.textContent = `${response.response || "(No response text)"}\nConversation ID: ${response.conversation_id || "—"}\nPath: ${response.handled_locally ? "Handled locally" : "AI provider"}${response.matched_rule ? `\nMatched rule: ${response.matched_rule.name}` : ""}${captured.length ? `\nCaptured values:\n${captured.map(([name,value]) => `${name} → ${value}`).join("\n")}` : ""}`; } catch (err) { output.textContent = err.message || String(err); } });
  const bindRemove = () => root.querySelectorAll(".wording-remove").forEach((button) => button.onclick = () => button.closest(".wording-group").remove()); bindRemove(); q("#wording-add")?.addEventListener("click", () => { const wrapper = document.createElement("div"); wrapper.className = "wording-group"; wrapper.innerHTML = `<label>Main phrase<input class="wording-canonical" maxlength="100"></label><label>Other ways to say it<input class="wording-alternatives" placeholder="Comma-separated alternatives"></label><button type="button" class="icon wording-remove" aria-label="Remove wording alternative">×</button>`; q("#wording-groups").append(wrapper); bindRemove(); }); q("#wording-save")?.addEventListener("click", async () => { const wording_groups = [...root.querySelectorAll(".wording-group")].map((row) => ({canonical:row.querySelector(".wording-canonical").value.trim(),alternatives:row.querySelector(".wording-alternatives").value.split(",").map((item) => item.trim()).filter(Boolean)})); await panel._call("request_rules", "wording_groups", {wording_groups}); await panel._loadSection(); });
  q("#rule-action-add")?.addEventListener("click", () => { const row = actionRow(panel); q("#rule-actions").append(row); setupActionRow(panel, row); }); q("#rule-function-add")?.addEventListener("click", () => q("#rule-actions").append(functionActionRow(panel))); q("#rule-action-type")?.addEventListener("change", refresh); q("#rule-match")?.addEventListener("change", refresh); q("#rule-phrases")?.addEventListener("input", refresh); q("#rule-matching-behavior")?.addEventListener("change", refresh); root.querySelectorAll(".rule-close").forEach((button) => button.addEventListener("click", () => q("#rule-dialog").close()));
  q("#rule-form")?.addEventListener("submit", async (event) => { event.preventDefault(); try { const actionType = q("#rule-action-type").value; const actions = [...q("#rule-actions").children].map((row) => { if (row.classList.contains("function-action-row")) return readFunctionAction(row); const [domain, service] = row.querySelector(".ha-service-picker").value.trim().split(".", 2); if (!domain || !service) throw new Error("Choose an action in domain.action format."); return mergeFriendlyActionValue({domain,service},row.querySelector(".ha-target-kind").value,row._targetValue,row.querySelector(".ha-advanced").value,row.dataset.originalTargetKind,row._originalTargetValue,row._friendlyDataChanges); }); const rule = {name:q("#rule-name").value,enabled:q("#rule-enabled-edit").checked,phrases:q("#rule-phrases").value.split("\n").map((item) => item.trim()).filter(Boolean),match_type:q("#rule-match").value,action_type:actionType,action:actionType === "local_action" ? {actions,success_response:q("#rule-success").value,failure_response:q("#rule-failure").value} : {model:q("#rule-model").value,reasoning_effort:q("#rule-reasoning").value,scope:q("#rule-scope").value,reset:q("#rule-reset").checked,success_response:q("#rule-routing-success").value},matching_behavior:q("#rule-matching-behavior").value,matching:{word_forms:q("#rule-word-forms").checked,wording_alternatives:q("#rule-wording").checked,fuzzy:q("#rule-fuzzy").checked,fuzzy_threshold:sensitivityValue(q("#rule-threshold").value)},order:rules.find((item) => item.id === panel._editingRuleId)?.order ?? rules.length}; await panel._call("request_rules", panel._editingRuleId ? "update" : "create", {...(panel._editingRuleId ? {rule_id:panel._editingRuleId} : {}),rule}); q("#rule-dialog").close(); await panel._loadSection(); } catch (err) { q("#rule-error").textContent = err.message || String(err); } });
}
