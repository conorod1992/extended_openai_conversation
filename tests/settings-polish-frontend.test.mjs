import assert from "node:assert/strict";
import {modelDataControls} from "../custom_components/extended_openai_conversation_responses/frontend/model-catalog.js";
import {settingBadgesMarkup} from "../custom_components/extended_openai_conversation_responses/frontend/management-decision-guidance.js";
import {renderConfiguration} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js";
import {renderMemorySettings} from "../custom_components/extended_openai_conversation_responses/frontend/memory-settings-ui.js";

// Pure rendering must not need a document, observer or scheduled enhancement.
const panel = {
  _e: (value) => String(value ?? ""), _titleCase: String,
  _result: {config:{api_mode:"auto",conversation_continuity:"ha_default",memory_retrieval_mode:"hybrid"},defaults:{api_mode:"auto",conversation_continuity:"ha_default"},options:{api_mode:[{value:"auto",label:"Auto"}],memory_retrieval_mode:[{value:"hybrid",label:"Hybrid"}]}},
  _configSections:["general","conversation"],
};
const config = renderConfiguration(panel);
assert.match(config, /<h2 class="eyebrow">General<\/h2>/);
assert.doesNotMatch(config, /<section class="page-intro"><h1>General<\/h1>/);
assert.match(config, /Auto is recommended unless your provider requires a specific API/);
assert.doesNotMatch(config, /Recommended default: Automatic \(Auto\)/);
assert.doesNotMatch(config, /eoc-decision-badge default[^>]*>Default: Automatic/);
assert.match(config, /Uses Home Assistant sessions/);
assert.doesNotMatch(config, /Default: Ha Default/);
const memory = renderMemorySettings(panel);
assert.match(memory, /Relevance matching/);
assert.match(memory, /Semantic \+ keyword matching \(Hybrid\)/);
assert.match(memory, /Requires embeddings/);
for (const key of ["temperature", "archive_enabled", "temporary_memory", "current_datetime_enabled"]) {
  assert.doesNotMatch(settingBadgesMarkup(panel, key, true), /eoc-effect-badge/);
}
const empty = modelDataControls(panel);
assert.match(empty, /data-model-data="apply" hidden disabled/);
assert.match(empty, /eoc-model-data-action" hidden/);
panel._modelCatalogData = {catalog_version:2,available_catalog_version:3,update_available:true};
const available = modelDataControls(panel);
assert.match(available, /Apply v3 update/);
assert.doesNotMatch(available, /hidden|disabled/);
assert.match(available, /Update available: v2 → v3/);
assert.match(available, /Model capability data/);
assert.match(available, /Future checks will not replace it automatically/);
