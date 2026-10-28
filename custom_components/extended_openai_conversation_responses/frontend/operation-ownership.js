// @ts-check
// Each surface owns only its latest operation, including failed completions.
/** @type {WeakMap<object, Map<string, object>>} */
const operations = new WeakMap();
/** @param {object} host @param {string} surface @returns {() => boolean} */
export function beginOperation(host, surface) {
  let slots = operations.get(host);
  if (!slots) operations.set(host, slots = new Map());
  const token = {};
  slots.set(surface, token);
  return () => slots.get(surface) === token;
}
/** @param {object} host @param {string} surface */
export function invalidateOperation(host, surface) {
  operations.get(host)?.delete(surface);
}
