import assert from "node:assert/strict";
import test from "node:test";

import {ensureSearchConfiguration} from "../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js";

function searchFixture() {
  const requests = [];
  const results = {dataset:{}};
  const panel = {
    _agentId:"A",
    _data:{is_admin:true},
    _hass:{user:{id:"admin"}},
    get hass() {return this._hass;},
    _settingsSearchQuery:"conversation timeout",
    _canAccessView:() => true,
    _e:String,
    shadowRoot:{querySelector:() => results},
    _call(section, action) {
      assert.deepEqual([section, action], ["configuration", "get"]);
      const request = Promise.withResolvers();
      requests.push(request);
      return request.promise;
    },
  };
  return {panel, requests, results};
}

function configuration(minutes) {
  return {config:{conversation_timeout_minutes:minutes}};
}

for (const oldFails of [false, true]) {
  for (const oldFinishesFirst of [false, true]) {
    test(`only the returning assistant's read owns Search (old fails=${oldFails}, old first=${oldFinishesFirst})`, async () => {
      const {panel, requests, results} = searchFixture();
      const old = ensureSearchConfiguration(panel);
      panel._agentId = "B";
      const other = ensureSearchConfiguration(panel);
      panel._agentId = "A";
      const latest = ensureSearchConfiguration(panel);
      assert.equal(requests.length, 3);
      assert.match(results.innerHTML, /Current value loading/);

      const finishOld = async () => {
        if (oldFails) requests[0].reject(new Error("Old request failed"));
        else requests[0].resolve(configuration(15));
        await old;
      };
      if (oldFinishesFirst) {
        const pendingMarkup = results.innerHTML;
        const pendingRevision = panel._eocSearchResultsRevision;
        await finishOld();
        assert.equal(results.innerHTML, pendingMarkup);
        assert.equal(panel._eocSearchResultsRevision, pendingRevision);
      }
      // B can finish between the two A requests without changing the spinner.
      requests[1].resolve(configuration(20));
      await other;
      assert.match(results.innerHTML, /Current value loading/);
      const sameRead = ensureSearchConfiguration(panel);
      assert.equal(requests.length, 3);
      requests[2].resolve(configuration(35));
      await Promise.all([latest, sameRead]);
      const settledRevision = panel._eocSearchResultsRevision;
      if (!oldFinishesFirst) await finishOld();
      assert.equal(panel._eocSearchResultsRevision, settledRevision);
      assert.deepEqual(panel._settingsSearchConfig, configuration(35));
      assert.doesNotMatch(results.innerHTML, /loading|Retry/);
    });
  }
}

test("returning to the original account starts a new read of the same assistant", async () => {
  const {panel, requests, results} = searchFixture();
  const old = ensureSearchConfiguration(panel);
  panel._eocAuthenticationGeneration = 1;
  panel._hass.user.id = "another-admin";
  panel._eocPresentationBlocked = true;
  await ensureSearchConfiguration(panel);
  assert.equal(requests.length, 1);
  panel._eocAuthenticationGeneration = 2;
  panel._hass.user.id = "admin";
  panel._eocPresentationBlocked = false;
  const latest = ensureSearchConfiguration(panel);
  assert.equal(requests.length, 2);
  requests[0].reject(new Error("Previous session failed"));
  await old;
  assert.match(results.innerHTML, /Current value loading/);
  assert.doesNotMatch(results.innerHTML, /Retry/);
  requests[1].resolve(configuration(45));
  await latest;
  assert.deepEqual(panel._settingsSearchConfig, configuration(45));
});

test("a retired draft read cannot leave an error or block a retry without a replacement", async () => {
  const {panel, requests, results} = searchFixture();
  const old = ensureSearchConfiguration(panel);
  // The panel increments this generation when clearing the draft on an assistant switch.
  panel._eocDraftGeneration = 2;
  requests[0].reject(new Error("Previous draft failed"));
  await old;
  assert.doesNotMatch(results.innerHTML, /loading|Retry/);
  const latest = ensureSearchConfiguration(panel);
  assert.equal(requests.length, 2);
  requests[1].resolve(configuration(25));
  await latest;
  assert.deepEqual(panel._settingsSearchConfig, configuration(25));
});
