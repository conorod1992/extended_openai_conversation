const sensitivityLabel = (value) => value >= 93 ? "Conservative" : value >= 88 ? "Normal" : "Tolerant";
const sensitivityValue = (value) => value === "Conservative" ? 94 : value === "Tolerant" ? 84 : 90;
const matchLabel = (value) => ({equals:"Equals",starts_with:"Starts with",ends_with:"Ends with",contains:"Contains"}[value] || value);

export function renderRequestRules(panel) {
  const result = panel._result || {};
  const rules = result.rules || [];
  const defaults = result.defaults || {};
  const filtered = rules.filter((rule) => {
    const query = String(panel._query || "").trim().toLowerCase();
    return !query || `${rule.name} ${rule.phrases.join(" ")} ${rule.action_type}`.toLowerCase().includes(query);
  });
  const description = (rule) => rule.action_type === "local_action"
    ? `${rule.action.actions.length} Home Assistant action${rule.action.actions.length === 1 ? "" : "s"} · says “${panel._e(rule.action.success_response)}”`
    : rule.action.reset ? "Return this conversation to configured defaults"
      : `${rule.action.model || "Current model"}${rule.action.reasoning_effort ? ` · ${rule.action.reasoning_effort} reasoning` : ""} · ${rule.action.scope === "conversation" ? "rest of conversation" : "this request"}`;
  return `<section class="page-intro"><div><h1>Request Rules</h1><p>Create fast voice shortcuts and route AI requests before they reach OpenAI.</p></div><button type="button" id="rule-add">Create rule</button></section>
    <section class="notice on"><strong>Local commands skip the AI/API call</strong><p>They normally respond faster. AI routing rules keep using the AI but can change the model or reasoning for one request or the active conversation.</p></section>
    <section class="content-card rule-settings"><details><summary>Default matching settings</summary><p class="help">Most rules use these settings. Matching is deterministic; fuzzy matching is only tried when strict matching fails.</p><div class="form-grid compact"><label class="toggle"><span>Normalize word forms<small>Matches simple forms such as light/lights and reminder/reminders.</small></span><input id="rules-default-word-forms" type="checkbox" ${defaults.word_forms ? "checked" : ""}></label><label class="toggle"><span>Common wording alternatives<small>Matches a small curated set such as turn on/switch on.</small></span><input id="rules-default-wording" type="checkbox" ${defaults.wording_alternatives ? "checked" : ""}></label><label class="toggle"><span>Fuzzy matching<small>Tolerates small speech-recognition mistakes, but may make matches less strict.</small></span><input id="rules-default-fuzzy" type="checkbox" ${defaults.fuzzy ? "checked" : ""}></label><label>Fuzzy sensitivity<select id="rules-default-threshold"><option ${sensitivityLabel(defaults.fuzzy_threshold) === "Conservative" ? "selected" : ""}>Conservative</option><option ${sensitivityLabel(defaults.fuzzy_threshold) === "Normal" ? "selected" : ""}>Normal</option><option ${sensitivityLabel(defaults.fuzzy_threshold) === "Tolerant" ? "selected" : ""}>Tolerant</option></select></label></div><div class="section-actions"><button type="button" id="rules-default-save">Save defaults</button></div></details></section>
    <div class="search-row"><input id="rule-search" type="search" value="${panel._e(panel._query || "")}" placeholder="Search rules" aria-label="Search Request Rules"><span class="count">${rules.length} rule${rules.length === 1 ? "" : "s"}</span></div>
    <section class="rule-list">${filtered.map((rule) => `<article class="request-rule-card ${rule.enabled ? "" : "disabled"}"><div class="rule-card-heading"><div><span class="type-badge ${rule.action_type === "local_action" ? "local" : "routing"}">${rule.action_type === "local_action" ? "Local command" : "AI routing"}</span><h2>${panel._e(rule.name)}</h2></div><label class="switch-label"><span class="sr-only">Enable ${panel._e(rule.name)}</span><input class="rule-enabled" data-id="${panel._e(rule.id)}" type="checkbox" ${rule.enabled ? "checked" : ""}></label></div><div class="phrase-chips">${rule.phrases.slice(0,4).map((phrase) => `<span><b>${matchLabel(rule.match_type)}</b> ${panel._e(phrase)}</span>`).join("")}${rule.phrases.length > 4 ? `<span>+${rule.phrases.length - 4} more</span>` : ""}</div><p>${description(rule)}</p><p class="meta">Matching behavior: ${rule.matching_behavior === "defaults" ? "Uses default settings" : `Custom${rule.matching.fuzzy ? ` · ${sensitivityLabel(rule.matching.fuzzy_threshold)} fuzzy` : ""}`}</p>${rule.sensitive_matching_warning && (rule.matching_behavior === "defaults" ? defaults.fuzzy || defaults.wording_alternatives : rule.matching.fuzzy || rule.matching.wording_alternatives) ? `<p class="sensitive-warning">Review tolerant matching carefully: this rule controls a potentially sensitive Home Assistant domain.</p>` : ""}<div class="actions"><button type="button" class="secondary rule-edit" data-id="${panel._e(rule.id)}">Edit</button><button type="button" class="secondary rule-duplicate" data-id="${panel._e(rule.id)}">Duplicate</button><button type="button" class="danger secondary-danger rule-delete" data-id="${panel._e(rule.id)}">Delete</button></div></article>`).join("") || `<section class="content-card empty-state"><h2>${rules.length ? "No rules match your search" : "Create your first Request Rule"}</h2><p>${rules.length ? "Try a different phrase or rule name." : "Add a fast local command such as “good night”, or route requests such as “think carefully” to a different model."}</p>${rules.length ? "" : `<button type="button" id="rule-empty-add">Create rule</button>`}</section>`}</section>`;
}

