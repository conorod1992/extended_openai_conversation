// Native YAML editors keep invalid text separately from their selector value.
// Observe the editors themselves: their events need not cross shadow boundaries.
export function watchSelectorYaml(selector) {
  if (!globalThis.MutationObserver) return;
  const records = new Map(), roots = new Set(), updates = new WeakSet();
  let stopped = false;
  const scan = node => {
    if (!node || stopped) return;
    if (node.shadowRoot) scan(node.shadowRoot);
    if (!roots.has(node)) { roots.add(node); observer.observe(node, {childList:true, subtree:true}); }
    for (const element of node.querySelectorAll?.("*") || []) {
      if (element.updateComplete?.then && !updates.has(element)) {
        updates.add(element);
        void element.updateComplete.then(() => scan(selector)).catch(() => {});
      }
      if (element.localName === "ha-yaml-editor" && !records.has(element)) {
        const record = {edited:false, raw:"", valid:true, pending:false};
        records.set(element, record);
        element.addEventListener("value-changed", event => {
          record.edited = true;
          record.raw = String(element.yaml ?? "");
          record.valid = event.detail?.isValid === true;
          record.pending = record.valid;
        }, true);
        // Text changes may precede the YAML parser's debounced value event.
        element.addEventListener("input", () => {
          record.edited = true;
          record.raw = String(element.yaml ?? "");
          record.pending = true;
        }, true);
      }
      if (element.shadowRoot) scan(element.shadowRoot);
    }
  };
  const observer = new MutationObserver(() => scan(selector));
  selector.addEventListener("value-changed", event => {
    if (event.composedPath()[0] !== selector) return;
    for (const record of records.values()) if (record.valid) record.pending = false;
  });
  selector.addEventListener("value-changed", () => scan(selector), true);
  selector.addEventListener("input", () => scan(selector), true);
  queueMicrotask(() => scan(selector));
  scan(selector);
  selector.__eocYamlDraft = () => {
    scan(selector);
    return [...records].filter(([, record]) => record.edited).map(([element, record]) => ({
      raw:String(element.yaml ?? record.raw), valid:record.valid, pending:record.pending,
    }));
  };
  selector.__eocStopYamlDraft = () => { stopped = true; observer.disconnect(); };
}

export function assertSelectorYamlReady(selector) {
  if (selector?.__eocYamlDraft?.().some(draft => !draft.valid || draft.pending)) {
    throw new Error("Correct the YAML errors and wait for the editor to apply your changes before saving.");
  }
}
