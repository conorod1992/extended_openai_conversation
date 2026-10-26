import assert from "node:assert/strict";
import test from "node:test";
import {captureToolEditorOperation, acknowledgeToolEditor} from "../custom_components/extended_openai_conversation_responses/frontend/tool-yaml-editor-adapter.js";
import {captureConfigurationOwner} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-acknowledgement.js";

const tool = name => ({spec:{name}, function:{type:"template"}});
function panel(repair = false) {
  const textarea = {value:"submitted A"}, dialog = {open:true};
  const root = {querySelector:selector => selector === "#tool-dialog" ? dialog : selector === "#tool-yaml" ? textarea : null};
  const agent = {entry_id:"entry", subentry_id:"agent"};
  return {shadowRoot:root, textarea, dialog, agent, _agentId:"agent", _selectedAgent(){return this.agent;},
    _toolEditorLoad:repair ? null : {}, _repairToolLoad:repair ? {} : null,
    _repairToolIndex:repair ? 0 : null, _repairToolRevision:repair ? "r1" : null,
    _toolRevision:"r1", _toolOriginalName:repair ? null : "old", _toolInitialYaml:"original",
    _configData:{revision:"r1", config:{functions:repair ? [tool("other")] : [tool("old"), tool("other")]}}};
}

for (const repair of [false, true]) {
  test(`${repair ? "repair" : "healthy"} new commands retain the owner of their original opening`, () => {
    const host = panel(repair);
    host.hass = {user:{id:"opening-owner"}};
    const token = {owner:captureConfigurationOwner(host)};
    if (repair) host._repairToolLoad = token; else host._toolEditorLoad = token;
    host.hass = {user:{id:"different-user"}};
    assert.equal(captureToolEditorOperation(host).isCurrent(), false);
    host.hass = {user:{id:"opening-owner"}};
    host._eocAuthenticationGeneration = 2;
    assert.equal(captureToolEditorOperation(host).isCurrent(), false);
    const reopened = {owner:captureConfigurationOwner(host)};
    if (repair) host._repairToolLoad = reopened; else host._toolEditorLoad = reopened;
    assert.equal(captureToolEditorOperation(host).isCurrent(), true);
  });

  test(`${repair ? "repair" : "healthy"} initial identity binding never reassigns an already captured operation`, () => {
    const host = panel(repair), token = {owner:captureConfigurationOwner(host)};
    if (repair) host._repairToolLoad = token; else host._toolEditorLoad = token;
    const pendingUnknown = captureToolEditorOperation(host), original = token.owner;
    host.hass = {user:{id:"first-known"}};
    const firstKnown = captureToolEditorOperation(host);
    assert.equal(firstKnown.isCurrent(), true);
    assert.equal(token.owner.userId, "first-known");
    assert.notEqual(token.owner, original);
    assert.equal(original.userId, undefined);
    assert.equal(pendingUnknown.isCurrent(), false);
    host.hass = {user:{id:"second-known"}};
    assert.equal(captureToolEditorOperation(host).isCurrent(), false);
  });
}

for (const repair of [false, true]) {
  test(`${repair ? "repair" : "healthy"} operation separates generation ownership from submitted YAML`, () => {
    const host = panel(repair), operation = captureToolEditorOperation(host);
    assert.equal(operation.isCurrent(), true);
    assert.equal(operation.isUnchanged(), true);
    host.textarea.value = "newer B";
    assert.equal(operation.isCurrent(), true);
    assert.equal(operation.isUnchanged(), false);
    if (repair) host._repairToolLoad = {};
    else host._toolEditorLoad = {};
    assert.equal(operation.isCurrent(), false);
  });

  test(`${repair ? "repair" : "healthy"} own acknowledgement preserves raw edits and prepares an update`, () => {
    const host = panel(repair), operation = captureToolEditorOperation(host);
    const previous = {revision:host._configData.revision, config:host._configData.config};
    host.textarea.value = "newer B";
    const saved = {revision:"r2", functions:[tool("renamed"), tool("other")]};
    host._configData = {revision:"r2", config:{functions:saved.functions}};
    const next = acknowledgeToolEditor(host, operation, saved, previous);
    assert.equal(host.textarea.value, "newer B");
    assert.equal(host._toolInitialYaml, "submitted A");
    assert.equal(host._toolOriginalName, "renamed");
    assert.equal(host._toolRevision, "r2");
    assert.equal(host._repairToolIndex, null);
    assert.equal(host._repairToolLoad, null);
    assert.equal(next.isCurrent(), true);
    assert.equal(next.originalName, "renamed");
    assert.equal(next.revision, "r2");
  });

  test(`${repair ? "repair" : "healthy"} acknowledgement cannot rebase an intervening saved baseline`, () => {
    const host = panel(repair), operation = captureToolEditorOperation(host);
    host.textarea.value = "newer B";
    const previous = {revision:"r3", config:structuredClone(host._configData.config)};
    acknowledgeToolEditor(host, operation, {revision:"r2", functions:[tool("renamed"), tool("other")]}, previous);
    assert.equal(host._toolInitialYaml, "submitted A");
    assert.equal(operation.revision, "r1");
    assert.equal(repair ? host._repairToolRevision : host._toolRevision, "r1");
    assert.equal(host.textarea.value, "newer B");
  });
}

