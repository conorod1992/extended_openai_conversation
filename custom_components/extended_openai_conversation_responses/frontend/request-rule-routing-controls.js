export function syncRequestRuleRoutingControls(root, efforts = null, selectedEffort = null) {
  const actionType = root?.querySelector?.("#rule-action-type");
  const matchType = root?.querySelector?.("#rule-match");
  const scope = root?.querySelector?.("#rule-scope");
  const continueToAi = root?.querySelector?.("#rule-continue-to-ai");
  const continueMatching = root?.querySelector?.("#rule-continue-matching");
  const reasoning = root?.querySelector?.("#rule-reasoning");
  const help = root?.querySelector?.("#rule-routing-scope-help");
  if (!actionType || !matchType || !scope) return;
  const consumed = actionType.value === "model_routing"
    && !(continueToAi?.checked ?? false)
    && !(continueMatching?.checked ?? false);
  const requestOption = scope.querySelector('option[value="request"]');
  if (requestOption) requestOption.disabled = consumed;
  if (consumed) scope.value = "conversation";
  scope.disabled = consumed;
  if (reasoning && Array.isArray(efforts)) {
    const desired = selectedEffort ?? reasoning.value;
    setReasoningOptions(root, efforts, desired);
  }
  if (help) help.textContent = consumed
    ? "This is a complete routing command. It is acknowledged locally and is not sent to the AI provider, so it must change or reset the rest of this conversation."
    : continueToAi?.checked
      ? `This sends the ${root.querySelector("#rule-ai-input-mode")?.value === "capture" ? "selected captured value to the AI" : "original request to the AI unchanged"} after applying the route. This request only affects that provider call; Rest of this conversation also changes later requests.`
      : "Later matching rules can run after this route. This request only affects a later AI handoff in this request; Rest of this conversation also changes later requests.";
}

export function setReasoningOptions(root, efforts, selected = "") {
  const reasoning = root?.querySelector("#rule-reasoning");
  if (!reasoning) return;
  const captured = /^\{[A-Za-z_][A-Za-z0-9_]{0,63}\}$/.test(selected);
  const values = Array.isArray(efforts) ? efforts : [];
  const allowed = [...new Set([...values, ...(captured ? [selected] : [])])];
  reasoning.replaceChildren(...["", ...allowed].map((value) => {
    const option = reasoning.ownerDocument.createElement("option");
    option.value = value;
    option.textContent = value ? value.charAt(0).toUpperCase() + value.slice(1) : "Keep current";
    return option;
  }));
  reasoning.value = selected && allowed.includes(selected) ? selected : "";
}
