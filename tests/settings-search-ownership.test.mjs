import assert from "node:assert/strict";
import test from "node:test";
import {ExtendedOpenAIManagementPanel} from "./management-ownership-harness.mjs";

import {ensureSearchConfiguration, settingCurrentState} from "../custom_components/extended_openai_conversation_responses/frontend/management-navigation-search.js";

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

test("an acknowledged Knowledge toggle invalidates cached Search values", async () => {
  const {panel, requests, results} = searchFixture();
  Object.assign(panel, {
    _settingsSearchQuery:"knowledge library access",
    _cleanConfigSnapshots:new Map(),
    _configurationSnapshotKey:() => null,
    _clearSearchData:ExtendedOpenAIManagementPanel.prototype._clearSearchData,
  });
  const invalidate = () => ExtendedOpenAIManagementPanel.prototype._invalidateCleanConfiguration.call(panel, "A");
  const before = ensureSearchConfiguration(panel);
  requests[0].resolve({config:{knowledge_enabled:true}});
  await before;
  assert.match(results.innerHTML, /Current: On/);

  invalidate();
  const obsolete = ensureSearchConfiguration(panel);
  invalidate();
  const after = ensureSearchConfiguration(panel);
  requests[2].resolve({config:{knowledge_enabled:false}});
  await after;
  requests[1].resolve({config:{knowledge_enabled:true}});
  await obsolete;
  assert.match(results.innerHTML, /Current: Off/);
});

test("Retention Search fills missing values from full settings and keeps draft values", async () => {
  const {panel, requests, results} = searchFixture();
  panel._draftAgentId = panel._agentId;
  panel._configData = {projection:"retention", config:{usage_run_retention_days:20}};
  panel._draft = {usage_run_retention_days:30};
  panel._settingsSearchQuery = "run details retention";
  await ensureSearchConfiguration(panel);
  assert.equal(requests.length, 0);
  panel._settingsSearchQuery = "conversation timeout";
  const pending = ensureSearchConfiguration(panel);
  assert.equal(requests.length, 1);
  assert.match(results.innerHTML, /Current value loading/);
  requests[0].resolve({config:{conversation_timeout_minutes:45, usage_run_retention_days:20}});
  await pending;
  assert.match(results.innerHTML, /Current: 45/);
  panel._settingsSearchQuery = "usage run retention";
  await ensureSearchConfiguration(panel);
  assert.equal(requests.length, 1);
  assert.deepEqual(settingCurrentState({configKey:"usage_run_retention_days"}, panel), {
    label:"Current draft", value:"30",
  });
});

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
