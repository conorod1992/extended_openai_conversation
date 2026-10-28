import {captureConfigurationOwner, requireMutationAcknowledgement} from "./configuration-acknowledgement.js";

// Uncertain commits belong to an assistant and authentication session, not a view.
const uncertainRuleMutations = new WeakMap();
const ruleOwnerKey = owner => JSON.stringify([owner.entryId, owner.agentId, owner.userId, owner.authenticationGeneration]);
export function requestRuleUncertainty(panel, owner = captureConfigurationOwner(panel)) {
  return uncertainRuleMutations.get(panel)?.get(ruleOwnerKey(owner));
}
export function requestRuleMutationNeedsRecovery(panel, owner = captureConfigurationOwner(panel)) {
  return Boolean(requestRuleUncertainty(panel, owner));
}
export function markUncertainRuleMutation(panel, owner, error) {
  if (error?.mutationOutcome !== "uncertain") return;
  let pending = uncertainRuleMutations.get(panel);
  if (!pending) uncertainRuleMutations.set(panel, pending = new Map());
  pending.set(ruleOwnerKey(owner), error);
}
export function requireRequestRuleAcknowledgement(action, result, ruleId) {
  const identity = result?.rule?.id;
  requireMutationAcknowledgement(result?.revision != null
    && (!["create", "duplicate", "update"].includes(action)
      || (typeof identity === "string" && identity.length > 0 && (action !== "update" || identity === ruleId) && (action !== "duplicate" || identity !== ruleId)))
    && (action !== "groups" || (Array.isArray(result.groups) && Array.isArray(result.rules))), "saving Request Rules");
}

export function clearRequestRuleUncertainty(panel, owner, expected) {
  if (requestRuleUncertainty(panel, owner) === expected) uncertainRuleMutations.get(panel)?.delete(ruleOwnerKey(owner));
}
