// Browser storage is optional and can throw in private or quota-limited contexts.

export function readOptionalStorage(key) {
  try {
    return globalThis.localStorage?.getItem?.(key) ?? null;
  } catch (_error) {
    return null;
  }
}

export function writeOptionalStorage(key, value) {
  try {
    globalThis.localStorage?.setItem?.(key, String(value));
    return true;
  } catch (_error) {
    return false;
  }
}
