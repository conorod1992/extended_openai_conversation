import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const source = await readFile(frontend("management-loading-performance.js"), "utf8");
const module = await import(frontend("management-loading-performance.js"));

assert.equal(module.fieldErrorKey("title"), "__title");
assert.equal(module.fieldErrorKey("chat_model"), "chat_model");

const localDateTime = "2026-09-02T19:15";
assert.equal(
  module.normalizeGuestModeTimestamp(localDateTime),
  new Date(localDateTime).toISOString(),
);
assert.equal(
  module.normalizeGuestModeTimestamp("2026-09-02T19:15:00+01:00"),
  "2026-09-02T19:15:00+01:00",
);
assert.equal(
  module.normalizeGuestModeTimestamp("2026-09-02T18:15:00Z"),
  "2026-09-02T18:15:00Z",
);

assert.equal(module.validatedImportMatches("document-a", "document-a"), true);
assert.equal(module.validatedImportMatches("document-a", "document-b"), false);
assert.equal(module.validatedImportMatches(null, "document-a"), false);

let resolveMutation;
let calls = 0;
const control = {
  tagName: "INPUT",
  disabled: false,
  dataset: {},
};
const toasts = [];
const panel = {
  _toast: (...args) => toasts.push(args),
};
const first = module.runFrontendMutation(panel, control, "save settings", () => {
  calls += 1;
  return new Promise((resolve) => { resolveMutation = resolve; });
});
assert.equal(control.disabled, true);
assert.equal(control.dataset.eocMutationPending, "true");
const duplicate = await module.runFrontendMutation(
  panel,
  control,
  "save settings",
  async () => { calls += 1; },
);
assert.equal(duplicate, false);
assert.equal(calls, 1);
resolveMutation();
assert.equal(await first, true);
assert.equal(control.disabled, false);
assert.equal(control.dataset.eocMutationPending, undefined);
assert.deepEqual(toasts, []);

const failedControl = {
  tagName: "INPUT",
  disabled: false,
  dataset: {},
};
const failed = await module.runFrontendMutation(
  panel,
  failedControl,
  "save settings",
  async () => { throw new Error("network down"); },
);
assert.equal(failed, false);
assert.equal(failedControl.disabled, false);
assert.deepEqual(toasts.at(-1), ["Unable to save settings: network down", true]);

assert.match(source, /Document changed\. Validate & preview again before importing\./);
assert.match(source, /validatedImportMatches\(panel\._importDocument, current\)/);
assert.match(source, /section === "guest_mode" && action === "update"/);
assert.match(source, /button\.id === "guest-policy-save"/);
assert.match(source, /button\.classList\.contains\("rule-duplicate"\)/);
assert.match(source, /button\.classList\.contains\("rule-delete"\)/);
assert.match(source, /input\?\.classList\?\.contains\("rule-enabled"\)/);
assert.match(source, /this\._eocRuleSavePromise/);
