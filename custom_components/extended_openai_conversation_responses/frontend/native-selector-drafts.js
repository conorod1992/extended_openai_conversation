// Native YAML editors keep invalid text separately from their selector value.
// Observe the editors themselves: their events need not cross shadow boundaries.
export function watchSelectorYaml(selector) {
  if (!globalThis.MutationObserver) return;
  const records = new Map(), roots = new Set(), updates = new WeakSet();
  let stopped = false;
  const owns = element => {
    // parentNode stops at a ShadowRoot. Follow its host as well; isConnected
    // would wrongly discard drafts when HA detaches the entire panel.
    for (let node = element; node; node = node.parentNode || (node.nodeType === 11 ? node.host : null)) {
      if (node === selector) return true;
    }
    return false;
  };
  const scan = node => {
    if (!node || stopped || !owns(node)) return;
    if (node.shadowRoot) scan(node.shadowRoot);
    if (!roots.has(node)) { roots.add(node); observer.observe(node, {childList:true, subtree:true}); }
    for (const element of node.querySelectorAll?.("*") || []) {
      if (element.updateComplete?.then && !updates.has(element)) {
        updates.add(element);
        void element.updateComplete.then(() => scan(selector)).catch(() => {});
      }
      if (element.localName === "ha-yaml-editor" && !records.has(element)) {
        const record = {edited:element.isValid === false, raw:String(element.yaml ?? ""), valid:element.isValid !== false, pending:false};
        records.set(element, record);
        const onChange = event => {
          record.edited = true;
          record.raw = String(element.yaml ?? "");
          record.valid = event.detail?.isValid === true;
          record.pending = record.valid;
        };
        // Text changes may precede the YAML parser's debounced value event.
        const onInput = () => {
          record.edited = true;
          record.raw = String(element.yaml ?? "");
          record.pending = true;
        };
        element.addEventListener("value-changed", onChange, true);
        element.addEventListener("input", onInput, true);
        record.stop = () => {
          element.removeEventListener("value-changed", onChange, true);
          element.removeEventListener("input", onInput, true);
        };
      }
      if (element.shadowRoot) scan(element.shadowRoot);
    }
  };
  const observer = new MutationObserver(() => refresh());
  const refresh = () => {
    if (stopped) return;
    for (const [element, record] of records) if (!owns(element)) {
      record.stop();
      records.delete(element);
    }
    // Release observers on removed action/condition trees too.
    if ([...roots].some(root => !owns(root))) { observer.disconnect(); roots.clear(); }
    scan(selector);
  };
  selector.addEventListener("value-changed", event => {
    if (event.composedPath()[0] !== selector) return;
    for (const record of records.values()) if (record.valid) record.pending = false;
  });
  selector.addEventListener("value-changed", () => scan(selector), true);
  selector.addEventListener("input", () => scan(selector), true);
  queueMicrotask(() => scan(selector));
  scan(selector);
  selector.__eocYamlDraft = () => {
    refresh();
    return [...records].filter(([, record]) => record.edited).map(([element, record]) => ({
      raw:String(element.yaml ?? record.raw), valid:record.valid, pending:record.pending,
    }));
  };
  selector.__eocStopYamlDraft = () => {
    stopped = true;
    observer.disconnect();
    roots.clear();
    for (const record of records.values()) record.stop();
    records.clear();
  };
}

export function assertSelectorYamlReady(selector) {
  if (selector?.__eocYamlDraft?.().some(draft => !draft.valid || draft.pending)) {
    throw new Error("Correct the YAML errors and wait for the editor to apply your changes before saving.");
  }
}
