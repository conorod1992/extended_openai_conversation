// A scope owns its baseline, not the current DOM or the lifetime of a route render.
export const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
const canonical = (value) => Array.isArray(value) ? value.map(canonical)
  : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value;
export const same = (left, right) => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));

export class UnsavedState {
  constructor() { this.scopes = new Map(); }
  register(id, scope) { this.scopes.set(id, scope); return scope; }
  dirty() { return [...this.scopes.values()].filter((scope) => scope.dirty()); }
  hasChanges() { return this.dirty().length > 0; }
  leaving(destination) { return this.dirty().filter((scope) => !scope.owns?.(destination)); }
  destinations() { return new Set(this.dirty().flatMap((scope) => scope.destinations?.() || [])); }
}

export function draftScope({read, write, baseline, save, owns, destinations}) {
  return {
    baseline: clone(baseline), pending: false, read, write, owns, destinations,
    dirty() { return !same(this.read(), this.baseline); },
    discard() { this.write(clone(this.baseline)); },
    async save() {
      if (this.pending) return false;
      this.pending = true;
      const submitted = clone(this.read());
      try {
        const saved = await save(submitted, this);
        this.baseline = clone(saved);
        // Edits made during an in-flight save belong to the next save.
        if (same(this.read(), submitted)) this.write(clone(saved));
        return true;
      } finally { this.pending = false; }
    },
  };
}

export function saveBarMarkup({pending = false, configuration = false} = {}) {
  return `<div class="save-bar"><strong id="dirty-state" class="dirty-state">Unsaved changes</strong><div class="actions"><button type="button" class="secondary" id="${configuration ? "revert-config" : "discard-page"}" ${pending ? "disabled" : ""}>Discard changes</button><button type="button" id="${configuration ? "save-config" : "save-page"}" ${pending ? "disabled" : ""}>${pending ? "Saving…" : "Save changes"}</button></div></div>`;
}
