import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const actions = event => [{event, event_data:{message:`Actions for ${event}`}}];
const selectorFor = panel => panel.locator("#rule-action-sequence-host ha-selector");

async function setActions(panel, value) {
  await selectorFor(panel).evaluate((selector, value) => {
    selector.value = structuredClone(value);
    selector.dispatchEvent(new CustomEvent("value-changed", {detail:{value}, bubbles:true, composed:true}));
  }, value);
}

async function prepare(page, bundled, existing = false) {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(panel.getByRole("button", {name:"Create rule", exact:true}).first()).toBeVisible();
  await panel.evaluate(host => {
    const original = host.hass.callWS.bind(host.hass);
    const probe = window.ruleSaveDraft = {writes:[], backend:original};
    host.hass.user = {id:"rule-save-draft-owner", is_admin:true};
    host.hass.callWS = request => {
      if (request.section !== "request_rules" || !["create", "update"].includes(request.action)) return original(request);
      return new Promise((resolve, reject) => {
        // Only the transport is held. The fixture still owns persistence, IDs,
        // revisions and normalization when the backend accepts the write.
        const record = {request:structuredClone(request), commit:async () => {
          if (record.result) return record.result;
          if (request.revision !== browserHarness.getState().requestRules.revision) {
            throw new Error("Request Rules changed in another tab. Reload the latest rules before saving.");
          }
          return record.result = await original(request);
        }, finish:async (mode = "success") => {
          try {
            if (mode === "failure") throw new Error("Held Rule save failed");
            const result = structuredClone(await record.commit());
            if (mode === "missing-id") delete result.rule.id;
            if (mode === "missing-revision") delete result.revision;
            resolve(result);
          } catch (error) { reject(error); }
          record.released = true;
        }};
        probe.writes.push(record);
      });
    };
  });
  if (existing) await panel.locator(".rule-edit").first().click();
  else await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await panel.evaluate(host => {
    ruleSaveDraft.dialog = host.shadowRoot.querySelector("#rule-dialog");
    ruleSaveDraft.selector = host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector");
  });
  return {panel, errors};
}

async function localDraft(panel, name, event) {
  await panel.locator("#rule-name").fill(name);
  await panel.locator("#rule-phrases").fill("save draft command");
  await panel.locator("#rule-action-type").selectOption("local_action");
  await setActions(panel, actions(event));
}

async function submit(page, panel, count = 1) {
  await panel.locator("#rule-save").click();
  await expect.poll(() => page.evaluate(() => ruleSaveDraft.writes.length)).toBe(count);
}

