import assert from "node:assert/strict";
import test from "node:test";
import {showRecoveredDraftCopy} from "../custom_components/extended_openai_conversation_responses/frontend/management-state-safety.js";

function fixture() {
  const previous = globalThis.document;
  const created = [];
  globalThis.document = {createElement(tagName) {
    const node = {tagName, children:[], dataset:{}, attributes:new Map(), listeners:new Map(),
      append(...children) { this.children.push(...children); },
      setAttribute(key, value) { this.attributes.set(key, value); },
      addEventListener(type, listener) { this.listeners.set(type, listener); },
      showModal() { this.open = true; },
      close() {
        if (!this.open) return;
        this.open = false;
        this.listeners.get("close")?.();
      },
      remove() { this.removed = true; },
    };
    created.push(node);
    return node;
  }};
  return {panel:{shadowRoot:{append(node) { this.dialog = node; }}}, created,
    restore() {
      if (previous === undefined) delete globalThis.document;
      else globalThis.document = previous;
    },
  };
}

test("recovered configuration copies render exact private data as read-only text", () => {
  const h = fixture();
  try {
    const values = {title:"<img onerror='bad()'>", config:{prompt:"Private <script>text</script>", functions:[]}};
    const dialog = showRecoveredDraftCopy(h.panel, {title:"Recovered configuration", description:"Review both drafts.", values, label:"Recovered configuration values"});
    const [heading, description, text] = dialog.children;
    assert.equal(dialog.open, true);
    assert.equal(dialog.dataset.eocRecoveredEditor, "");
    assert.equal(heading.textContent, "Recovered configuration");
    assert.equal(description.textContent, "Review both drafts.");
    assert.equal(text.value, JSON.stringify(values, null, 2));
    assert.equal(text.readOnly, true);
    assert.equal(text.attributes.get("aria-label"), "Recovered configuration values");
    assert.equal(h.created.some(node => Object.hasOwn(node, "innerHTML")), false);
  } finally { h.restore(); }
});

for (const allowed of [false, true]) test(`recovered Discard obeys its current ownership guard: ${allowed}`, () => {
  const h = fixture();
  try {
    let current = false, discards = 0;
    const dialog = showRecoveredDraftCopy(h.panel, {values:{prompt:"retained"}, canDiscard:() => current, onDiscard:() => {discards++;}});
    current = allowed;
    const [, , , keep, discard] = dialog.children;
    discard.onclick();
    assert.equal(discards, Number(allowed));
    assert.equal(dialog.open, false);
    assert.equal(dialog.removed, true);
    assert.equal(keep.onclick, null);
    assert.equal(discard.onclick, null);
  } finally { h.restore(); }
});

test("keeping or navigating away from a copy never discards the source", () => {
  const h = fixture();
  try {
    let discards = 0;
    const dialog = showRecoveredDraftCopy(h.panel, {values:[{value:"unsaved"}], canDiscard:() => true, onDiscard:() => {discards++;}});
    const [, , text, keep, discard] = dialog.children;
    const retiredDiscard = discard.onclick;
    assert.equal(text.attributes.get("aria-label"), "Recovered editor values");
    keep.onclick();
    retiredDiscard();
    assert.equal(discards, 0);
    assert.equal(dialog.removed, true);
    assert.equal(keep.onclick, null);
    assert.equal(discard.onclick, null);
  } finally { h.restore(); }
});

test("recovered copies default to requiring an explicit discard policy", () => {
  const h = fixture();
  try {
    let discards = 0;
    const dialog = showRecoveredDraftCopy(h.panel, {values:{prompt:"unsaved"}, onDiscard:() => {discards++;}});
    assert.equal(dialog.children[0].textContent, "Recovered unsaved editor");
    dialog.children[4].onclick();
    assert.equal(discards, 0);
    assert.equal(dialog.open, false);
  } finally { h.restore(); }
});
