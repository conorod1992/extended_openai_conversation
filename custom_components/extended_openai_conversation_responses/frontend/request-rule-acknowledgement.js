// @ts-check
/** @typedef {{entryId?: string, agentId?: string, userId?: string, authenticationGeneration?: number}} RuleOwner */
/** @typedef {{revision?: string | number, rule?: {id?: string}, groups?: unknown[], rules?: unknown[], defaults?: Record<string, unknown>, wording_groups?: unknown[]}} RuleAcknowledgement */
import {captureConfigurationOwner, requireMutationAcknowledgement} from "./configuration-acknowledgement.js";

// Uncertain commits belong to an assistant and authentication session, not a view.
/** @type {WeakMap<object, Map<string, unknown>>} */
const uncertainRuleMutations = new WeakMap();
/** @param {RuleOwner} owner */
const ruleOwnerKey = owner => JSON.stringify([owner.entryId, owner.agentId, owner.userId, owner.authenticationGeneration]);
/** @param {object} panel @param {RuleOwner} owner */
export function requestRuleUncertainty(panel, owner = captureConfigurationOwner(panel)) {
  return uncertainRuleMutations.get(panel)?.get(ruleOwnerKey(owner));
}
/** @param {object} panel @param {RuleOwner} owner */
export function requestRuleMutationNeedsRecovery(panel, owner = captureConfigurationOwner(panel)) {
  return Boolean(requestRuleUncertainty(panel, owner));
}
/** @param {object} panel @param {RuleOwner} owner @param {{mutationOutcome?: unknown} | null | undefined} error */
export function markUncertainRuleMutation(panel, owner, error) {
  if (error?.mutationOutcome !== "uncertain") return;
  let pending = uncertainRuleMutations.get(panel);
  if (!pending) uncertainRuleMutations.set(panel, pending = new Map());
  pending.set(ruleOwnerKey(owner), error);
}
/** @param {string} action @param {RuleAcknowledgement | null | undefined} result @param {string | undefined} ruleId */
export function requireRequestRuleAcknowledgement(action, result, ruleId) {
  const identity = result?.rule?.id;
  const hasDefaults = !!result?.defaults && typeof result.defaults === "object" && !Array.isArray(result.defaults);
  requireMutationAcknowledgement(result?.revision != null
    && (!["create", "duplicate", "update"].includes(action)
      || (typeof identity === "string" && identity.length > 0 && (action !== "update" || identity === ruleId) && (action !== "duplicate" || identity !== ruleId)))
    && (!["groups", "rule_pack_import"].includes(action) || (Array.isArray(result.groups) && Array.isArray(result.rules)))
    && (!["settings", "defaults"].includes(action) || hasDefaults)
    && (!["settings", "wording_groups"].includes(action) || Array.isArray(result.wording_groups)), "saving Request Rules");
}

/** @param {object} panel @param {RuleOwner} owner @param {unknown} expected */
export function clearRequestRuleUncertainty(panel, owner, expected) {
  if (requestRuleUncertainty(panel, owner) === expected) uncertainRuleMutations.get(panel)?.delete(ruleOwnerKey(owner));
}
