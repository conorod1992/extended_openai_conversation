import {adoptKeyedElements, elementFromMarkup, keyedElement, placeChildren, pruneKeys} from "./keyed-collection.js";
import {requestRuleSummary, renderLiveRequestTester} from "./management-decision-guidance.js";
import {renderRequestRuleMatchTester} from "./request-rules-match-test-ui.js";
export const fuzzyThresholdValue = (value) => {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 70 && parsed <= 100 ? parsed : 90;
};
const matchLabel = (value) => ({equals:"Equals",starts_with:"Starts with",ends_with:"Ends with",contains:"Contains",sentence_pattern:"Sentence pattern"}[value] || value);
export function applySentencePatternHelper(value, selectionStart, selectionEnd, kind) {
  const start = Math.max(0, Math.min(Number(selectionStart) || 0, value.length));
  const end = Math.max(start, Math.min(Number(selectionEnd) || start, value.length));
  const selected = value.slice(start, end);
  let snippet;
  let editStart;
  let editEnd;
  if (kind === "optional") {
    const inner = selected || "optional words";
    snippet = `[${inner}]`;
    editStart = start + 1;
    editEnd = editStart + inner.length;
  } else if (kind === "choice") {
    const inner = selected ? `${selected}|alternative` : "one|two";
    snippet = `(${inner})`;
    editStart = start + 1;
    editEnd = editStart + inner.length;
  } else if (kind === "variable") {
    const name = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(selected) ? selected : "name";
    snippet = `{${name}}`;
    editStart = start + 1;
    editEnd = editStart + name.length;
  } else if (kind === "range") {
    const name = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(selected) ? selected : "level";
    snippet = `{${name}=0..100}`;
    editStart = start + 1;
    editEnd = editStart + name.length;
  } else {
    throw new Error(`Unknown sentence-pattern helper: ${kind}`);
  }
  return {
    value: `${value.slice(0, start)}${snippet}${value.slice(end)}`,
    selectionStart: editStart,
    selectionEnd: editEnd,
  };
}

export async function recoverRequestRuleMutation(panel, error, label) {
  const message = error?.message || String(error || "Unknown error");
  panel._toast?.(`${label}: ${message}`, true);
  const cacheKey = panel._sectionCacheKey?.();
  if (cacheKey) panel._sectionCache?.delete(cacheKey);
  try {
    await panel._loadSection(true);
  } catch (refreshError) {
    const refreshMessage = refreshError?.message || String(refreshError || "Unknown error");
    panel._toast?.(`Unable to refresh Request Rules: ${refreshMessage}`, true);
  }
}

const matchingControls = (prefix, values, hidden = false) => `<div id="${prefix}-matching-controls" class="matching-settings" ${hidden ? "hidden" : ""}><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Normalize word forms</span><small>Treats simple variations such as “light” and “lights” as the same.</small></span><input id="${prefix}-word-forms" type="checkbox" ${values.word_forms ? "checked" : ""}></label><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Wording alternatives</span><small>Uses your saved alternative phrases, such as “switch on” matching “turn on”.</small></span><input id="${prefix}-wording" type="checkbox" ${values.wording_alternatives ? "checked" : ""}></label><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Fuzzy matching</span><small>Allows small speech-recognition or typing mistakes when no normal match succeeds.</small></span><input id="${prefix}-fuzzy" type="checkbox" ${values.fuzzy ? "checked" : ""}></label><label class="matching-setting fuzzy-sensitivity ${values.fuzzy ? "" : "is-disabled"}"><span class="matching-copy"><span class="matching-title">Fuzzy sensitivity</span><small>Controls how close a phrase must be before fuzzy matching is accepted. Conservative is least likely to match the wrong rule.</small></span><span class="matching-control"><input id="${prefix}-threshold" type="number" min="70" max="100" step="1" value="${fuzzyThresholdValue(values.fuzzy_threshold)}" ${values.fuzzy ? "" : "disabled"}></span></label></div>`;