export function requestRulesDialog(panel) {
  return `<dialog id="rule-dialog" class="editor-dialog wide request-rule-dialog" aria-labelledby="rule-dialog-title"><form id="rule-form"><div class="dialog-header"><h2 id="rule-dialog-title">Create Request Rule</h2><button type="button" class="icon rule-close" aria-label="Close">×</button></div><div class="dialog-body"><div class="form-grid"><label>Rule name<input id="rule-name" required maxlength="120" placeholder="Good night"></label><label class="toggle"><span>Enabled</span><input id="rule-enabled-edit" type="checkbox" checked></label></div><label>Trigger phrases<textarea id="rule-phrases" required placeholder="One phrase per line&#10;good night"></textarea><small>Put each alternative phrase on a new line.</small></label><div class="form-grid"><label>Match<select id="rule-match"><option value="equals">Equals</option><option value="starts_with">Starts with</option><option value="ends_with">Ends with</option><option value="contains">Contains</option></select></label><label>Behaviour<select id="rule-action-type"><option value="local_action">Local command — run Home Assistant actions</option><option value="model_routing">AI routing — switch model or reasoning</option></select></label></div>
      <section id="rule-local-config"><h3>Home Assistant actions</h3><p class="help">Actions run in order. Local commands do not call OpenAI.</p><div id="rule-actions"></div><button type="button" class="secondary" id="rule-action-add">Add action</button><div class="form-grid"><label>Success response<input id="rule-success" value="Done"></label><label>Failure response<input id="rule-failure" value="Sorry, that did not work"></label></div></section>
      <section id="rule-routing-config" hidden><h3>AI routing</h3><div class="form-grid"><label>Model<input id="rule-model" placeholder="gpt-5-mini"></label><label>Reasoning effort<select id="rule-reasoning"><option value="">Keep current</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><label>Scope<select id="rule-scope"><option value="request">This request only</option><option value="conversation">Rest of this conversation</option></select></label><label class="toggle"><span>Reset to configured defaults</span><input id="rule-reset" type="checkbox"></label></div><label>Acknowledgement<input id="rule-routing-success" value="Updated"></label><p class="help">An Equals command is acknowledged locally. Broader matches keep the original request wording unchanged.</p></section>
      <section><h3>Matching behaviour</h3><label><select id="rule-matching-behavior"><option value="defaults">Uses default settings</option><option value="custom">Customize for this rule</option></select></label><div id="rule-custom-matching" class="form-grid compact" hidden><label class="toggle"><span>Normalize word forms</span><input id="rule-word-forms" type="checkbox" checked></label><label class="toggle"><span>Common wording alternatives</span><input id="rule-wording" type="checkbox" checked></label><label class="toggle"><span>Fuzzy matching</span><input id="rule-fuzzy" type="checkbox"></label><label>Fuzzy sensitivity<select id="rule-threshold"><option>Conservative</option><option selected>Normal</option><option>Tolerant</option></select></label></div><p class="help">Fuzzy matching can tolerate small speech-recognition mistakes but may also make matches less strict. It is not semantic understanding.</p></section><div id="rule-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary rule-close">Cancel</button><button type="submit" id="rule-save">Save rule</button></div></form></dialog>`;
}