async function release(page, index = 0, mode = "success") {
  await page.evaluate(({index, mode}) => ruleSaveDraft.writes[index].finish(mode), {index, mode});
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function expectRetained(panel) {
  await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
  await expect(panel.locator("#rule-save")).toBeEnabled();
  expect(await panel.evaluate(host => ruleSaveDraft.dialog === host.shadowRoot.querySelector("#rule-dialog")
    && ruleSaveDraft.selector === host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector"))).toBe(true);
  expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
}

for (const bundled of [false, true]) {
  const surface = bundled ? "bundle" : "source";

  test(`Rule save ACK preserves later native action edits (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, true);
    await localDraft(panel, "Submitted Rule A", "submitted_a");
    await submit(page, panel);
    await panel.locator("#rule-name").fill("Later Rule B");
    await setActions(panel, actions("later_b"));
    await release(page);
    await expectRetained(panel);
    await expect(panel.locator("#rule-name")).toHaveValue("Later Rule B");
    expect(await selectorFor(panel).evaluate(selector => selector.value)).toEqual(actions("later_b"));
    const saved = await page.evaluate(() => browserHarness.getState().requestRules.rules.find(rule => rule.id === "rule-1"));
    expect(saved.name).toBe("Submitted Rule A");
    expect(saved.action.actions).toEqual(actions("submitted_a"));
    expect(await page.evaluate(() => ruleSaveDraft.writes.length)).toBe(1);
    await expectHarnessClean(page, errors);
  });

  test(`Rule save ACK preserves a revert to the opening draft (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, true);
    const openedName = await panel.locator("#rule-name").inputValue();
    await panel.locator("#rule-name").fill("Submitted replacement name");
    await submit(page, panel);
    await panel.locator("#rule-name").fill(openedName);
    await release(page);
    await expectRetained(panel);
    await expect(panel.locator("#rule-name")).toHaveValue(openedName);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules[0].name)).toBe("Submitted replacement name");
    // The successful dispatch becomes the baseline. Returning to its exact
    // controls is clean even though the dialog remains available to the user.
    await panel.locator("#rule-name").fill("Submitted replacement name");
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    await expectHarnessClean(page, errors);
  });

  test(`Rule save ACK preserves later invalid native YAML (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, true);
    await localDraft(panel, "Submitted native Rule", "submitted_native");
    await submit(page, panel);
    const raw = "- event: later_native\n  event_data: [invalid";
    await selectorFor(panel).evaluate(selector => {
      const root = selector.shadowRoot || selector.attachShadow({mode:"open"});
      const wrapper = document.createElement("ha-selector-action");
      const collection = document.createElement("ha-automation-action");
      const yaml = document.createElement("ha-yaml-editor");
      root.append(wrapper);
      wrapper.attachShadow({mode:"open"}).append(collection);
      collection.attachShadow({mode:"open"}).append(yaml);
      ruleSaveDraft.yaml = yaml;
    });
    // Native YAML events are observed in the child's shadow root; the generic
    // selector intentionally retains the last valid parsed actions.
    await selectorFor(panel).evaluate((selector, raw) => {
      ruleSaveDraft.yaml.yaml = raw;
      ruleSaveDraft.yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:false}, bubbles:true, composed:true}));
    }, raw);
    await expect.poll(() => selectorFor(panel).evaluate(selector => selector.__eocYamlDraft())).toEqual([{raw, valid:false, pending:false}]);
    await release(page);
    await expectRetained(panel);
    expect(await panel.evaluate(() => ruleSaveDraft.yaml.yaml)).toBe(raw);
    expect(await selectorFor(panel).evaluate(selector => selector.value)).toEqual(actions("submitted_native"));
    await panel.locator("#rule-save").click();
    await expect(panel.locator("#rule-error")).toContainText("Correct the YAML errors");
    expect(await page.evaluate(() => ruleSaveDraft.writes.length)).toBe(1);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules[0].action.actions)).toEqual(actions("submitted_native"));
    await expectHarnessClean(page, errors);
  });

  test(`retained Rule create uses its ACK ID and revision for the next update (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled);
    await localDraft(panel, "Created Rule A", "created_a");
    await submit(page, panel);
    await panel.locator("#rule-name").fill("Created Rule B");
    await setActions(panel, actions("created_b"));
    await release(page);
    await expectRetained(panel);
    const acknowledged = await page.evaluate(() => ruleSaveDraft.writes[0].result);
    await submit(page, panel, 2);
    const update = await page.evaluate(() => ruleSaveDraft.writes[1].request);
    expect(update.action).toBe("update");
    expect(update.rule_id).toBe(acknowledged.rule.id);
    expect(update.revision).toBe(acknowledged.revision);
    expect(update.rule.name).toBe("Created Rule B");
    expect(update.rule.action.actions).toEqual(actions("created_b"));
    await release(page, 1);
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
    const saved = await page.evaluate(() => browserHarness.getState().requestRules.rules.filter(rule => rule.id === ruleSaveDraft.writes[0].result.rule.id));
    expect(saved).toHaveLength(1);
    expect(saved[0].name).toBe("Created Rule B");
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
    await expectHarnessClean(page, errors);
  });

  test(`Rule authoring revision survives an intervening accepted read (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, true);
    await localDraft(panel, "Submitted before other writer", "own_write");
    await submit(page, panel);
    await panel.locator("#rule-name").fill("Later unsaved authoring");
    await panel.evaluate(async host => {
      await ruleSaveDraft.writes[0].commit();
      await ruleSaveDraft.backend({type:"extended_openai_conversation_responses/management", section:"request_rules", action:"duplicate",
        entry_id:"entry-1", subentry_id:"agent-1", rule_id:"rule-1", revision:browserHarness.getState().requestRules.revision});
      host._sectionCache.delete(host._sectionCacheKey());
      await host._loadSection(true);
      ruleSaveDraft.accepted = structuredClone(host._result);
    });
    const accepted = await panel.evaluate(() => ruleSaveDraft.accepted);
    expect(accepted.revision).toBeGreaterThan(await page.evaluate(() => ruleSaveDraft.writes[0].result.revision));
    await release(page);
    await expectRetained(panel);
    await expect(panel.locator("#rule-name")).toHaveValue("Later unsaved authoring");
    expect(await panel.evaluate(host => host._result)).toEqual(accepted);
    await submit(page, panel, 2);
    // A read can advance the collection while this authoring still belongs to
    // an earlier revision. It cannot silently authorize overwriting that read.
    expect(await page.evaluate(() => ruleSaveDraft.writes[1].request.revision)).not.toBe(accepted.revision);
    await release(page, 1);
    await expect(panel.locator("#rule-error")).toContainText("changed in another tab");
    await expectRetained(panel);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.revision)).toBe(accepted.revision);
    await expectHarnessClean(page, errors);
  });

  test(`failed Rule save retains later controls and permits retry (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, true);
    await localDraft(panel, "Failed submitted Rule", "failed_submission");
    await submit(page, panel);
    await panel.locator("#rule-name").fill("Retry current Rule");
    await setActions(panel, actions("retry_current"));
    await release(page, 0, "failure");
    await expectRetained(panel);
    await expect(panel.locator("#rule-error")).toContainText("Held Rule save failed");
    await submit(page, panel, 2);
    expect(await page.evaluate(() => ruleSaveDraft.writes[1].request.revision)).toBe(await page.evaluate(() => ruleSaveDraft.writes[0].request.revision));
    await release(page, 1);
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules[0].name)).toBe("Retry current Rule");
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules[0].action.actions)).toEqual(actions("retry_current"));
    await expectHarnessClean(page, errors);
  });

  test(`detached Rule ACK keeps later raw YAML available for recovery (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, true);
    await localDraft(panel, "Submitted before replacement", "before_replacement");
    await submit(page, panel);
    const raw = "- event: unsaved_replacement\n  event_data: [invalid";
    await selectorFor(panel).evaluate(selector => {
      const root = selector.shadowRoot || selector.attachShadow({mode:"open"});
      root.append(document.createElement("ha-yaml-editor"));
    });
    await selectorFor(panel).evaluate((selector, raw) => {
      const yaml = selector.shadowRoot.querySelector("ha-yaml-editor");
      yaml.yaml = raw;
      yaml.dispatchEvent(new CustomEvent("value-changed", {detail:{isValid:false}, bubbles:true, composed:true}));
    }, raw);
    await panel.locator("#rule-name").fill("Later detached Rule draft");
    await panel.evaluate(host => {
      ruleSaveDraft.source = host;
      const parent = host.parentElement, hass = host.hass;
      host.remove();
      const replacement = document.createElement("extended-openai-management-panel");
      parent.append(replacement);
      replacement.hass = hass;
    });
    await expect(panel.locator(".rule-edit").first()).toBeVisible();
    await release(page);
    await expect(panel.getByRole("heading", {name:"Recovered unsaved editor"})).toBeVisible();
    const recovered = await panel.locator('[aria-label="Recovered editor values"]').inputValue();
    const values = JSON.parse(recovered);
    expect(values.find(value => value.id === "rule-name").value).toBe("Later detached Rule draft");
    expect(values.some(value => value.yaml?.some(draft => draft.raw === raw && !draft.valid))).toBe(true);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules[0].name)).toBe("Submitted before replacement");
    expect(await page.evaluate(() => ruleSaveDraft.writes.length)).toBe(1);
    await expectHarnessClean(page, errors);
  });

  for (const mode of ["missing-id", "missing-revision"]) {
    test(`Rule save rejects an incomplete ${mode} ACK without retiring the draft (${surface})`, async ({page}) => {
      const {panel, errors} = await prepare(page, bundled);
      await localDraft(panel, "Incomplete ACK Rule", "incomplete_ack");
      const before = await panel.evaluate(host => structuredClone(host._result));
      await submit(page, panel);
      await release(page, 0, mode);
      await expectRetained(panel);
      await expect(panel.locator("#rule-name")).toHaveValue("Incomplete ACK Rule");
      expect(await panel.evaluate(host => host._editingRuleId)).toBe(null);
      expect(await panel.evaluate(host => host._result)).toEqual(before);
      await expect(panel.locator("#rule-error")).not.toHaveText("");
      const committed = await page.evaluate(() => browserHarness.getState().requestRules);
      await submit(page, panel, 2);
      expect(await page.evaluate(() => ruleSaveDraft.writes[1].request.revision)).toBe(before.revision);
      await release(page, 1);
      await expect(panel.locator("#rule-error")).toContainText("changed in another tab");
      await expectRetained(panel);
      // The server may have committed despite an incomplete response. Retrying
      // the old authoring revision must fail instead of creating a duplicate.
      expect(await page.evaluate(() => browserHarness.getState().requestRules)).toEqual(committed);
      await expectHarnessClean(page, errors);
    });
  }
}