const wordingEditor = (panel, groups) => `<details class="wording-editor eoc-details-base"><summary>Wording alternatives</summary><p class="help">Add different ways of saying the same thing. Separate multiple alternatives with commas.</p><div id="wording-groups">${groups.map((group) => `<div class="wording-group"><label>Main phrase<input class="wording-canonical" maxlength="100" value="${panel._e(group.canonical)}"></label><label>Other ways to say it<input class="wording-alternatives" value="${panel._e(group.alternatives.join(", "))}" placeholder="Comma-separated alternatives"></label><button type="button" class="icon wording-remove" aria-label="Remove wording alternative">×</button></div>`).join("")}</div><div class="section-actions"><button type="button" class="secondary" id="wording-add">Add wording alternative</button></div></details>`;

const ROUTING_HELP = '<section class="notice"><strong>AI routing command behavior</strong><p><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> routing rules are complete commands by default: they are acknowledged locally and apply to the rest of the current conversation. Enable <strong>Continue to AI</strong> to send the original request to the provider unchanged after applying the route. <strong>Starts with</strong>, <strong>Ends with</strong>, and <strong>Contains</strong> continue to the provider by default; matched words are not stripped.</p><p>A request-only reset bypasses a conversation override for that one provider request; it does not clear the saved conversation route. For strict matches, rules are evaluated from top to bottom and the first matching rule wins.</p></section>';

function requestRuleCard(panel, rule, index) {
  const result = panel._result || {};
  const rules = result.rules || [];
  const summary = requestRuleSummary(rule, result.defaults || {});
  return `<article data-rule-key="${panel._e(rule.id)}" class="request-rule-card ${rule.enabled ? "" : "disabled"}"><div class="rule-card-heading"><div><span class="type-badge ${rule.action_type === "local_action" ? "local" : "routing"}">${rule.action_type === "local_action" ? "Local command" : "AI routing"}</span><h2>${panel._e(rule.name)}</h2></div><label class="switch-label"><span class="sr-only">Enable ${panel._e(rule.name)}</span><input class="rule-enabled" data-id="${panel._e(rule.id)}" type="checkbox" ${rule.enabled ? "checked" : ""}></label></div><div class="phrase-chips">${rule.phrases.slice(0,4).map((phrase) => `<span><b>${matchLabel(rule.match_type)}</b> ${panel._e(phrase)}</span>`).join("")}${summary.hiddenPhrases ? `<span class="eoc-more-phrases">+${summary.hiddenPhrases} more</span>` : ""}</div><p>${panel._e(summary.action)}</p><p class="meta">${panel._e(summary.matching)}</p>${rule.sensitive_matching_warning && rule.match_type !== "sentence_pattern" ? `<p class="sensitive-warning">Review tolerant matching carefully: this rule controls a potentially sensitive Home Assistant domain.</p>` : ""}<div class="actions">${result.diagnostics?.[rule.id] ? `<p class="sensitive-warning"><strong>Rule inactive:</strong> ${panel._e(result.diagnostics[rule.id])} Edit and save this rule to use the current sentence-pattern syntax.</p>` : ""}<button type="button" class="secondary rule-move" data-id="${panel._e(rule.id)}" data-direction="up" ${index === 0 ? "disabled" : ""}>Move up</button><button type="button" class="secondary rule-move" data-id="${panel._e(rule.id)}" data-direction="down" ${index === rules.length - 1 ? "disabled" : ""}>Move down</button><button type="button" class="secondary rule-edit" data-id="${panel._e(rule.id)}">Edit</button><button type="button" class="secondary rule-duplicate" data-id="${panel._e(rule.id)}">Duplicate</button><button type="button" class="danger secondary-danger rule-delete" data-id="${panel._e(rule.id)}">Delete</button></div></article>`;
}

const EMPTY_RULES_MARKUP = '<section class="content-card empty-state"><h2>Create your first Request Rule</h2><p>Add a fast local command such as “good night”.</p><button type="button" id="rule-empty-add">Create rule</button></section>';
const NO_RULES_MATCH_CONTENT = '<h2>No rules match your search</h2><p>Try a different phrase or rule name.</p>';

