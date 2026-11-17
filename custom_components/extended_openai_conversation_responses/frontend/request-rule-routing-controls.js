const unforcedScopes = new WeakMap();

export function syncRequestRuleRoutingControls(root, efforts = null, selectedEffort = null) {
  const actionType = root?.querySelector?.("#rule-action-type");
  const scope = root?.querySelector?.("#rule-scope");
  const continueToAi = root?.querySelector?.("#rule-continue-to-ai");
  const continueMatching = root?.querySelector?.("#rule-continue-matching");
  const reasoning = root?.querySelector?.("#rule-reasoning");
  const help = root?.querySelector?.("#rule-routing-scope-help");
  if (!actionType || !scope) return;
  const consumed = actionType.value === "model_routing"
    && !(continueToAi?.checked ?? false)
    && !(continueMatching?.checked ?? false);
  const requestOption = scope.querySelector('option[value="request"]');
  if (requestOption) requestOption.disabled = consumed;
  if (consumed) {
    if (!scope.disabled) unforcedScopes.set(scope, scope.value);
    scope.value = "conversation";
  } else if (scope.disabled) {
    scope.value = unforcedScopes.get(scope);
  }
  scope.disabled = consumed;
  if (reasoning && Array.isArray(efforts)) {
    const desired = selectedEffort ?? reasoning.value;
    setReasoningOptions(root, efforts, desired);
  }
  if (help) help.textContent = consumed
    ? "This command is acknowledged locally. It must change or reset the rest of the conversation."
    : continueToAi?.checked
      ? `This sends the ${root.querySelector("#rule-ai-input-mode")?.value === "capture" ? "captured value" : "original request"} to the AI after applying the route.`
      : "Later matching rules can run. Request-only scope applies to any AI handoff in this request.";
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
