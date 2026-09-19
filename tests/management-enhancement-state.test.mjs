import assert from "node:assert/strict";
import {enhancementChanged} from "../custom_components/extended_openai_conversation_responses/frontend/management-enhancement-state.js";

const panel = {shadowRoot:{}, _eocShellRevision:1, _eocMainRevision:1};
assert.equal(enhancementChanged(panel, "layout"), true);
assert.equal(enhancementChanged(panel, "layout"), false);
assert.equal(enhancementChanged(panel, "guidance", [false]), true);
assert.equal(enhancementChanged(panel, "guidance", [false]), false);
assert.equal(enhancementChanged(panel, "guidance", [true]), true);
assert.equal(enhancementChanged(panel, "layout"), false);
panel._eocMainRevision++;
assert.equal(enhancementChanged(panel, "layout"), true);
assert.equal(enhancementChanged(panel, "guidance", [true]), true);
panel._eocShellRevision++;
assert.equal(enhancementChanged(panel, "layout"), true);
panel.shadowRoot = {};
assert.equal(enhancementChanged(panel, "layout"), true);
assert.equal(enhancementChanged({...panel}, "layout"), true);
