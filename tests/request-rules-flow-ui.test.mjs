import assert from "node:assert/strict";
import test from "node:test";

import {
  applySentencePatternHelper,
  requestRulesDialog,
} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";

test("Request Rule dialog exposes explicit Continue to AI control", () => {
  const html = requestRulesDialog();
  assert.match(html, /id="rule-continue-to-ai"/);
  assert.match(html, /After applying these routing settings, send the original request/);
});

test("sentence-pattern helper controls are present", () => {
  const html = requestRulesDialog();
  for (const helper of ["optional", "choice", "variable", "range"]) {
    assert.match(html, new RegExp(`data-pattern-helper="${helper}"`));
  }
});

test("sentence-pattern helpers insert editable raw syntax", () => {
  assert.deepEqual(applySentencePatternHelper("turn light on", 5, 10, "optional"), {
    value: "turn [light] on",
    selectionStart: 6,
    selectionEnd: 11,
  });
  assert.deepEqual(applySentencePatternHelper("set ", 4, 4, "variable"), {
    value: "set {name}",
    selectionStart: 5,
    selectionEnd: 9,
  });
  assert.deepEqual(applySentencePatternHelper("set ", 4, 4, "range"), {
    value: "set {level=0..100}",
    selectionStart: 5,
    selectionEnd: 10,
  });
  assert.equal(applySentencePatternHelper("", 0, 0, "choice").value, "(one|two)");
});

test("sentence-pattern helper rejects unknown helper types", () => {
  assert.throws(() => applySentencePatternHelper("", 0, 0, "unknown"), /Unknown sentence-pattern helper/);
});