test("transport revision acknowledgement can advance its unchanged baseline envelope", () => {
  const host = panel(), operation = captureToolEditorOperation(host);
  host.textarea.value = "newer B";
  const previous = {revision:"r2", config:host._configData.config};
  acknowledgeToolEditor(host, operation, {revision:"r2", functions:[tool("old"), tool("other")]}, previous);
  assert.equal(host._toolRevision, "r2");
});

test("a reverted original draft remains dirty after an acknowledgement with an intervening read", () => {
  const host = panel(), operation = captureToolEditorOperation(host);
  host.textarea.value = "original";
  const previous = {revision:"r3", config:structuredClone(host._configData.config)};
  acknowledgeToolEditor(host, operation, {revision:"r2", functions:[tool("old"), tool("other")]}, previous);
  assert.equal(host._toolInitialYaml, "submitted A");
  assert.notEqual(host.textarea.value, host._toolInitialYaml);
  assert.equal(host._toolRevision, "r1", "dirty tracking must not silently rebase the authoring revision");
});

test("an old operation cannot acknowledge a reopened editor or another entry", () => {
  const host = panel(true), operation = captureToolEditorOperation(host);
  const previous = {revision:"r1", config:host._configData.config};
  host._repairToolLoad = {};
  host._toolInitialYaml = "new editor baseline";
  acknowledgeToolEditor(host, operation, {revision:"r2", functions:[tool("new"), tool("other")]}, previous);
  assert.equal(host._toolInitialYaml, "new editor baseline");
  const next = captureToolEditorOperation(host);
  host.agent = {...host.agent, entry_id:"different"};
  assert.equal(next.isCurrent(), false);
});

for (const saved of [{valid:false, revision:"r2", functions:[tool("old")]}, {revision:"r2"}, {functions:[tool("old")]}]) {
  test(`an unacknowledged response cannot change the dirty baseline: ${JSON.stringify(saved)}`, () => {
    const host = panel(), operation = captureToolEditorOperation(host);
    host.textarea.value = "original";
    acknowledgeToolEditor(host, operation, saved, {revision:"r1", config:host._configData.config});
    assert.equal(host._toolInitialYaml, "original");
    assert.equal(host._toolRevision, "r1");
  });
}

for (const repair of [false, true]) {
  for (const transition of ["user", "session", "root", "entry"]) {
    test(`${repair ? "repair" : "healthy"} callbacks cannot adopt a retired ${transition} owner`, () => {
      const host = panel(repair);
      host.hass = {user:{id:"original"}};
      const operation = captureToolEditorOperation(host);
      const previous = {revision:"r1", config:host._configData.config};
      if (transition === "user") host.hass = {user:{id:"replacement"}};
      if (transition === "session") host._eocDraftGeneration = 1;
      if (transition === "root") host.shadowRoot = {querySelector:selector => operation.root.querySelector(selector)};
      if (transition === "entry") host.agent = {...host.agent, entry_id:"replacement"};
      assert.equal(operation.isCurrent(), false);
      assert.equal(operation.isUnchanged(), false);
      acknowledgeToolEditor(host, operation, {revision:"r2", functions:[tool("renamed"), tool("other")]}, previous);
      assert.equal(host._toolInitialYaml, "original");
      assert.equal(repair ? host._repairToolRevision : host._toolRevision, "r1");
    });
  }
}
