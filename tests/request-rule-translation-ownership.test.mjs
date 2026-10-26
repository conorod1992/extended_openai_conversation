import assert from "node:assert/strict";
import test from "node:test";
import {ensureRequestRuleTranslations} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";
import {deferred} from "./frontend-test-helpers.mjs";

for (const replacement of ["connection", "language", "locale"]) for (const failed of [false, true]) {
  test(`retired ${replacement} translation ${failed ? "failure" : "success"} cannot affect a newer cache`, async () => {
    const old = deferred(), fresh = deferred(), selectors = [{}], messages = [];
    let calls = 0;
    const first = {connection:{}, ...(replacement === "locale" ? {locale:{language:"en"}} : {language:"en"}),
      loadFragmentTranslation() {calls++; return old.promise;}};
    const panel = {hass:first, shadowRoot:{querySelectorAll:() => selectors}, _toast:message => messages.push(message)};
    const previous = ensureRequestRuleTranslations(panel);
    panel.hass = {...first, connection:replacement === "connection" ? {} : first.connection,
      ...(replacement === "locale" ? {locale:{language:"de"}} : {language:replacement === "language" ? "de" : "en"}),
      loadFragmentTranslation() {calls++; return fresh.promise;}};
    const current = ensureRequestRuleTranslations(panel), localize = () => "current labels";
    fresh.resolve(localize); await current;
    assert.equal(selectors[0].hass.localize, localize);
    if (failed) old.reject(Error("obsolete translation failure")); else old.resolve(() => "obsolete labels");
    await previous;
    assert.equal(selectors[0].hass.localize, localize);
    assert.deepEqual(messages, []);
    assert.equal(ensureRequestRuleTranslations(panel), current, "an old failure must not delete a newer cache");
    assert.equal(calls, 2);
  });
}

test("matching translation readiness hydrates the current selectors across editor reopening", async () => {
  const ready = deferred(), original = {}, reopened = {}, localize = () => "ready labels";
  let selectors = [original], calls = 0;
  const panel = {hass:{language:"en", connection:{}, loadFragmentTranslation() {calls++; return ready.promise;}},
    shadowRoot:{querySelectorAll:() => selectors}};
  const pending = ensureRequestRuleTranslations(panel);
  selectors = [reopened];
  assert.equal(ensureRequestRuleTranslations(panel), pending);
  ready.resolve(localize); await pending;
  assert.equal(original.hass, undefined);
  assert.equal(reopened.hass.localize, localize);
  assert.equal(calls, 1);
});

test("a current translation failure remains visible and allows a later retry", async () => {
  const selector = {}, messages = [], localize = () => "retry labels";
  let calls = 0;
  const panel = {hass:{language:"en", connection:{}, loadFragmentTranslation() {
    return ++calls === 1 ? Promise.reject(Error("current failure")) : Promise.resolve(localize);
  }}, shadowRoot:{querySelectorAll:() => [selector]}, _toast:message => messages.push(message)};
  await ensureRequestRuleTranslations(panel);
  assert.equal(messages.length, 1);
  assert.match(messages[0], /current failure/);
  await ensureRequestRuleTranslations(panel);
  assert.equal(calls, 2);
  assert.equal(selector.hass.localize, localize);
});