function renderRulesPage(panel, {query = panel._query || "", inPlaceSearch = false} = {}) {
  const result = panel._result || {}, rules = result.rules || [];
  const defaults = {...{word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90}, ...((panel._rulesSettingsDraft || result).defaults || {})};
  const search = inPlaceSearch ? "" : String(query).trim().toLowerCase();
  const filtered = rules.map((rule, index) => ({rule, index})).filter(({rule}) => !search || `${rule.name} ${rule.phrases.join(" ")} ${rule.action_type}`.toLowerCase().includes(search));
  return `<section class="page-intro"><div><h1>Request Rules</h1><p>Create fast voice shortcuts and route AI requests before they reach OpenAI.</p></div><button type="button" id="rule-add">Create rule</button></section><section class="notice on"><strong>Local commands skip the AI/API call</strong><p>They normally respond faster. AI routing rules keep using the AI but can change the model or reasoning for one request or the active conversation.</p></section>${ROUTING_HELP}<section class="content-card rule-settings"><details class="eoc-details-base"><summary>Default matching settings</summary><p class="help">These settings apply to rules unless a rule has its own custom matching settings. Normal matches are always preferred before fuzzy matching is tried.</p>${matchingControls("rules-default", defaults)}</details>${wordingEditor(panel, (panel._rulesSettingsDraft || result).wording_groups || [])}</section><div class="search-row"><input id="rule-search" type="search" value="${panel._e(query)}" placeholder="Search rules" aria-label="Search Request Rules"><span class="count">${rules.length} rule${rules.length === 1 ? "" : "s"}</span></div><section class="rule-list">${filtered.map(({rule, index}) => requestRuleCard(panel, rule, index)).join("") || (rules.length ? "" : EMPTY_RULES_MARKUP)}<section class="content-card empty-state" data-eoc-rule-search-empty ${inPlaceSearch || filtered.length || !rules.length ? "hidden" : ""}>${NO_RULES_MATCH_CONTENT}</section></section>`;
}

export function renderRequestRules(panel, presentation = {query: panel._query || "", inPlaceSearch: Boolean(panel._eocInPlaceRequestRuleSearch)}) {
  return `${renderRulesPage(panel, presentation)}${renderRequestRuleMatchTester()}${renderLiveRequestTester()}`;
}

const ruleCollections = new WeakMap();
const pendingRuleButtons = new WeakSet();
const moveDisabled = (index, length, direction) => index < 0 || (direction === "up" ? index === 0 : index === length - 1);
const ruleSettingsSignature = panel => JSON.stringify([
  (panel._rulesSettingsDraft || panel._result)?.defaults,
  (panel._rulesSettingsDraft || panel._result)?.wording_groups,
]);

function prepareRequestRulesCollection(panel) {
  const list = panel.shadowRoot.querySelector(".rule-list");
  if (!list || ruleCollections.has(list)) return;
  ruleCollections.set(list, {
    cards: adoptKeyedElements(list, "[data-rule-key]", "ruleKey"),
    settings: ruleSettingsSignature(panel),
    empty: list.querySelector(".empty-state"),
  });
  reconcileRequestRules(panel);
}

export function reconcileRequestRules(panel) {
  const list = panel.shadowRoot.querySelector(".rule-list");
  const state = ruleCollections.get(list);
  // Matching-settings/wording editors retain their existing rendering lifecycle.
  // This fast path owns the rule collection, not those independent settings.
  if (!state || state.settings !== ruleSettingsSignature(panel)) return false;
  const result = panel._result || {};
  const rules = result.rules || [];
  const nodes = rules.map((rule, index) => {
    // Renumbering/reordering is not a change to the card's contents. Only the
    // two move-button boundaries depend on its current collection position.
    const presentation = {...rule};
    delete presentation.order;
    const record = keyedElement(
      state.cards, rule.id,
      JSON.stringify([presentation, result.defaults, result.diagnostics?.[rule.id]]),
      () => requestRuleCard(panel, rule, index),
    );
    record.moves ||= [...record.node.querySelectorAll(".rule-move")];
    for (const button of record.moves) {
      const disabled = pendingRuleButtons.has(button) || moveDisabled(index, rules.length, button.dataset.direction);
      if (button.disabled !== disabled) button.disabled = disabled;
    }
    return record.node;
  });
  if (!rules.length) {
    state.empty ||= elementFromMarkup(EMPTY_RULES_MARKUP);
    nodes.push(state.empty);
  }
  // Search owns its notice and visibility. Preserve that node for the existing
  // in-place filter pass, which runs after this collection update.
  const searchEmpty = list.querySelector("[data-eoc-rule-search-empty]");
  if (searchEmpty) nodes.push(searchEmpty);
  placeChildren(list, nodes);
  pruneKeys(state.cards, new Set(rules.map(rule => rule.id)));
  panel._eocRequestRuleCollectionRevision = (panel._eocRequestRuleCollectionRevision || 0) + 1;
  panel._eocRequestRuleSearchCache = null;
  return true;
}

