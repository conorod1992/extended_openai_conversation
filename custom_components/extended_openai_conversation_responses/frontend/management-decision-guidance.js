import {friendlySettingValue} from "./management-configuration-clarity.js";

const PATCHED = Symbol.for("extended-openai.management-decision-guidance");

const DEFAULT_GUIDANCE_KEYS = new Set([
  "api_mode",
  "conversation_continuity",
  "continue_conversation",
  "temporary_memory",
  "memory_mode",
  "memory_retrieval_mode",
  "shared_memory_mode",
  "voice_scope_policy",
  "voice_unmapped_policy",
  "web_search_context",
]);

const RECOMMENDED_CHOICES = Object.freeze({
  api_mode: new Set(["auto"]),
});

const MATCH_LABELS = Object.freeze({
  equals: "Equals",
  starts_with: "Starts with",
  ends_with: "Ends with",
  contains: "Contains",
  sentence_pattern: "Sentence pattern",
});

function titleCase(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function assistantScopeLabel(agent) {
  if (!agent) return "";
  const title = String(agent.title || "Unnamed assistant").trim();
  const entryTitle = String(agent.entry_title || "").trim();
  return entryTitle && entryTitle !== title ? `${title} — ${entryTitle}` : title;
}

export function displayDefaultValue(key, value) {
  const friendly = friendlySettingValue(key, value);
  if (friendly) return friendly;
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (value === null || value === undefined || value === "") return "None";
  return typeof value === "string" ? titleCase(value) : String(value);
}

export function configurationDecisionBadges(key, defaults = {}) {
  if (!DEFAULT_GUIDANCE_KEYS.has(key) || !Object.prototype.hasOwnProperty.call(defaults, key)) return [];
  const value = defaults[key];
  const display = displayDefaultValue(key, value);
  const badges = [{kind:"default", text:`Default: ${display}`}];
  if (RECOMMENDED_CHOICES[key]?.has(String(value))) badges.push({kind:"recommended", text:`Recommended: ${display}`});
  return badges;
}

function matchingSettings(rule, defaults) {
  if (rule?.match_type === "sentence_pattern") return null;
  return rule?.matching_behavior === "defaults" ? defaults : (rule?.matching || {});
}

export function requestRuleSummary(rule = {}, defaults = {}) {
  const phrases = Array.isArray(rule.phrases) ? rule.phrases : [];
  const match = MATCH_LABELS[rule.match_type] || titleCase(rule.match_type || "match");
  const phraseLabel = `${phrases.length} trigger phrase${phrases.length === 1 ? "" : "s"}`;
  let matching;
  if (rule.match_type === "sentence_pattern") {
    matching = `${phraseLabel} · ${match} · Hassil grammar`;
  } else {
    const source = rule.matching_behavior === "defaults" ? "Default matching" : "Custom matching";
    const settings = matchingSettings(rule, defaults) || {};
    const threshold = Number(settings.fuzzy_threshold ?? 90);
    const fuzzyLabel = threshold >= 93 ? "conservative" : threshold >= 88 ? "normal" : "tolerant";
    const fuzzy = settings.fuzzy ? ` · Fuzzy fallback: ${titleCase(fuzzyLabel)}` : "";
    matching = `${phraseLabel} · ${match} · ${source}${fuzzy}`;
  }

  if (rule.action_type === "local_action") {
    const actions = Array.isArray(rule.action?.actions) ? rule.action.actions : [];
    const response = String(rule.action?.success_response || "").trim();
    return {
      action: `Runs ${actions.length} local step${actions.length === 1 ? "" : "s"} without an AI request${response ? ` · replies “${response}”` : ""}`,
      matching,
      hiddenPhrases: Math.max(0, phrases.length - 4),
    };
  }

  if (rule.action?.reset) {
    return {
      action: "Returns model and reasoning to the assistant's configured defaults for the active conversation.",
      matching,
      hiddenPhrases: Math.max(0, phrases.length - 4),
    };
  }

  const model = rule.action?.model ? `Model: ${rule.action.model}` : "Keep current model";
  const reasoning = rule.action?.reasoning_effort ? `${titleCase(rule.action.reasoning_effort)} reasoning` : "Keep current reasoning";
  const scope = rule.action?.scope === "conversation" ? "rest of conversation" : "this request only";
  return {
    action: `${model} · ${reasoning} · ${scope}`,
    matching,
    hiddenPhrases: Math.max(0, phrases.length - 4),
  };
}

export function formatLiveRequestResult(response = {}) {
  const captured = Object.entries(response.captured_values || {});
  return [
    response.response || "(No response text)",
    `Path: ${response.handled_locally ? "Handled locally" : "AI provider"}`,
    response.matched_rule ? `Matched rule: ${response.matched_rule.name}` : "Matched rule: none",
    captured.length ? `Captured values: ${captured.map(([name, value]) => `${name}=${value}`).join(", ")}` : null,
    `Conversation ID: ${response.conversation_id || "—"}`,
  ].filter(Boolean).join("\n");
}

function configurationDefaults(panel) {
  if (panel._draftAgentId === panel._agentId && panel._configData?.defaults) return panel._configData.defaults;
  if (panel._result?.config && panel._result?.defaults) return panel._result.defaults;
  if (panel._settingsSearchConfigAgentId === panel._agentId && panel._settingsSearchConfig?.defaults) return panel._settingsSearchConfig.defaults;
  return {};
}

function ensureStyles(panel) {
  const root = panel.shadowRoot;
  if (!root || root.querySelector("style[data-eoc-decision-guidance]")) return;
  const style = document.createElement("style");
  style.dataset.eocDecisionGuidance = "";
  style.textContent = `
    .eoc-decision-badge{display:inline-flex;align-items:center;min-height:20px;padding:2px 7px;border-radius:999px;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--secondary-text-color);font-size:11px;font-weight:650;line-height:1.25;white-space:nowrap}
    .eoc-decision-badge.recommended{border-color:color-mix(in srgb,var(--primary-color) 45%,var(--divider-color));color:var(--primary-text-color)}
    .eoc-confirm-scope,.eoc-restore-scope{margin:12px 0 0;padding:10px 12px;border-radius:8px;background:var(--secondary-background-color);color:var(--secondary-text-color);line-height:1.4}
    .eoc-confirm-scope strong,.eoc-restore-scope strong{color:var(--primary-text-color)}
    .eoc-more-phrases{opacity:.8}
    .eoc-live-request-test{margin-top:16px}
    .eoc-live-request-test>summary{cursor:pointer;display:flex;align-items:center;justify-content:space-between;gap:12px;font-weight:650}
    .eoc-live-request-body{padding-top:14px}
    .eoc-live-request-result{margin-top:12px;white-space:pre-wrap;line-height:1.45}
    .eoc-live-label{display:inline-flex;align-items:center;min-height:22px;padding:2px 8px;border-radius:999px;border:1px solid var(--error-color);color:var(--error-color);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.04em}
  `;
  root.append(style);
}

function enhanceDefaultGuidance(panel) {
  const root = panel.shadowRoot;
  if (!root) return;
  const defaults = configurationDefaults(panel);
  root.querySelectorAll("[data-setting][data-field]").forEach((field) => {
    const row = field.querySelector(".setting-label-row");
    if (!row) return;
    row.querySelectorAll(".eoc-decision-badge").forEach((badge) => badge.remove());
    for (const badge of configurationDecisionBadges(field.dataset.field, defaults)) {
      const node = document.createElement("span");
      node.className = `eoc-decision-badge ${badge.kind}`;
      node.textContent = badge.text;
      row.append(node);
    }
  });
}

function addScopeNode(container, className, agentLabel, subject = "") {
  if (!container) return;
  let node = container.querySelector(`.${className}`);
  if (!agentLabel) {
    node?.remove();
    return;
  }
  if (!node) {
    node = document.createElement("p");
    node.className = className;
    container.append(node);
  }
  node.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = "Assistant: ";
  node.append(strong, document.createTextNode(agentLabel));
  if (subject) node.append(document.createElement("br"), document.createTextNode(`Item: ${subject}`));
}

function enhanceRestoreScope(panel) {
  const dialog = panel.shadowRoot?.querySelector("#restore-dialog");
  const body = dialog?.querySelector(".dialog-body");
  const label = assistantScopeLabel(panel._selectedAgent?.());
  if (!body || !label) return;
  let node = body.querySelector(".eoc-restore-scope");
  if (!node) {
    node = document.createElement("p");
    node.className = "eoc-restore-scope";
    body.prepend(node);
  }
  node.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = "This backup will replace: ";
  node.append(strong, document.createTextNode(label));
}

function enhanceConfirmationScope(panel, subject = "") {
  const dialog = panel.shadowRoot?.querySelector("#confirm-dialog");
  const body = dialog?.querySelector(".dialog-body");
  addScopeNode(body, "eoc-confirm-scope", assistantScopeLabel(panel._selectedAgent?.()), subject);
}

function enhanceRuleCards(panel) {
  const root = panel.shadowRoot;
  const rules = panel._result?.rules || [];
  const defaults = panel._result?.defaults || {};
  if (!root || !Array.isArray(rules)) return;
  root.querySelectorAll(".request-rule-card").forEach((card) => {
    const button = card.querySelector(".rule-edit[data-id],.rule-delete[data-id]");
    const rule = rules.find((item) => item.id === button?.dataset.id);
    if (!rule) return;
    const summary = requestRuleSummary(rule, defaults);
    const phrases = card.querySelector(".phrase-chips");
    phrases?.querySelector(".eoc-more-phrases")?.remove();
    if (phrases && summary.hiddenPhrases) {
      const more = document.createElement("span");
      more.className = "eoc-more-phrases";
      more.textContent = `+${summary.hiddenPhrases} more`;
      phrases.append(more);
    }
    const description = phrases?.nextElementSibling;
    if (description?.tagName === "P") description.textContent = summary.action;
    const meta = card.querySelector("p.meta");
    if (meta) meta.textContent = summary.matching;
  });
}

function enhanceSafeTest(root) {
  const section = root.querySelector("#rule-match-tester");
  if (!section) return null;
  const heading = section.querySelector("h2");
  if (heading) heading.textContent = "Preview rule match (safe)";
  const intro = section.querySelector(":scope > p");
  if (intro) intro.textContent = "Checks which enabled Request Rule would win using the real matcher, without running the resulting action.";
  const notice = section.querySelector(".notice");
  const noticeTitle = notice?.querySelector("strong");
  const noticeCopy = notice?.querySelector("p");
  if (noticeTitle) noticeTitle.textContent = "Safe preview — nothing executes";
  if (noticeCopy) noticeCopy.textContent = "No Home Assistant action runs, conversation routing is not changed, and the AI provider is not called.";
  const button = section.querySelector("#rule-match-test");
  if (button) button.textContent = "Preview match";
  return section;
}

function bindLiveRequest(panel, section) {
  if (!section || section.dataset.eocBound) return;
  const input = section.querySelector("#eoc-rule-live-text");
  const button = section.querySelector("#eoc-rule-live-run");
  const output = section.querySelector("#eoc-rule-live-result");
  if (!input || !button || !output) return;
  section.dataset.eocBound = "";
  const run = async () => {
    const text = input.value.trim();
    if (!text || button.disabled) return;
    const confirmed = await panel._confirm(
      "Run full request?",
      "This uses the assistant's real processing path. It may execute Home Assistant actions, change conversation routing, or call the AI provider.",
      "Run live request",
    );
    if (!confirmed) return;
    button.disabled = true;
    output.textContent = "Processing live request…";
    try {
      const response = await panel._call("request_rules", "test", {text});
      output.textContent = formatLiveRequestResult(response);
    } catch (err) {
      output.textContent = err.message || String(err);
    } finally {
      button.disabled = false;
    }
  };
  button.addEventListener("click", () => { void run(); });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    void run();
  });
}

