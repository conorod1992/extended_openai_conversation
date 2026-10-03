// Each surface owns only its latest operation, including failed completions.
const operations = new WeakMap();
export function beginOperation(host, surface) {
  let slots = operations.get(host);
  if (!slots) operations.set(host, slots = new Map());
  const token = {};
  slots.set(surface, token);
  return () => slots.get(surface) === token;
}
export function invalidateOperation(host, surface) {
  operations.get(host)?.delete(surface);
}
