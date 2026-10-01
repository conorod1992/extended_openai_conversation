// Describe only statically knowable outcomes; branches/templates remain runtime decisions.
export function localRuleActionSummary(action = {}) {
  const actions = Array.isArray(action.actions) ? action.actions : [];
  const prefix = `Runs ${actions.length} local step${actions.length === 1 ? "" : "s"}`;
  let response;
  let stopped = false;
  let failed = false;
  let dynamic = false;
  for (const step of actions) {
    if (!step || step.enabled === false) continue;
    if ((step.enabled !== undefined && typeof step.enabled !== "boolean") || ["choose", "if", "repeat", "parallel", "sequence", "condition"].some(key => key in step)) {
      dynamic = true;
      break;
    }
    if ("set_conversation_response" in step) response = step.set_conversation_response;
    if ("stop" in step) {
      stopped = true;
      failed = step.error === true;
      break;
    }
  }
  if (dynamic) return `${prefix} · response depends on local actions${action.continue_to_ai ? " · may continue to AI unless actions respond, stop, or fail" : " without an AI request"}`;
  if (failed) response = action.failure_response;
  else if (response === undefined || response === null) {
    if (action.continue_to_ai && !stopped) return `${prefix} then continues to AI`;
    response = action.success_response;
  }
  const text = String(response ?? "");
  const reply = /\{[{%#]/.test(text) ? " · replies using a conversation response template" : ` · replies “${text}”`;
  return `${prefix} without an AI request${failed ? " · aborts" : stopped ? " · stops" : ""}${reply}`;
}