export function requestRulesDialog() {
  return `<dialog id="rule-dialog" class="editor-dialog wide request-rule-dialog" aria-labelledby="rule-dialog-title"><form id="rule-form"><div class="dialog-header"><h2 id="rule-dialog-title">Create Request Rule</h2><button type="button" class="icon rule-close" aria-label="Close">×</button></div><div class="dialog-body"><div class="form-grid"><label>Rule name<input id="rule-name" required maxlength="120" placeholder="Shopping list"></label><label class="toggle"><span>Enabled</span><input id="rule-enabled-edit" type="checkbox" checked></label></div><section><h3>1. What will you say?</h3><label>Trigger phrases or patterns<textarea id="rule-phrases" required placeholder="Add {item} to my shopping list"></textarea><small>Put each alternative on a new line. Alternatives must use the same variable names.</small></label><div id="sentence-pattern-builder" class="section-actions" hidden><span class="help">Insert pattern:</span><button type="button" class="secondary pattern-helper" data-pattern-helper="optional">Optional</button><button type="button" class="secondary pattern-helper" data-pattern-helper="choice">Choice</button><button type="button" class="secondary pattern-helper" data-pattern-helper="variable">Variable</button><button type="button" class="secondary pattern-helper" data-pattern-helper="range">Number range</button></div><div id="rule-slot-help" class="notice" hidden><strong>Variable values</strong><p>Variable values let part of the request change each time. You can use the captured value in actions or responses.</p><p id="rule-slot-list"></p></div><label>How should it match?<select id="rule-match"><option value="equals">Equals</option><option value="starts_with">Starts with</option><option value="ends_with">Ends with</option><option value="contains">Contains</option><option value="sentence_pattern">ExtendedOpenAI sentence pattern</option></select></label><div id="sentence-pattern-help" class="notice" hidden><strong>Sentence-pattern syntax</strong><p>Use <code>[optional words]</code>, <code>(one|two)</code>, free-text values such as <code>{room}</code>, constrained values such as <code>{room=kitchen|bedroom}</code>, and integer ranges such as <code>{level=0..100}</code>. Escape syntax characters with <code>\\</code>, including <code>\\|</code> inside choices. Sentence-ending punctuation is tolerated. This is ExtendedOpenAI syntax; named expansions and permutations are not supported.</p></div></section><section><h3>2. What should happen?</h3><label>Behaviour<select id="rule-action-type"><option value="local_action">Run actions locally</option><option value="model_routing">Route through AI with different settings</option></select></label><div id="rule-local-config"><p class="help">Build a native Home Assistant action sequence that runs locally without asking the AI model. Conditions, delays, choose, repeat, parallel, and templates use the same editor and syntax as scripts and automations.</p><div id="rule-action-sequence-host"></div><div id="rule-action-slot-help" class="notice" hidden><strong>Captured values in actions</strong><p id="rule-action-slot-list"></p><p>Use a captured value as a script variable, for example <code>{{ item }}</code>. The same values are also available under <code>request.slots</code>.</p><p>To call an enabled configured function, add <code>extended_openai_conversation_responses.call_function</code> and provide its name and arguments.</p></div></div><div id="rule-routing-config" hidden><p class="help"><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> are complete commands by default. Enable <strong>Continue to AI</strong> to send the original request to the provider unchanged after applying the route. Broader Starts/Ends/Contains matches continue to the provider by default.</p><p class="help" id="rule-routing-scope-help"></p><label class="matching-setting"><span class="matching-copy"><span class="matching-title">Continue to AI</span><small>After applying these routing settings, send the original request to the AI provider.</small></span><input id="rule-continue-to-ai" type="checkbox" checked></label><div class="form-grid"><label>Model<input id="rule-model" placeholder="gpt-5-mini"></label><label>Reasoning effort<select id="rule-reasoning"><option value="">Keep current</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label><label>Scope<select id="rule-scope"><option value="request">This request only</option><option value="conversation">Rest of this conversation</option></select></label><label class="toggle"><span>Reset to configured defaults</span><input id="rule-reset" type="checkbox"></label></div></div></section><section><h3>3. What should the assistant say?</h3><div id="rule-local-responses" class="form-grid"><label>Success response<input id="rule-success" value="Done"><small>You can include a captured value such as <code>{item}</code>.</small></label><label>Failure response<input id="rule-failure" value="Sorry, that did not work"></label></div><p id="rule-routing-ai-response" class="help" hidden>The AI provider will generate the response.</p><label id="rule-routing-response" hidden>Acknowledgement<input id="rule-routing-success" value="Updated"></label></section><details id="rule-advanced" class="advanced-context-formatting eoc-details-base"><summary>Advanced matching and action configuration</summary><label>Matching behaviour<select id="rule-matching-behavior"><option value="defaults">Use default settings</option><option value="custom">Customize for this rule</option></select></label>${matchingControls("rule", {word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90}, true)}<p class="help">Sentence patterns use ExtendedOpenAI's bounded matcher, so fuzzy matching, wording alternatives, and word-form normalization do not apply. Advanced Home Assistant JSON can use <code>{slot}</code> in text values.</p></details><div id="rule-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" class="secondary rule-close">Cancel</button><button type="submit" id="rule-save">Save</button></div></form></dialog>`;
}

