const matchLabel = (value) => ({equals:"Equals",starts_with:"Starts with",ends_with:"Ends with",contains:"Contains",sentence_pattern:"Sentence pattern"}[value] || value);

function localActionSummary(response) {
  const count = Number(response?.would_do?.action_count || 0);
  return `Would run ${count} local action${count === 1 ? "" : "s"}. Nothing was executed.`;
}

function routingSummary(response) {
  const action = response?.would_do || {};
  if (action.reset) return "Would return the active conversation to the configured model settings. Nothing was changed.";
  const changes = [];
  if (action.model) changes.push(`model ${action.model}`);
  if (action.reasoning_effort) changes.push(`${action.reasoning_effort} reasoning`);
  const scope = action.scope === "conversation" ? "the rest of this conversation" : "this request";
  return `Would apply ${changes.join(" and ") || "the routing override"} to ${scope}. Nothing was changed and the AI provider was not called.`;
}

export function formatRequestRuleMatchResult(panel, response) {
  if (!response?.matched) {
    return '<div class="notice"><strong>No Request Rule matched</strong><p>The request would continue through the normal processing path. No Home Assistant action ran and the AI provider was not called.</p></div>';
  }
  const rule = response.rule || {};
  const captured = Object.entries(response.captured_values || {});
  const matchKind = response.fuzzy
    ? `Fuzzy match · ${Number(response.score || 0).toFixed(1)}%`
    : "Normal match";
  const actionType = rule.action_type === "local_action" ? "Local command" : "AI routing";
  const wouldDo = rule.action_type === "local_action" ? localActionSummary(response) : routingSummary(response);
  return `<div class="notice on rule-match-preview"><strong>Matched: ${panel._e(rule.name || "Unnamed rule")}</strong><p>${panel._e(actionType)} · ${panel._e(matchLabel(rule.match_type))} · ${panel._e(matchKind)}</p><dl class="match-preview-details"><div><dt>Matched phrase</dt><dd>${panel._e(response.matched_phrase || "—")}</dd></div>${captured.length ? `<div><dt>Captured values</dt><dd>${captured.map(([name,value]) => `${panel._e(name)} → ${panel._e(value)}`).join("<br>")}</dd></div>` : ""}<div><dt>Would happen</dt><dd>${panel._e(wouldDo)}</dd></div></dl></div>`;
}

export function renderRequestRuleMatchTester() {
  return `<section class="content-card" id="rule-match-tester"><h2>Test matching</h2><p>Check which enabled Request Rule would win for some text using the same matcher as real requests.</p><div class="notice on"><strong>Safe preview only</strong><p>This checks matching only. It does not run Home Assistant actions, change conversation routing, or call the AI provider.</p></div><div class="search-row"><input id="rule-match-test-text" type="text" placeholder="Turn off the kitchen light" aria-label="Request text to test against Request Rules"><button type="button" id="rule-match-test">Test match</button></div><div id="rule-match-test-result" aria-live="polite"></div></section>`;
}

export function transformRequestRulesMatchTester(html) {
  const replacement = renderRequestRuleMatchTester();
  return html.replace(
    /<section class="content-card"><h2>Test a request<\/h2>[\s\S]*?<\/section>\s*$/,
    replacement,
  );
}

export function bindRequestRuleMatchTester(panel) {
  const root = panel.shadowRoot;
  const input = root.querySelector("#rule-match-test-text");
  const button = root.querySelector("#rule-match-test");
  const output = root.querySelector("#rule-match-test-result");
  if (!input || !button || !output) return;

  const run = async () => {
    const text = input.value.trim();
    if (!text || button.disabled) return;
    button.disabled = true;
    output.textContent = "Checking…";
    try {
      const response = await panel._call("request_rules", "test_match", {text});
      output.innerHTML = formatRequestRuleMatchResult(panel, response);
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
