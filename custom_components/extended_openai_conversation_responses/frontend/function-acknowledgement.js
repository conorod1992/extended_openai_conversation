// @ts-check
/** @typedef {import("./request-rule-acknowledgement.js").RuleOwner} FunctionOwner */
/** @typedef {{_eocFunctionMutationEpoch?: number, _eocFunctionMutationTail?: unknown,
 * _invalidateCleanConfiguration?: (agentId?: string) => void, _configDataStale?: boolean}} FunctionPanel */
import {captureConfigurationOwner, configurationSubmissionIsCurrent, requireMutationAcknowledgement} from "./configuration-acknowledgement.js";
/** @type {WeakMap<object, Map<string, unknown>>} */
const pending = new WeakMap();
/** @param {FunctionOwner} owner */
const key = owner => JSON.stringify([owner.entryId, owner.agentId, owner.userId, owner.authenticationGeneration]);
/** @param {object} panel @param {FunctionOwner} owner */
export const functionUncertainty = (panel, owner = captureConfigurationOwner(panel)) => pending.get(panel)?.get(key(owner));
/** @param {{valid?: boolean, revision?: unknown, functions?: unknown, function_groups?: unknown} | null | undefined} result */
export function requireFunctionAcknowledgement(result) {
  if (result?.valid === false) return;
  requireMutationAcknowledgement(typeof result?.revision === "string" && result.revision.length > 0
    && Array.isArray(result.functions) && Array.isArray(result.function_groups), "saving Function configuration");
}
/** @param {FunctionPanel} panel @param {FunctionOwner} owner @param {{mutationOutcome?: unknown} | null | undefined} error */
export function markFunctionUncertainty(panel, owner, error) {
  if (error?.mutationOutcome !== "uncertain") return;
  let owners = pending.get(panel);
  if (!owners) pending.set(panel, owners = new Map());
  owners.set(key(owner), error);
  if (configurationSubmissionIsCurrent(panel, owner)) {
    panel._invalidateCleanConfiguration?.(owner.agentId);
    panel._configDataStale = true;
  }
}
/** @param {FunctionPanel} panel @param {FunctionOwner} owner @param {unknown} expected @param {number} epoch
 * @param {{revision?: unknown, config?: {functions?: unknown, function_groups?: unknown}} | null | undefined} data */
export function settleFunctionRead(panel, owner, expected, epoch, data) {
  if (!expected || functionUncertainty(panel, owner) !== expected
      || epoch !== (panel._eocFunctionMutationEpoch || 0) || panel._eocFunctionMutationTail
      || typeof data?.revision !== "string" || !data.revision
      || !Array.isArray(data?.config?.functions) || !Array.isArray(data?.config?.function_groups)) return;
  pending.get(panel)?.delete(key(owner));
}
