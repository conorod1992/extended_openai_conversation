const matchLabel = (value) => ({equals:"Equals",starts_with:"Starts with",ends_with:"Ends with",contains:"Contains",sentence_pattern:"Sentence pattern"}[value] || value);

export const REQUEST_RULE_MATCH_MAX_CHARS = 2048;
const unchanged = " Nothing was changed.";

function localActionSummary(response) {
  const action = response?.would_do || {};
  const count = Number(action.action_count || 0);
  const functions=(action.functions || []).map((item)=>`${item.name}${item.result_alias ? ` → ${item.result_alias}` : ""}`).join(", ");
  const outcome = action.conditional ? "Conditional: actions may stop or fail." : action.continue_matching ? "After success, keep checking later rules." : action.consumed ? "The command would be consumed locally." : "After success, the selected AI input would continue to the AI.";
  return `Configured: ${count} local action${count === 1 ? "" : "s"}${functions ? `, including ${functions}` : ""}. ${outcome} No action ran; no Function result was invented.`;
}

function routingSummary(response) {
  const action = response?.would_do || {};
  if(action.conditional)return `Earlier actions may stop; any AI handoff is conditional.${unchanged}`;
  if(action.continue_matching)return `Would apply routing, then keep checking later rules.${unchanged}`;
  const consumed = Boolean(action.consumed);
  const conversation = action.scope === "conversation";
  if (action.reset) {
    if (consumed) return `Would consume this command locally and reset conversation routing. The AI provider would not receive this command.${unchanged}`;
    if (conversation) return `Would clear conversation routing, then send the selected AI input using configured model settings.${unchanged}`;
    return `Would use configured model settings for this request only. The saved conversation routing override would remain for the next request.${unchanged}`;
  }
  const changes = [];
  if (action.model) changes.push(`model ${action.model}`);
  if (action.reasoning_effort) changes.push(`${action.reasoning_effort} reasoning`);
  const route = changes.join(" and ") || "the routing override";
  if (consumed) return `Would consume this command locally and apply ${route} for this conversation. The AI provider would not receive this command.${unchanged}`;
  const scope = conversation ? "this request and later requests in this conversation" : "this request only";
  return `Would send the selected AI input using ${route} for ${scope}.${unchanged}`;
}

export function formatRequestRuleMatchResult(panel, response) {
  const skipped=(response?.skipped_conditions || []).map((item)=>item.name).join(", ");
  if (!response?.matched) {
    if(skipped)return `<div class="notice"><strong>No eligible Request Rule</strong><p>Text matched ${panel._e(skipped)}, but Only when conditions were false. No action executed.</p></div>`;
    return '<div class="notice"><strong>No Request Rule matched</strong><p>In a real request, processing would continue normally; the original request could reach the AI provider. Nothing was executed.</p></div>';
  }
  const rule = response.rule || {};
  const captured = Object.entries(response.captured_values || {});
  const matchKind = "Normal match";
  const actionType = rule.action_type === "local_action" ? "Local command" : "AI routing";
  const wouldDo = rule.action_type === "local_action" ? localActionSummary(response) : routingSummary(response);
  const chain=(response.matched_rules || []).map((item)=>`<li>${panel._e(item.rule.name)} — ${item.status === "would_send_to_ai" ? "would send to AI, stopped" : panel._e(item.status)}${item.ai_input ? ` · ${item.ai_input.conditional ? "Possible AI input: " : ""}${item.ai_input.mode === "capture" ? `Captured value: ${panel._e(item.ai_input.capture)}` : "Original request"} → ${panel._e(item.ai_input.provider_input)}` : ""}</li>`).join("");
  return `<div class="notice on rule-match-preview"><strong>${response.conditional ? "Conditional match" : "Matched"}: ${panel._e(rule.name || "Unnamed rule")}</strong>${chain ? `<ol class="rule-match-chain">${chain}</ol>` : ""}<p>${panel._e(actionType)} · ${panel._e(matchLabel(rule.match_type))} · ${panel._e(matchKind)}</p><dl class="match-preview-details"><div><dt>Matched phrase</dt><dd>${panel._e(response.matched_phrase || "—")}</dd></div>${captured.length ? `<div><dt>Captured values</dt><dd>${captured.map(([name,value]) => `${panel._e(name)} → ${panel._e(value)}`).join("<br>")}</dd></div>` : ""}${skipped ? `<div><dt>Skipped</dt><dd>${panel._e(skipped)}: Only when conditions were false</dd></div>` : ""}<div><dt>Would happen</dt><dd>${panel._e(wouldDo)}</dd></div></dl></div>`;
}

export function renderRequestRuleMatchTester() {
  return `<section class="content-card" id="rule-match-tester"><h2>Preview rule match (safe)</h2><p>Checks possible outcomes using the real matcher. No actions run.</p><div class="notice on"><strong>Safe preview — nothing executes</strong><p>No Home Assistant action runs, conversation routing is not changed, and the AI provider is not called.</p></div><div class="search-row"><input id="rule-match-test-text" type="text" maxlength="${REQUEST_RULE_MATCH_MAX_CHARS}" placeholder="Turn off the kitchen light" aria-label="Request Rule test text"><button type="button" id="rule-match-test">Preview match</button></div><p class="help">Preview and live matching: ${REQUEST_RULE_MATCH_MAX_CHARS} characters (and 256 words) maximum.</p><div id="rule-match-test-result" aria-live="polite"></div></section>`;
}


export function bindRequestRuleMatchTester(panel) {
  const root = panel.shadowRoot;
  const input = root.querySelector("#rule-match-test-text");
  const button = root.querySelector("#rule-match-test");
  const output = root.querySelector("#rule-match-test-result");
  if (!input || !button || !output || button.dataset.eocSafeTesterBound) return;
  button.dataset.eocSafeTesterBound = "1";

  const run = async () => {
    const text = input.value;
    if (!text.trim() || button.disabled) return;
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
