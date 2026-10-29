export function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

// A synchronous HA editor double must include the editable document, not just
// record setValue calls. Production waits for the outer and child Lit updates.
export function completeNativeDocument(editor, value) {
  editor.yaml = JSON.stringify(value);
  const yaml = editor.yaml;
  const code = {updateComplete:Promise.resolve(true), codemirror:{state:{doc:{toString:() => yaml}}}};
  editor.updateComplete = Promise.resolve(true);
  editor.shadowRoot = {querySelector:selector => selector === "ha-code-editor" ? code : null};
}

export class TestEventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  dispatchEvent(event) {
    for (const listener of this.listeners.get(event.type) || []) listener.call(this, event);
    return !event.defaultPrevented;
  }
  emit(type) {
    return Promise.all([...this.listeners.get(type) || []].map((listener) => listener.call(this, {type, target:this})));
  }
}
