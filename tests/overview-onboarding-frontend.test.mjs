import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  buildGettingStarted,
  dismissOnboarding,
  hasEstablishedUsage,
  markOnboardingStepReviewed,
  onboardingStorageKey,
  readOnboardingReviewState,
} from "../custom_components/extended_openai_conversation_responses/frontend/overview-onboarding.js";

const agent = {
  entry_id: "entry-1",
  subentry_id: "agent-1",
  provider: "openai",
  model: "gpt-5-mini",
};
const freshResult = {
  usage: {
    lifetime: {api_request_count: 0, conversation_count: 0},
    latest: null,
  },
  load_errors: [],
};
const baseFacts = {
  provider_runtime: {
    client_loaded: true,
    provider: "openai",
    model: "gpt-5-mini",
    configured_api_mode: "auto",
  },
  prompt_state: "starter",
  exposed_entity_count: 0,
  web_search: {effective_api_mode: "chat_completions"},
};

assert.equal(onboardingStorageKey(agent), "extended-openai-getting-started-v1:entry-1:agent-1");
assert.equal(onboardingStorageKey({}), null);
assert.equal(hasEstablishedUsage(freshResult), false);
assert.equal(hasEstablishedUsage({usage:{lifetime:{api_request_count:1}}}), true);
assert.equal(hasEstablishedUsage({usage:{lifetime:{conversation_count:1}}}), true);
assert.equal(hasEstablishedUsage({usage:{latest:{successful:true}}}), true);

const initial = buildGettingStarted({
  facts: baseFacts,
  agent,
  result: freshResult,
  reviewState: {},
  isAdmin: true,
});
assert.ok(initial);
assert.equal(initial.reviewed_count, 1);
assert.equal(initial.total_count, 4);
assert.equal(initial.remaining_count, 3);
assert.equal(initial.steps.find((step) => step.id === "assistant_model").reviewed, true);
assert.match(initial.steps.find((step) => step.id === "assistant_model").value, /OpenAI · gpt-5-mini · Automatic API format/);
assert.equal(initial.steps.find((step) => step.id === "instructions").reviewed, false);
assert.equal(initial.steps.find((step) => step.id === "home_assistant_access").state, "warning");
assert.equal(initial.steps.find((step) => step.id === "connection_test").reviewed, false);

const reviewedDefaults = buildGettingStarted({
  facts: baseFacts,
  agent,
  result: freshResult,
  reviewState: {reviewed:["instructions", "home_assistant_access", "connection_test"]},
  isAdmin: true,
});
assert.equal(reviewedDefaults, null, "reviewing intentional defaults should complete one-time onboarding");

const emptyPrompt = buildGettingStarted({
  facts: {...baseFacts, prompt_state:"empty", exposed_entity_count:2},
  agent,
  result: freshResult,
  reviewState: {reviewed:["instructions", "connection_test"]},
  isAdmin: true,
});
assert.ok(emptyPrompt);
const emptyInstructions = emptyPrompt.steps.find((step) => step.id === "instructions");
assert.equal(emptyInstructions.reviewed, false, "an actually empty prompt remains blocking even if previously reviewed");
assert.equal(emptyInstructions.state, "warning");

const configured = buildGettingStarted({
  facts: {...baseFacts, prompt_state:"custom", exposed_entity_count:4},
  agent,
  result: freshResult,
  reviewState: {reviewed:["connection_test"]},
  isAdmin: true,
});
assert.equal(configured, null);

assert.equal(buildGettingStarted({facts:baseFacts, agent, result:freshResult, isAdmin:false}), null);
assert.equal(buildGettingStarted({facts:{unavailable:true}, agent, result:freshResult, isAdmin:true}), null);
assert.equal(buildGettingStarted({
  facts:baseFacts,
  agent,
  result:{...freshResult, load_errors:[{key:"usage"}]},
  isAdmin:true,
}), null, "do not show first-run onboarding when usage history cannot be determined");
assert.equal(buildGettingStarted({
  facts:baseFacts,
  agent,
  result:{usage:{lifetime:{api_request_count:3}}},
  isAdmin:true,
}), null, "established agents must not be nagged after upgrade");
assert.equal(buildGettingStarted({
  facts:baseFacts,
  agent,
  result:freshResult,
  reviewState:{dismissed:true},
  isAdmin:true,
}), null);

const values = new Map();
const storage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
};
assert.deepEqual(readOnboardingReviewState(agent, storage), {dismissed:false, reviewed:[]});
assert.equal(markOnboardingStepReviewed(agent, "instructions", storage), true);
assert.deepEqual(readOnboardingReviewState(agent, storage), {dismissed:false, reviewed:["instructions"]});
assert.equal(markOnboardingStepReviewed(agent, "instructions", storage), true);
assert.deepEqual(readOnboardingReviewState(agent, storage), {dismissed:false, reviewed:["instructions"]});
assert.equal(dismissOnboarding(agent, storage), true);
assert.deepEqual(readOnboardingReviewState(agent, storage), {dismissed:true, reviewed:["instructions"]});

const brokenStorage = {
  getItem: () => { throw new Error("storage blocked"); },
  setItem: () => { throw new Error("storage blocked"); },
};
assert.deepEqual(readOnboardingReviewState(agent, brokenStorage), {dismissed:false, reviewed:[]});
assert.equal(markOnboardingStepReviewed(agent, "instructions", brokenStorage), false);
assert.equal(dismissOnboarding(agent, brokenStorage), false);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-onboarding.js", import.meta.url),
  "utf8",
);
assert.match(source, /Getting started/);
assert.match(source, /First-time setup/);
assert.match(source, /Optional capabilities such as Memory, Knowledge, Web Search and Function Tools are not required here/);
assert.match(source, /Setup & health below remains the ongoing source of truth/);
assert.match(source, /panel\._data\?\.is_admin === true/);
assert.match(source, /panel\._pendingSettingFocus/);
assert.match(source, /panel\._navigate/);
assert.match(source, /Nothing runs automatically from Overview/);
assert.doesNotMatch(source, /callWS\(/, "the onboarding card must not add provider or management calls");

const overviewPage = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-page.js", import.meta.url),
  "utf8",
);
assert.match(overviewPage, /overview-onboarding\.js/);
assert.match(overviewPage, /bindGettingStarted\(panel\)/);
