// @ts-check
/** @type {WeakMap<object, unknown>} */
const responseProcessingErrors = new WeakMap();
/** @param {unknown} value @returns {string} */
function errorMessage(value) {
  if (value && typeof value === "object") {
    if ("message" in value && value.message) return String(value.message);
    if ("error" in value && value.error && typeof value.error === "object" && "message" in value.error && value.error.message) return String(value.error.message);
  }
  return String(value);
}
/** @template T @param {() => Promise<T>} send @returns {Promise<T>} */
export async function callMutation(send) {
  try { return await send(); }
  catch (error) {
    if (error && typeof error === "object" && "code" in error && typeof error.code === "string" && error.code) throw error;
    throw Object.assign(new Error(`${errorMessage(error)}. The mutation outcome is unknown; refresh before retrying.`, {cause:error}), {mutationOutcome:"uncertain"});
  }
}
/** @param {unknown} value @returns {value is PromiseLike<unknown>} */
function thenable(value) {
  return !!value && (typeof value === "object" || typeof value === "function") && "then" in value && typeof value.then === "function";
}
/** @template T @param {T} result @param {() => unknown} process @returns {T | PromiseLike<T>} */
export function processMutationResponse(result, process) {
  /** @param {unknown} error */
  const failed = error => {
    if (result && typeof result === "object") responseProcessingErrors.set(result, error);
    return result;
  };
  try {
    const pending = process();
    if (thenable(pending)) {
      return pending.then(() => result, failed);
    }
  } catch (error) { return failed(error); }
  return result;
}
/** @param {unknown} result @returns {unknown} */
export function mutationResponseError(result) {
  return result && typeof result === "object" ? responseProcessingErrors.get(result) : undefined;
}
/** @param {boolean} confirmed @param {string} operation */
export function requireMutationAcknowledgement(confirmed, operation) {
  if (!confirmed) throw Object.assign(new Error(`The server response did not confirm ${operation}. Refresh before retrying; the outcome is unknown.`), {mutationOutcome:"uncertain"});
}
