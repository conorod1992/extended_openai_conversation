const unforcedScopes = new WeakMap();
export const hasRoutingCapture = value => /\{(?:[A-Za-z_][A-Za-z0-9_]{0,63}\}|[{%#])/.test(value);

export function syncRequestRuleRoutingControls(root, efforts = null, selectedEffort = null, retainUnavailable = false) {
  const actionType = root?.querySelector?.("#rule-action-type");
  const scope = root?.querySelector?.("#rule-scope");
  const continueToAi = root?.querySelector?.("#rule-continue-to-ai");
  const reasoning = root?.querySelector?.("#rule-reasoning");
  const help = root?.querySelector?.("#rule-routing-scope-help");
  if (!actionType || !scope) return;
  const consumed = actionType.value === "model_routing"
    && !(continueToAi?.checked ?? false);
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
    setReasoningOptions(root, efforts, desired, retainUnavailable);
  }
  if (help) help.textContent = consumed
    ? "This command is acknowledged locally. It must change or reset the rest of the conversation."
    : continueToAi?.checked
      ? `This sends the ${root.querySelector("#rule-ai-input-mode")?.value === "capture" ? "captured value" : "original request"} to the AI after applying the route.`
      : "";
}

export function setReasoningOptions(root, efforts, selected = "", retainUnavailable = false) {
  const reasoning = root?.querySelector?.("#rule-reasoning");
  if (!reasoning) return;
  const fixed = [...new Set(efforts.filter(value => value && !hasRoutingCapture(value)))];
  const legacy = hasRoutingCapture(selected);
  const unavailable = Boolean(selected && !fixed.includes(selected) && retainUnavailable);
  const options = legacy || unavailable ? ["", ...fixed, selected] : ["", ...fixed];
  reasoning.replaceChildren(...options.map((value) => {
    const option = reasoning.ownerDocument.createElement("option");
    option.value = value;
    option.disabled = (legacy || unavailable) && value === selected;
    option.textContent = option.disabled ? legacy ? `Previous capture ${value} (replace)` : `${value} (unavailable)`
      : value ? value.charAt(0).toUpperCase() + value.slice(1) : "Keep current";
    return option;
  }));
  reasoning.value = options.includes(selected) ? selected : "";
}