function actionRow(panel, action = {}) {
  const row = document.createElement("div");
  row.className = "ha-action-row";
  const target = action.target?.entity_id;
  row.innerHTML = `<label>Domain<input class="ha-domain" required value="${panel._e(action.domain || "")}" placeholder="script"></label><label>Action<input class="ha-service" required value="${panel._e(action.service || "")}" placeholder="turn_on"></label><label>Target entities<input class="ha-target" value="${panel._e(Array.isArray(target) ? target.join(", ") : target || "")}" placeholder="script.goodnight"></label><button type="button" class="icon ha-action-remove" aria-label="Remove action">×</button>`;
  row.querySelector(".ha-action-remove").addEventListener("click", () => row.remove());
  return row;
}

export function bindRequestRules(panel) {
  const root = panel.shadowRoot;
  const q = (selector) => root.querySelector(selector);
  const result = panel._result || {};
  const open = (id = null) => {
    const rule = (result.rules || []).find((item) => item.id === id);
    panel._editingRuleId = id;
    q("#rule-dialog-title").textContent = rule ? "Edit Request Rule" : "Create Request Rule";
    q("#rule-name").value = rule?.name || "";
    q("#rule-enabled-edit").checked = rule?.enabled ?? true;
    q("#rule-phrases").value = (rule?.phrases || []).join("\n");
    q("#rule-match").value = rule?.match_type || "equals";
    q("#rule-action-type").value = rule?.action_type || "local_action";
    q("#rule-success").value = rule?.action?.success_response || "Done";
    q("#rule-failure").value = rule?.action?.failure_response || "Sorry, that did not work";
    q("#rule-model").value = rule?.action?.model || "";
    q("#rule-reasoning").value = rule?.action?.reasoning_effort || "";
    q("#rule-scope").value = rule?.action?.scope || "request";
    q("#rule-reset").checked = rule?.action?.reset || false;
    q("#rule-routing-success").value = rule?.action?.success_response || "Updated";
    q("#rule-matching-behavior").value = rule?.matching_behavior || "defaults";
    q("#rule-word-forms").checked = rule?.matching?.word_forms ?? true;
    q("#rule-wording").checked = rule?.matching?.wording_alternatives ?? true;
    q("#rule-fuzzy").checked = rule?.matching?.fuzzy ?? false;
    q("#rule-threshold").value = sensitivityLabel(rule?.matching?.fuzzy_threshold ?? 90);
    q("#rule-actions").replaceChildren(...(rule?.action?.actions || [{domain:"script",service:"turn_on",target:{}}]).map((item) => actionRow(panel, item)));
    const refresh = () => { const local = q("#rule-action-type").value === "local_action"; q("#rule-local-config").hidden = !local; q("#rule-routing-config").hidden = local; q("#rule-custom-matching").hidden = q("#rule-matching-behavior").value !== "custom"; };
    refresh();
    q("#rule-error").textContent = "";
    q("#rule-dialog").showModal();
    requestAnimationFrame(() => q("#rule-name").focus());
  };
  q("#rule-add")?.addEventListener("click", () => open());
  q("#rule-empty-add")?.addEventListener("click", () => open());
  q("#rule-search")?.addEventListener("input", (event) => { panel._query = event.target.value; panel._render(); requestAnimationFrame(() => { const input = q("#rule-search"); input?.focus(); input?.setSelectionRange(input.value.length, input.value.length); }); });
  root.querySelectorAll(".rule-edit").forEach((button) => button.addEventListener("click", () => open(button.dataset.id)));
  root.querySelectorAll(".rule-duplicate").forEach((button) => button.addEventListener("click", async () => { await panel._call("request_rules", "duplicate", {rule_id:button.dataset.id}); await panel._loadSection(); panel._toast("Rule duplicated"); }));
  root.querySelectorAll(".rule-delete").forEach((button) => button.addEventListener("click", async () => { if (!await panel._confirm("Delete Request Rule?", "This cannot be undone.", "Delete")) return; await panel._call("request_rules", "delete", {rule_id:button.dataset.id,confirm:true}); await panel._loadSection(); panel._toast("Rule deleted"); }));
  root.querySelectorAll(".rule-enabled").forEach((input) => input.addEventListener("change", async () => { const rule = (result.rules || []).find((item) => item.id === input.dataset.id); await panel._call("request_rules", "update", {rule_id:rule.id,rule:{...rule,enabled:input.checked,sensitive_matching_warning:undefined}}); await panel._loadSection(true); }));
  q("#rules-default-save")?.addEventListener("click", async () => { await panel._call("request_rules", "defaults", {defaults:{word_forms:q("#rules-default-word-forms").checked,wording_alternatives:q("#rules-default-wording").checked,fuzzy:q("#rules-default-fuzzy").checked,fuzzy_threshold:sensitivityValue(q("#rules-default-threshold").value)}}); await panel._loadSection(); panel._toast("Matching defaults saved"); });
  q("#rule-action-add")?.addEventListener("click", () => q("#rule-actions").append(actionRow(panel)));
  q("#rule-action-type")?.addEventListener("change", () => { const local = q("#rule-action-type").value === "local_action"; if (!local && q("#rule-match").value === "equals") q("#rule-scope").value = "conversation"; q("#rule-local-config").hidden = !local; q("#rule-routing-config").hidden = local; });
  q("#rule-match")?.addEventListener("change", () => { if (q("#rule-action-type").value === "model_routing" && q("#rule-match").value === "equals") q("#rule-scope").value = "conversation"; });
  q("#rule-matching-behavior")?.addEventListener("change", () => { q("#rule-custom-matching").hidden = q("#rule-matching-behavior").value !== "custom"; });
  root.querySelectorAll(".rule-close").forEach((button) => button.addEventListener("click", () => q("#rule-dialog").close()));
  q("#rule-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const actionType = q("#rule-action-type").value;
    const actions = [...root.querySelectorAll(".ha-action-row")].map((row) => { const ids = row.querySelector(".ha-target").value.split(",").map((item) => item.trim()).filter(Boolean); return {domain:row.querySelector(".ha-domain").value,service:row.querySelector(".ha-service").value,target:ids.length ? {entity_id:ids} : {},data:{}}; });
    const rule = {name:q("#rule-name").value,enabled:q("#rule-enabled-edit").checked,phrases:q("#rule-phrases").value.split("\n").map((item) => item.trim()).filter(Boolean),match_type:q("#rule-match").value,action_type:actionType,action:actionType === "local_action" ? {actions,success_response:q("#rule-success").value,failure_response:q("#rule-failure").value} : {model:q("#rule-model").value,reasoning_effort:q("#rule-reasoning").value,scope:q("#rule-scope").value,reset:q("#rule-reset").checked,success_response:q("#rule-routing-success").value},matching_behavior:q("#rule-matching-behavior").value,matching:{word_forms:q("#rule-word-forms").checked,wording_alternatives:q("#rule-wording").checked,fuzzy:q("#rule-fuzzy").checked,fuzzy_threshold:sensitivityValue(q("#rule-threshold").value)},order:(result.rules || []).find((item) => item.id === panel._editingRuleId)?.order ?? (result.rules || []).length};
    try { await panel._call("request_rules", panel._editingRuleId ? "update" : "create", {...(panel._editingRuleId ? {rule_id:panel._editingRuleId} : {}),rule}); q("#rule-dialog").close(); await panel._loadSection(); panel._toast(panel._editingRuleId ? "Rule updated" : "Rule created"); } catch (err) { q("#rule-error").textContent = err.message || String(err); }
  });
}