function enhanceRequestRuleTesting(panel) {
  const root = panel.shadowRoot;
  if (!root) return;
  const safe = enhanceSafeTest(root);
  if (!safe) return;
  let live = root.querySelector("#eoc-rule-live-test");
  if (!live) {
    live = document.createElement("details");
    live.id = "eoc-rule-live-test";
    live.className = "content-card eoc-live-request-test";
    live.innerHTML = `<summary><span>Run full request (live)</span><span class="eoc-live-label">Live</span></summary><div class="eoc-live-request-body"><p>Runs text through the same full processing path as a real request to this assistant.</p><div class="notice"><strong>This can have real effects</strong><p>Unlike the safe preview above, this may execute Home Assistant actions, change conversation routing, or call the AI provider. A confirmation is shown before it runs.</p></div><div class="search-row"><input id="eoc-rule-live-text" type="text" placeholder="Turn off the kitchen light" aria-label="Live request text"><button type="button" id="eoc-rule-live-run">Run live request</button></div><pre id="eoc-rule-live-result" class="eoc-live-request-result" aria-live="polite"></pre></div>`;
    safe.insertAdjacentElement("afterend", live);
  }
  bindLiveRequest(panel, live);
}

function bindRequestRuleDeleteContext(panel) {
  const root = panel.shadowRoot;
  if (!root || root.__eocDecisionDeleteContextBound) return;
  root.__eocDecisionDeleteContextBound = true;
  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.(".rule-delete[data-id]");
    if (!button) return;
    const rule = (panel._result?.rules || []).find((item) => item.id === button.dataset.id);
    panel._eocDecisionConfirmSubject = rule?.name ? `Request Rule “${rule.name}”` : "Request Rule";
  }, true);
}

function enhanceRequestRules(panel) {
  if (panel._page !== "capabilities" || panel._subsection !== "request-rules") return;
  bindRequestRuleDeleteContext(panel);
  enhanceRuleCards(panel);
  enhanceRequestRuleTesting(panel);
}

function enhancePanel(panel) {
  if (!panel.shadowRoot) return;
  ensureStyles(panel);
  enhanceDefaultGuidance(panel);
  enhanceRestoreScope(panel);
  enhanceRequestRules(panel);
}

export function installManagementDecisionGuidance(registry = globalThis.customElements) {
  if (typeof document === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      enhancePanel(this);
      queueMicrotask(() => enhancePanel(this));
      return result;
    };

    const originalConfirm = prototype._confirm;
    prototype._confirm = function(...args) {
      const subject = this._eocDecisionConfirmSubject || "";
      this._eocDecisionConfirmSubject = "";
      const result = originalConfirm.apply(this, args);
      enhanceConfirmationScope(this, subject);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  void installManagementDecisionGuidance();
}
