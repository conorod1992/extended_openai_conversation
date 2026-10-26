import assert from "node:assert/strict";
import test from "node:test";
import {setImmediate} from "node:timers/promises";
import {ensureNativePicker} from "../custom_components/extended_openai_conversation_responses/frontend/native-picker-loader.js";
import {bindQuietHours} from "../custom_components/extended_openai_conversation_responses/frontend/quiet-hours-ui.js";

function documentFixture() {
  const registered = new Set();
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const state = {loads:0, register:true};
  const document = {
    defaultView:{customElements:{get:name => registered.has(name)}},
    createElement(name) {
      if (name === "partial-panel-resolver") return {getRoutes:() => ({routes:{config:{load:async () => { state.loads++; await gate; }}}})};
      if (name === "ha-panel-config") return {routerOptions:{routes:{automation:{load:async () => {
        if (state.register) { registered.add("ha-user-picker"); registered.add("ha-entity-picker"); }
      }}}}};
      return {setAttribute(name,value) { this[name] = value; }, remove() { this.removed = true; }};
    },
  };
  return {document,state,registered,release};
}

test("native picker loading is shared within each document and isolated across fresh documents", async () => {
  const first = documentFixture(), second = documentFixture();
  const requests = [ensureNativePicker("ha-user-picker",first.document), ensureNativePicker("ha-entity-picker",first.document), ensureNativePicker("ha-entity-picker",second.document)];
  assert.equal(first.state.loads,1);
  assert.equal(second.state.loads,1);
  first.release(); second.release();
  await Promise.all(requests);
  await ensureNativePicker("ha-entity-picker",first.document);
  assert.equal(first.state.loads,1);
});

test("missing registration is an error and a later attempt can retry", async () => {
  const fixture = documentFixture();
  fixture.state.register = false;
  const request = ensureNativePicker("ha-entity-picker",fixture.document);
  fixture.release();
  await assert.rejects(request,/could not be loaded/);
  fixture.state.register = true;
  await ensureNativePicker("ha-entity-picker",fixture.document);
  assert.equal(fixture.state.loads,2);
});

function quietFixture() {
  const fixture = documentFixture();
  const picker = {
    ownerDocument:fixture.document, isConnected:true,
    dataset:{satellite:"assist_satellite.test",kind:"media_player_entity_id",domain:"media_player"},
    addEventListener() {}, after(status) { this.status = status; },
  };
  const writes = [];
  for (const name of ["hass","value","includeDomains","includeEntities","allowCustomEntity","placeholder"]) {
    Object.defineProperty(picker,name,{set(value) {
      assert.ok(fixture.registered.has("ha-entity-picker"), `${name} assigned before registration`);
      writes.push([name,value]);
    }});
  }
  const root = {addEventListener() {}, querySelector:() => null, querySelectorAll:selector => selector === ".qh-override" ? [picker] : []};
  const panel = {shadowRoot:root, _agentId:"agent", _viewKey:() => "capabilities/quiet-hours", _quietHoursDraft:{overrides:{}}, _result:{satellites:[]}};
  return {...fixture,panel,picker,writes};
}

test("Quiet Hours waits for native registration before assigning picker properties", async () => {
  const fixture = quietFixture();
  bindQuietHours(fixture.panel);
  assert.equal(fixture.picker.hidden,true);
  assert.deepEqual(fixture.writes,[]);
  fixture.release();
  await setImmediate();
  assert.equal(fixture.picker.hidden,false);
  assert.ok(fixture.writes.some(([name]) => name === "includeEntities"));
  assert.equal(fixture.picker.status.removed,true);
});

test("Quiet Hours reports failed loading visibly and does not initialize a detached picker", async () => {
  const failed = quietFixture();
  failed.state.register = false;
  bindQuietHours(failed.panel);
  failed.release();
  await setImmediate();
  assert.equal(failed.picker.status.role,"alert");
  assert.match(failed.picker.status.textContent,/could not be loaded/);
  assert.equal(failed.picker.hidden,true);
  const stale = quietFixture();
  bindQuietHours(stale.panel);
  stale.picker.isConnected = false;
  stale.release();
  await setImmediate();
  assert.deepEqual(stale.writes,[]);
});
