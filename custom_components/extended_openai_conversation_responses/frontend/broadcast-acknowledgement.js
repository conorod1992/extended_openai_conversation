// @ts-check
import {captureConfigurationOwner, processMutationResponse, requireMutationAcknowledgement} from "./configuration-acknowledgement.js";

/** Queue admission is independent of per-recipient delivery state.
 * @typedef {{id: string, targets: string[]}} BroadcastAdmission
 */
/** @typedef {{userId?: string, authenticationGeneration: number}} BroadcastOwner */
/** @typedef {{hass?: {user?: {id?: string}}, _eocAuthenticationGeneration?: number,
 * _broadcastDraftRevision?: number, _broadcastMessage?: string,
 * shadowRoot: {querySelector(selector: string): {value: string} | null}}} BroadcastComposer
 */

/** @param {unknown} result @returns {asserts result is BroadcastAdmission} */
function assertAdmission(result) {
  const value = result && typeof result === "object" ? result : null;
  requireMutationAcknowledgement(value !== null && "id" in value
    && typeof value.id === "string" && !!value.id.trim()
    && "targets" in value && Array.isArray(value.targets) && value.targets.length > 0
    && value.targets.every((/** @type {unknown} */ target) => typeof target === "string" && !!target.trim()), "queuing Broadcast");
}

/** Validate only admission evidence, allowing additional response fields.
 * Return the original object so response-processing errors keep their owner.
 * @param {unknown} result @returns {BroadcastAdmission}
 */
export function requireBroadcastAdmission(result) {
  assertAdmission(result);
  return result;
}

/** Adopt committed intent before fallible presentation. The composer is global
 * across assistants; its authentication lifetime and draft revision own edits.
 * @param {BroadcastComposer} panel @param {BroadcastOwner} submission
 * @param {string} message @param {number} draftRevision @param {unknown} response
 * @returns {BroadcastAdmission}
 */
export function acknowledgeBroadcast(panel, submission, message, draftRevision, response) {
  const admission = requireBroadcastAdmission(response);
  const current = captureConfigurationOwner(panel);
  if (current.userId === submission.userId && current.authenticationGeneration === submission.authenticationGeneration
      && (panel._broadcastDraftRevision || 0) === draftRevision) {
    panel._broadcastMessage = "";
    processMutationResponse(admission, () => {
      const composer = panel.shadowRoot.querySelector("#broadcast-message");
      if (composer && composer.value.trim() === message) composer.value = "";
    });
  }
  return admission;
}