export function capturedSlotNames(text) {
  // Discover editor bindings without interpreting constrained values as syntax.
  // Backend validation remains authoritative for incomplete/invalid patterns.
  const source = String(text || ""), names = new Set();
  for (let index = 0; index < source.length; index += 1) {
    if (source[index] === "\\") { index += 1; continue; }
    if (source[index] !== "{") continue;
    let body = "", closed = false;
    while (++index < source.length) {
      if (source[index] === "\\") {
        body += source[index];
        if (++index < source.length) body += source[index];
      } else if (source[index] === "}") {
        closed = true;
        break;
      } else body += source[index];
    }
    const name = body.split("=", 1)[0].trim();
    if (closed && /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(name)) names.add(name);
  }
  return [...names];
}

const setFuzzyState = (root, prefix) => { const toggle = root.querySelector(`#${prefix}-fuzzy`), select = root.querySelector(`#${prefix}-threshold`); if (!toggle || !select) return; select.disabled = !toggle.checked; select.closest(".fuzzy-sensitivity")?.classList.toggle("is-disabled", !toggle.checked); };


export function createRequestRuleActionSelector(panel, host) {
  const actionSelector = host.ownerDocument.createElement("ha-selector");
  actionSelector.hass = panel._hass;
  actionSelector.selector = {action:{}};
  actionSelector.value = [];
  actionSelector.addEventListener("value-changed", (event) => {
    actionSelector.value = event.detail.value || [];
  });
  host.replaceChildren(actionSelector);
  return actionSelector;
}

export function loadRequestRuleActions(actionSelector, rule) {
  actionSelector.value = rule?.action?.actions || [{action:"script.turn_on", target:{}}];
}

export function readRequestRuleActions(actionSelector) {
  return actionSelector.value || [];
}

