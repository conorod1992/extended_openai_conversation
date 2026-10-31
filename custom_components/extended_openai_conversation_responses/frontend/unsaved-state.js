// A scope owns its baseline, not the current DOM or the lifetime of a route render.
export const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
const canonical = (value) => Array.isArray(value) ? value.map(canonical)
  : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value;
export const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));

// Adopt acknowledged values only where the draft still matches the submission.
// Key presence matters: a removed field is different from an explicit null.
export function mergeAcknowledgedDraft(current, submitted, saved, fields) {
  if (![current, submitted, saved].every(value => value && typeof value === "object" && !Array.isArray(value))) {
    return same(current, submitted) ? clone(saved) : current;
  }
  const result = clone(current);
  const keys = fields || new Set([...Object.keys(submitted), ...Object.keys(saved)]);
  for (const key of keys) {
    if (Object.hasOwn(current, key) !== Object.hasOwn(submitted, key) || !same(current[key], submitted[key])) continue;
    if (Object.hasOwn(saved, key)) result[key] = clone(saved[key]);
    else delete result[key];
  }
  return result;
}

export class UnsavedState {
  constructor() { this.scopes = new Map(); }
  register(id, scope) { this.scopes.set(id, scope); return scope; }
  dirty() { return [...this.scopes.values()].filter((scope) => scope.dirty()); }
  hasChanges() { return this.dirty().length > 0; }
  leaving(destination) { return [...this.scopes.values()].filter((scope) => (scope.dirty() || scope.pending) && !scope.owns?.(destination)); }
  destinations() { return new Set(this.dirty().flatMap((scope) => scope.destinations?.() || [])); }
}

export function draftScope({read, write, baseline, save, owns, destinations, isCurrent, captureSaveGuard}) {
  return {
    baseline: clone(baseline), pending: false, read, write, owns, destinations,
    dirty() { return !same(this.read(), this.baseline); },
    discard() { this.write(clone(this.baseline)); },
    async save() {
      if (this.pending) return false;
      if (isCurrent && !isCurrent()) throw new Error("The policy editor owner changed. Reopen the current editor before saving.");
      const saveIsCurrent = captureSaveGuard?.();
      this.pending = true;
      const submitted = clone(this.read());
      try {
        const saved = await save(submitted, this);
        this.baseline = clone(saved);
        // Edits made during an in-flight save belong to the next save.
        if (!saveIsCurrent || saveIsCurrent()) this.write(mergeAcknowledgedDraft(this.read(), submitted, saved));
        return true;
      } finally { this.pending = false; }
    },
  };
}

export function saveBarMarkup({pending = false, configuration = false} = {}) {
  return `<div class="save-bar"><div class="setting-copy"><strong id="dirty-state" class="dirty-state">Unsaved changes</strong>${configuration ? '<small class="help">Save and discard apply to this assistant&#39;s configuration draft, including edits in other sections.</small>' : ""}</div><div class="actions"><button type="button" class="secondary" id="${configuration ? "revert-config" : "discard-page"}" ${pending ? "disabled" : ""}>Discard changes</button><button type="button" id="${configuration ? "save-config" : "save-page"}" data-label="Save changes" ${pending ? "disabled" : ""}>${pending ? "Saving…" : "Save changes"}</button></div></div>`;
}
