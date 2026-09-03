import assert from "node:assert/strict";

import {
  featureStatusMarkup,
  overviewAgentFeatureProjection,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-feature-status.js";

const panel = {
  _data: {is_admin: true},
  _e: (value) => String(value),
};

const markup = featureStatusMarkup(panel, "Persistent memory", {
  state: "enabled",
  label: "Manual",
  detail: "Automatic inclusion is off; memory search tools remain available.",
});
assert.match(markup, /Persistent memory/);
assert.match(markup, /Manual/);
assert.match(markup, /memory search tools remain available/);
assert.match(markup, /data-subsection="advanced"/);
assert.match(markup, /status-value on/);

const disabledMarkup = featureStatusMarkup(panel, "Knowledge Library", {
  state: "empty",
  label: "Needs sources",
  detail: "Knowledge tools remain unavailable until at least one source exists.",
});
assert.match(disabledMarkup, /Needs sources/);
assert.doesNotMatch(disabledMarkup, /status-value on/);

const projected = overviewAgentFeatureProjection({
  memory_mode: "automatic",
  feature_status: {
    memory: {label: "Automatic"},
    knowledge: {label: "Available"},
  },
});
assert.equal(projected.memory_mode, "Automatic · Knowledge Available");

const original = {memory_mode: "manual"};
assert.equal(overviewAgentFeatureProjection(original), original);
