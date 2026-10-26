import assert from "node:assert/strict";
import test from "node:test";
import {fixture, edit, retire, captureConfigurationSubmission, configurationSubmissionIsCurrent} from "./management-ownership-harness.mjs";
import {deferred} from "./frontend-test-helpers.mjs";

const command = {names:["example"], enabled:false};

function observed({unknown = false} = {}) {
  const context = fixture(), panel = context.create();
  if (unknown) panel.hass.user = undefined;
  // Exercise the actual setter's first observation before external HA code
  // mutates and reuses this very same payload object.
  panel.hass = panel.hass;
  edit(panel, "prompt", "Retained authoring from the mutable HA payload");
  return {...context, panel};
}

function authored(panel) {
  return {draft:panel._draft, baseline:panel._configData, result:panel._result,
    values:structuredClone(panel._draft), title:panel._draftTitle};
}

function assertAuthored(panel, before) {
  assert.equal(panel._draft, before.draft);
  assert.equal(panel._configData, before.baseline);
  assert.equal(panel._result, before.result);
  assert.deepEqual(panel._draft, before.values);
  assert.equal(panel._draftTitle, before.title);
}

function roundTrip(panel, shape) {
  const same = panel.hass, originalUser = same.user, id = originalUser.id;
  if (shape === "user id mutation") same.user.id = "different-actor";
  else if (shape === "nested user replacement") same.user = {id:"different-actor"};
  else delete same.user;
  panel.hass = same;
  if (shape === "user id mutation") same.user.id = id;
  else same.user = originalUser;
  panel.hass = same;
  assert.equal(panel.hass, same, "the external hass object was reused throughout");
}

function outcome(promise) {
  return promise.then(value => ({status:"fulfilled", value}), error => ({status:"rejected", error}));
}

function queued(panel, barrier) {
  panel._eocFunctionMutationTail = barrier.promise;
  return panel._call("tools", "set_enabled", command);
}

for (const shape of ["user id mutation", "nested user replacement", "logout"]) {
  test(`same hass object ${shape} round trip retires queued authority and retains the source`, async t => {
    const {panel, state} = observed(), barrier = deferred();
    const before = authored(panel), owner = captureConfigurationSubmission(panel);
    const pending = queued(panel, barrier), settled = outcome(pending);
    t.after(async () => {barrier.resolve(); await settled; retire(panel);});
    roundTrip(panel, shape);
    assertAuthored(panel, before);
    barrier.resolve();
    const result = await settled;
    assert.equal(result.status, "rejected", "a previous actor session must not dispatch after returning to the same id");
    assert.match(result.error.message, /changed|owner|draft/i);
    assert.equal(configurationSubmissionIsCurrent(panel, owner), false);
    assert.equal(state.writes.length, 0);
    assert.equal(panel._pendingMutations.size, 0);
    assertAuthored(panel, before);
  });
}

test("a fresh identical command is not deduplicated into a same-object retired session", async t => {
  const {panel, state} = observed(), barrier = deferred();
  const old = queued(panel, barrier), oldResult = outcome(old);
  t.after(async () => {barrier.resolve(); await oldResult; retire(panel);});
  roundTrip(panel, "user id mutation");
  const fresh = panel._call("tools", "set_enabled", command), freshResult = outcome(fresh);
  barrier.resolve();
  assert.equal((await oldResult).status, "rejected");
  assert.equal((await freshResult).status, "fulfilled");
  assert.equal(state.writes.length, 1);
  assert.equal(state.writes[0].subentry_id, panel._agentId);
  assert.equal(panel._pendingMutations.size, 0);
});

test("same-user state mutations reuse authentication state and preserve normal queue deduplication", async t => {
  const {panel, state} = observed(), barrier = deferred(), same = panel.hass;
  const before = authored(panel), owner = captureConfigurationSubmission(panel), authentication = panel._eocAuthentication;
  const visit = panel._loadToken, catalogRead = panel._eocCatalogReadSequence, modelToken = panel._eocModelChangeToken;
  const first = queued(panel, barrier), firstResult = outcome(first);
  t.after(async () => {barrier.resolve(); await firstResult; retire(panel);});
  for (let index=0; index<4; index++) {
    same.states = {[`sensor.update_${index}`]:{state:String(index)}};
    same.themes = {darkMode:Boolean(index % 2)};
    panel.hass = same;
  }
  assert.equal(configurationSubmissionIsCurrent(panel, owner), true);
  assert.equal(panel._eocAuthentication, authentication, "same-user updates allocate no replacement authentication record");
  assert.equal(panel._loadToken, visit);
  assert.equal(panel._eocCatalogReadSequence, catalogRead);
  assert.equal(panel._eocModelChangeToken, modelToken);
  assertAuthored(panel, before);
  const duplicate = panel._call("tools", "set_enabled", command);
  barrier.resolve();
  await first;
  await duplicate;
  assert.equal(state.writes.length, 1);
  assert.equal(panel._draft, before.draft);
  assert.deepEqual(panel._draft, before.values);
  assert.equal(panel._pendingMutations.size, 0);
});

test("same-user nested user replacement preserves the historical identity and owner", t => {
  const {panel} = observed(), same = panel.hass;
  t.after(() => retire(panel));
  const before = authored(panel), owner = captureConfigurationSubmission(panel), authentication = panel._eocAuthentication;
  same.user = {...same.user, name:"Updated same-user metadata"};
  panel.hass = same;
  assert.equal(configurationSubmissionIsCurrent(panel, owner), true);
  assert.equal(captureConfigurationSubmission(panel).authenticationGeneration, owner.authenticationGeneration);
  assert.equal(panel._eocAuthentication, authentication);
  assertAuthored(panel, before);
});

test("initial unknown identity can become known without reviving its queued command", async t => {
  const {panel, state} = observed({unknown:true}), barrier = deferred(), same = panel.hass;
  const before = authored(panel), unknown = captureConfigurationSubmission(panel);
  const pending = queued(panel, barrier), settled = outcome(pending);
  t.after(async () => {barrier.resolve(); await settled; retire(panel);});
  same.user = {id:"first-known-actor"};
  panel.hass = same;
  const known = captureConfigurationSubmission(panel);
  assert.equal(known.authenticationGeneration, unknown.authenticationGeneration, "first identity resolution retains initial editor compatibility");
  assert.equal(configurationSubmissionIsCurrent(panel, unknown), false);
  assert.equal(configurationSubmissionIsCurrent(panel, known), true);
  assertAuthored(panel, before);
  barrier.resolve();
  assert.equal((await settled).status, "rejected");
  assert.equal(state.writes.length, 0);
  await panel._call("tools", "set_enabled", command);
  assert.equal(state.writes.length, 1);
  assert.equal(panel._draft, before.draft);
});

test("the first known identity cannot regain authority after later shared-object mutations", async t => {
  const {panel, state} = observed({unknown:true}), barrier = deferred(), same = panel.hass;
  same.user = {id:"first-known-actor"};
  panel.hass = same;
  const before = authored(panel), owner = captureConfigurationSubmission(panel);
  const pending = queued(panel, barrier), settled = outcome(pending);
  t.after(async () => {barrier.resolve(); await settled; retire(panel);});
  roundTrip(panel, "user id mutation");
  barrier.resolve();
  assert.equal((await settled).status, "rejected");
  assert.equal(configurationSubmissionIsCurrent(panel, owner), false);
  assert.equal(state.writes.length, 0);
  assertAuthored(panel, before);
});