export function bindRequestRules(panel) {
  const root = panel.shadowRoot;
  const q = (selector) => root.querySelector(selector);
  const result = panel._result || {};
  const rules = () => panel._result?.rules || [];
  let editorRevision = result.revision;
  const actionSelectorHost = q("#rule-action-sequence-host");
  if (!actionSelectorHost) return;
  const actionSelector = createRequestRuleActionSelector(panel, actionSelectorHost);
  prepareRequestRulesCollection(panel);

  const refresh = () => {
    const local = q("#rule-action-type").value === "local_action";
    const grammar = q("#rule-match").value === "sentence_pattern";
    const slots = capturedSlotNames(q("#rule-phrases").value);
    q("#rule-local-config").hidden = !local;
    q("#rule-routing-config").hidden = local;
    q("#rule-local-responses").hidden = !local;
    const continueToAi = q("#rule-continue-to-ai")?.checked ?? true;
    q("#rule-routing-response").hidden = local || continueToAi;
    q("#rule-routing-ai-response").hidden = local || !continueToAi;
    q("#sentence-pattern-help").hidden = !grammar;
    q("#sentence-pattern-builder").hidden = !grammar;
    q("#rule-slot-help").hidden = !slots.length;
    q("#rule-slot-list").textContent = slots.length ? `Captured values: ${slots.join(", ")}` : "";
    q("#rule-action-slot-help").hidden = !slots.length;
    q("#rule-action-slot-list").textContent = slots.length ? slots.map((name) => `{{ ${name} }}`).join(", ") : "";
    q("#rule-matching-behavior").disabled = grammar;
    q("#rule-matching-controls").hidden = grammar || q("#rule-matching-behavior").value !== "custom";
    if (slots.length) q("#rule-match").value = "sentence_pattern";
    const requestScopeOption = q('#rule-scope option[value="request"]');
    if (requestScopeOption) requestScopeOption.disabled = !continueToAi;
    if (!local && !continueToAi && q("#rule-scope").value === "request") q("#rule-scope").value = "conversation";
  };

  const open = (id = null) => {
    editorRevision = panel._result?.revision;
    const rule = rules().find((item) => item.id === id);
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
    q("#rule-continue-to-ai").checked = rule?.action_type === "model_routing"
      ? (rule?.action?.continue_to_ai ?? !["equals", "sentence_pattern"].includes(rule?.match_type))
      : true;
    q("#rule-routing-success").value = rule?.action?.success_response || "Updated";
    q("#rule-matching-behavior").value = rule?.matching_behavior || "defaults";
    q("#rule-word-forms").checked = rule?.matching?.word_forms ?? true;
    q("#rule-wording").checked = rule?.matching?.wording_alternatives ?? true;
    q("#rule-fuzzy").checked = rule?.matching?.fuzzy ?? false;
    q("#rule-threshold").value = String(fuzzyThresholdValue(rule?.matching?.fuzzy_threshold ?? 90));
    loadRequestRuleActions(actionSelector, rule);
    refresh();
    setFuzzyState(root, "rule");
    q("#rule-error").textContent = "";
    q("#rule-dialog").showModal();
    panel._captureDialogBaseline?.(q("#rule-dialog"));
  };

  q("#rule-add")?.addEventListener("click", () => open());
  const list = q(".rule-list");
  list?.addEventListener("click", async event => {
    const button = event.target.closest?.("button");
    if (!button || button.disabled) return;
    if (button.id === "rule-empty-add") return open();
    if (button.matches(".rule-edit")) return open(button.dataset.id);
    if (!button.matches(".rule-move")) return;
    pendingRuleButtons.add(button);
    button.disabled = true;
    try {
      await panel._call("request_rules", "move", {
        rule_id: button.dataset.id,
        direction: button.dataset.direction,
        revision: panel._result?.revision,
      });
      await panel._loadSection(true);
    } catch (err) {
      await recoverRequestRuleMutation(panel, err, "Unable to move Request Rule");
    } finally {
      pendingRuleButtons.delete(button);
      const current = rules();
      button.disabled = moveDisabled(
        current.findIndex(rule => rule.id === button.dataset.id),
        current.length,
        button.dataset.direction,
      );
    }
  });
  q("#rules-default-fuzzy")?.addEventListener("change", () => setFuzzyState(root, "rules-default"));
  q("#rule-fuzzy")?.addEventListener("change", () => setFuzzyState(root, "rule"));
  setFuzzyState(root, "rules-default");

  q("#rule-test")?.addEventListener("click", async () => { const output = q("#rule-test-result"), text = q("#rule-test-text").value.trim(); if (!text) return; output.textContent = "Processing…"; try { const response = await panel._call("request_rules", "test", {text}), captured = Object.entries(response.captured_values || {}); output.textContent = `${response.response || "(No response text)"}\nConversation ID: ${response.conversation_id || "—"}\nPath: ${response.handled_locally ? "Handled locally" : "AI provider"}${response.matched_rule ? `\nMatched rule: ${response.matched_rule.name}` : ""}${captured.length ? `\nCaptured values:\n${captured.map(([name,value]) => `${name} → ${value}`).join("\n")}` : ""}`; } catch (err) { output.textContent = err.message || String(err); } });
  const bindRemove = () => root.querySelectorAll(".wording-remove").forEach((button) => button.onclick = () => button.closest(".wording-group").remove());
  bindRemove();
  q("#wording-add")?.addEventListener("click", () => { const wrapper = document.createElement("div"); wrapper.className = "wording-group"; wrapper.innerHTML = `<label>Main phrase<input class="wording-canonical" maxlength="100"></label><label>Other ways to say it<input class="wording-alternatives" placeholder="Comma-separated alternatives"></label><button type="button" class="icon wording-remove" aria-label="Remove wording alternative">×</button>`; q("#wording-groups").append(wrapper); bindRemove(); });

  q("#rule-action-type")?.addEventListener("change", refresh);
  q("#rule-match")?.addEventListener("change", refresh);
  q("#rule-continue-to-ai")?.addEventListener("change", refresh);
  root.querySelectorAll(".pattern-helper").forEach((button) => button.addEventListener("click", () => {
    const textarea = q("#rule-phrases");
    const result = applySentencePatternHelper(textarea.value, textarea.selectionStart, textarea.selectionEnd, button.dataset.patternHelper);
    textarea.value = result.value;
    textarea.focus();
    textarea.setSelectionRange(result.selectionStart, result.selectionEnd);
    refresh();
  }));
  q("#rule-phrases")?.addEventListener("input", refresh);
  q("#rule-matching-behavior")?.addEventListener("change", refresh);
  root.querySelectorAll(".rule-close").forEach((button) => button.addEventListener("click", () => q("#rule-dialog").close()));
  q("#rule-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const save = q("#rule-save");
    if (save.disabled) return;
    panel._setSaving(save, true);
    try {
      const actionType = q("#rule-action-type").value;
      const actions = readRequestRuleActions(actionSelector);
      const rule = {name:q("#rule-name").value, enabled:q("#rule-enabled-edit").checked, phrases:q("#rule-phrases").value.split("\n").map((item) => item.trim()).filter(Boolean), match_type:q("#rule-match").value, action_type:actionType, action:actionType === "local_action" ? {actions, success_response:q("#rule-success").value, failure_response:q("#rule-failure").value} : {model:q("#rule-model").value, reasoning_effort:q("#rule-reasoning").value, scope:q("#rule-scope").value, reset:q("#rule-reset").checked, continue_to_ai:q("#rule-continue-to-ai").checked, success_response:q("#rule-routing-success").value}, matching_behavior:q("#rule-matching-behavior").value, matching:{word_forms:q("#rule-word-forms").checked, wording_alternatives:q("#rule-wording").checked, fuzzy:q("#rule-fuzzy").checked, fuzzy_threshold:fuzzyThresholdValue(q("#rule-threshold").value)}, order:rules().find((item) => item.id === panel._editingRuleId)?.order ?? rules().length};
      await panel._call("request_rules", panel._editingRuleId ? "update" : "create", {...(panel._editingRuleId ? {rule_id:panel._editingRuleId} : {}),rule,revision: editorRevision});
      q("#rule-dialog").close();
      await panel._loadSection(true);
    } catch (err) { q("#rule-error").textContent = err.message || String(err); }
    finally { panel._setSaving(save, false); }
  });
}
