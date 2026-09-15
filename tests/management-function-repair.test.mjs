import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  editableToolsText,
  renderFunctionRepair,
  repairIssue,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js";

const bootstrap = await readFile(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js",
    import.meta.url,
  ),
  "utf8",
);
assert.match(bootstrap, /management-function-repair\.js/);
assert.ok(
  bootstrap.indexOf('await import("./management-loading-performance.js")')
    < bootstrap.indexOf('await import("./management-function-repair.js")'),
  "Function repair should wrap the final optimized section loader",
);

const issue = {
  field: "functions",
  message: "parameters.phone: unsupported keyword: enumNames",
  repairable: true,
};
const panel = {
  _data: {is_admin: true},
  _selectedAgent: () => ({configuration_issue: issue}),
  _result: {
    tools: [{spec: {name: "legacy_tool"}}],
    validation_error: '<bad schema & "unsafe">',
  },
};

assert.deepEqual(repairIssue(panel), issue);
assert.equal(
  repairIssue({
    _selectedAgent: () => ({
      configuration_issue: {...issue, field: "prompt"},
    }),
  }),
  null,
);
assert.equal(
  editableToolsText([{spec: {name: "legacy_tool"}}]),
  '[\n  {\n    "spec": {\n      "name": "legacy_tool"\n    }\n  }\n]',
);
assert.equal(editableToolsText("raw: persisted yaml"), "raw: persisted yaml");

const adminHtml = renderFunctionRepair(panel, issue);
assert.match(adminHtml, /Function Tools need repair/);
assert.match(adminHtml, /id="function-repair-editor"/);
assert.match(adminHtml, /id="function-repair-save"/);
assert.match(adminHtml, /changes only the Function Tools field/);
assert.doesNotMatch(adminHtml, /<bad schema/);
assert.match(adminHtml, /&lt;bad schema &amp; &quot;unsafe&quot;&gt;/);

const nonAdminHtml = renderFunctionRepair(
  {
    _data: {is_admin: false},
    _result: {administrator_required: true, validation_error: issue.message},
  },
  issue,
);
assert.match(nonAdminHtml, /Administrator permission is required/);
assert.doesNotMatch(nonAdminHtml, /function-repair-editor/);
assert.doesNotMatch(nonAdminHtml, /function-repair-save/);
