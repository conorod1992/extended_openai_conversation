import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

const managementType = "extended_openai_conversation_responses/management";
const cardFor = (panel, id = "rule-1") => panel.locator(`.request-rule-card[data-rule-key="${id}"]`);

async function prepare(page, bundled, action, failed = false, secondRule = false, deferCommit = false) {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl("capabilities/request-rules", bundled ? "&bundle=1" : ""));
  const panel = page.locator("extended-openai-management-panel");
  await expect(cardFor(panel)).toBeVisible();
  await panel.evaluate(async (host, {action, failed, secondRule, deferCommit}) => {
    const backend = host.hass.callWS.bind(host.hass);
    const probe = window.ruleCollectionOwner = {backend, records:[], toasts:[]};
    probe.call = (action, extra = {}) => backend({type:"extended_openai_conversation_responses/management", section:"request_rules", action,
      entry_id:"entry-1", subentry_id:"agent-1", revision:browserHarness.getState().requestRules.revision, ...extra});
    host.hass.user = {id:"rule-collection-owner-A", is_admin:true};
    host._toast = (...args) => probe.toasts.push(args);
    if (action === "move" || secondRule) {
      await probe.call("create", {rule:{...browserHarness.getState().requestRules.rules[0], name:"Second collection rule"}});
      host._sectionCache.clear();
      await host._loadSection(true);
    }
    const heldAction = {group:"groups", move:"move", toggle:"update", duplicate:"duplicate", delete:"delete"}[action];
    const heldActions = action === "toggle-duplicate" ? ["update", "duplicate"] : [heldAction];
    host.hass.callWS = request => {
      if (request.section !== "request_rules" || !heldActions.includes(request.action)) return backend(request);
      return new Promise((resolve, reject) => {
        // A successful server write commits before its response is delivered.
        // Independent clients may then write and this panel may accept a read.
        const record = {request:structuredClone(request), committed:failed ? Promise.resolve(null) : deferCommit ? null : backend(request)};
        record.finish = async () => {
          if (failed) reject(Error(`Obsolete ${action} failed`));
          else {
            record.committed ||= backend(request);
            resolve(await record.committed);
          }
        };
        probe.records.push(record);
      });
    };
  }, {action, failed, secondRule, deferCommit});
  return {panel, errors};
}

async function dispatch(panel, action) {
  if (action === "group") {
    await panel.locator("#rule-groups-manage").click();
    await panel.locator("#rule-new-group-name").fill("Submitted collection group");
    await panel.locator("#rule-group-add").click();
  } else if (action === "move") {
    await cardFor(panel).locator(".rule-move-menu summary").click();
    await cardFor(panel).locator('.rule-move[data-direction="bottom"]').click();
  } else if (action === "toggle") await cardFor(panel).locator(".rule-enabled").uncheck();
  else if (action === "duplicate") await cardFor(panel).locator(".rule-duplicate").click();
  else {
    await cardFor(panel).locator(".rule-delete").click();
    await panel.locator("#confirm-accept").click();
  }
}

async function acceptNewerRead(panel, action, change) {
  await panel.evaluate(async (host, {action, change}) => {
    const probe = ruleCollectionOwner;
    await probe.records[0].committed;
    if (change === "authentication ABA") {
      host.hass = {...host.hass, user:{id:"rule-collection-owner-B", is_admin:true}};
      host.hass = {...host.hass, user:{id:"rule-collection-owner-A", is_admin:true}};
    }
    if (action === "group") await probe.call("groups", {groups:[{name:"Authoritative external group"}]});
    else {
      if (action === "duplicate") {
        for (const rule of browserHarness.getState().requestRules.rules.filter(rule => rule.id !== "rule-1")) {
          await probe.call("delete", {rule_id:rule.id});
        }
      }
      const rule = browserHarness.getState().requestRules.rules.find(rule => rule.id === "rule-1");
      if (rule) await probe.call("update", {rule_id:rule.id, rule:{...rule, name:"Authoritative external rule", enabled:true}});
      else await probe.call("create", {rule:{name:"Authoritative external rule", enabled:true, phrases:["external command"],
        match_type:"equals", action_type:"model_routing", action:{model:"gpt-5-mini"}, conditions:[]}});
    }
    host._sectionCache.clear();
    await host._loadSection(true);
    probe.accepted = structuredClone(host._result);
    probe.acceptedRevision = structuredClone(host._eocRequestRuleRevision);
    probe.readsBefore = browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "list").length;
  }, {action, change});
}

async function release(page, index = 0) {
  await page.evaluate(async index => {
    await ruleCollectionOwner.records[index].finish();
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, index);
}

for (const bundled of [false, true]) {
  const surface = bundled ? "bundle" : "source";
  for (const action of ["group", "move", "toggle", "duplicate", "delete"]) {
    for (const change of ["authentication ABA", "same owner"]) for (const failed of [false, true]) {
      test(`Rule ${action} retires its callback after ${change} and accepted read, failure=${failed} (${surface})`, async ({page}) => {
        const {panel, errors} = await prepare(page, bundled, action, failed);
        await dispatch(panel, action);
        await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
        await acceptNewerRead(panel, action, change);
        const accepted = await panel.evaluate(() => ruleCollectionOwner.accepted);
        if (action === "group") await panel.locator("#rule-new-group-name").fill("Later group draft after accepted read");
        else {
          await panel.getByRole("button", {name:"Create rule", exact:true}).first().click();
          await panel.locator("#rule-name").fill("Later Rule draft after accepted read");
          await panel.evaluate(host => {
            ruleCollectionOwner.dialog = host.shadowRoot.querySelector("#rule-dialog");
            ruleCollectionOwner.selector = host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector");
          });
        }
        await release(page);
        expect(await panel.evaluate(host => host._result)).toEqual(accepted);
        expect(await panel.evaluate(host => host._eocRequestRuleRevision)).toEqual(await panel.evaluate(() => ruleCollectionOwner.acceptedRevision));
        expect(await page.evaluate(() => ruleCollectionOwner.toasts)).toEqual([]);
        expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === "request_rules" && call.action === "list").length))
          .toBe(await page.evaluate(() => ruleCollectionOwner.readsBefore));
        expect(await page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
        await expect(panel.locator(action === "group" ? "#rule-new-group-name" : "#rule-name")).toBeFocused();
        if (action === "group") await expect(panel.locator("#rule-new-group-name")).toHaveValue("Later group draft after accepted read");
        else {
          await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", true);
          await expect(panel.locator("#rule-name")).toHaveValue("Later Rule draft after accepted read");
          await expect(panel.locator("#rule-save")).toBeEnabled();
          expect(await panel.evaluate(host => ruleCollectionOwner.dialog === host.shadowRoot.querySelector("#rule-dialog")
            && ruleCollectionOwner.selector === host.shadowRoot.querySelector("#rule-action-sequence-host ha-selector"))).toBe(true);
        }
        await expectHarnessClean(page, errors);
      });
    }
  }

  for (const kind of ["rule", "group"]) for (const change of ["authentication ABA", "same owner"]) {
    test(`Rule ${kind} confirmation cannot dispatch after ${change} and accepted read (${surface})`, async ({page}) => {
      const {panel, errors} = await prepare(page, bundled, kind === "rule" ? "delete" : "group");
      if (kind === "group") {
        await panel.evaluate(async host => {
          await ruleCollectionOwner.call("groups", {groups:[{name:"Confirmed old group"}]});
          host._sectionCache.clear();
          await host._loadSection(true);
        });
        await panel.locator("#rule-groups-manage").click();
        await panel.locator(".rule-group-delete").first().click();
      } else await cardFor(panel).locator(".rule-delete").click();
      await expect(panel.locator("#confirm-dialog")).toHaveJSProperty("open", true);
      await panel.evaluate(async (host, {kind, change}) => {
        if (change === "authentication ABA") {
          host.hass = {...host.hass, user:{id:"rule-collection-owner-B", is_admin:true}};
          host.hass = {...host.hass, user:{id:"rule-collection-owner-A", is_admin:true}};
        }
        const current = browserHarness.getState().requestRules;
        if (kind === "rule") await ruleCollectionOwner.call("update", {rule_id:"rule-1", rule:{...current.rules[0], name:"Fresh rule after confirmation"}});
        else await ruleCollectionOwner.call("groups", {groups:[{...current.groups[0], name:"Fresh group after confirmation"}]});
        host._sectionCache.clear();
        await host._loadSection(true);
        ruleCollectionOwner.accepted = structuredClone(host._result);
      }, {kind, change});
      await panel.locator("#confirm-accept").click();
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      expect(await page.evaluate(() => ruleCollectionOwner.records.length)).toBe(0);
      expect(await panel.evaluate(host => host._result)).toEqual(await panel.evaluate(() => ruleCollectionOwner.accepted));
      expect(await page.evaluate(() => ruleCollectionOwner.toasts)).toEqual([]);
      await expectHarnessClean(page, errors);
    });
  }

  test(`distinct Rule controls serialize normally without retiring the queued action (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, "toggle", false, true);
    const secondId = await page.evaluate(() => browserHarness.getState().requestRules.rules.find(rule => rule.id !== "rule-1").id);
    await cardFor(panel).locator(".rule-enabled").uncheck();
    await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
    await cardFor(panel, secondId).locator(".rule-enabled").uncheck();
    expect(await page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
    await release(page);
    await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(2);
    const queued = await page.evaluate(() => ({first:ruleCollectionOwner.records[0].request, second:ruleCollectionOwner.records[1].request,
      saved:browserHarness.getState().requestRules}));
    expect(queued.second.rule_id).toBe(secondId);
    expect(queued.second.rule.enabled).toBe(false);
    expect(queued.second.revision).toBeGreaterThan(queued.first.revision);
    await release(page, 1);
    await expect(cardFor(panel).locator(".rule-enabled")).not.toBeChecked();
    await expect(cardFor(panel, secondId).locator(".rule-enabled")).not.toBeChecked();
    await expect(cardFor(panel).locator(".rule-enabled")).toBeEnabled();
    await expect(cardFor(panel, secondId).locator(".rule-enabled")).toBeEnabled();
    expect(await panel.evaluate(host => host._result.rules.every(rule => !rule.enabled))).toBe(true);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules.every(rule => !rule.enabled))).toBe(true);
    await expectHarnessClean(page, errors);
  });

  test(`Rule group ACK preserves later names, row identity and text selection (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, "group");
    await panel.evaluate(async host => {
      await ruleCollectionOwner.call("groups", {groups:[{name:"Existing group"}]});
      host._sectionCache.clear();
      await host._loadSection(true);
    });
    await dispatch(panel, "group");
    await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
    const existing = panel.locator(".rule-group-row").first();
    await existing.locator(".rule-group-name").fill("Later existing group name");
    await panel.locator("#rule-new-group-name").fill("Later new group name");
    await panel.evaluate(host => {
      const dialog = host.shadowRoot.querySelector("#rule-groups-dialog");
      ruleCollectionOwner.row = dialog.querySelector(".rule-group-row");
      ruleCollectionOwner.rowInput = ruleCollectionOwner.row.querySelector(".rule-group-name");
      ruleCollectionOwner.newName = dialog.querySelector("#rule-new-group-name");
      ruleCollectionOwner.newName.setSelectionRange(6, 14);
    });
    await release(page);
    await expect(existing.locator(".rule-group-name")).toHaveValue("Later existing group name");
    await expect(panel.locator("#rule-new-group-name")).toHaveValue("Later new group name");
    await expect(panel.locator("#rule-new-group-name")).toBeFocused();
    expect(await panel.evaluate(host => {
      const dialog = host.shadowRoot.querySelector("#rule-groups-dialog");
      return {
        row:ruleCollectionOwner.row === dialog.querySelector(".rule-group-row"),
        input:ruleCollectionOwner.rowInput === dialog.querySelector(".rule-group-row .rule-group-name"),
        newName:ruleCollectionOwner.newName === dialog.querySelector("#rule-new-group-name"),
        selection:[ruleCollectionOwner.newName.selectionStart, ruleCollectionOwner.newName.selectionEnd],
      };
    })).toEqual({row:true, input:true, newName:true, selection:[6, 14]});
    expect(await page.evaluate(() => browserHarness.getState().requestRules.groups.map(group => group.name)))
      .toEqual(["Existing group", "Submitted collection group"]);
    await expectHarnessClean(page, errors);
  });

  test(`Rule ACK can advance a same-owner read made before its commit (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, "toggle", false, false, true);
    await dispatch(panel, "toggle");
    await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
    await panel.evaluate(async host => {
      host._sectionCache.clear();
      await host._loadSection(true);
    });
    const beforeCommitRevision = await panel.evaluate(host => host._result.revision);
    await release(page);
    const saved = await page.evaluate(() => browserHarness.getState().requestRules);
    expect(saved.revision).toBeGreaterThan(beforeCommitRevision);
    expect(await panel.evaluate(host => host._result.revision)).toBe(saved.revision);
    expect(await panel.evaluate(host => host._result.rules[0].enabled)).toBe(false);
    await expect(cardFor(panel).locator(".rule-enabled")).not.toBeChecked();
    // The editor carries enabled state from the accepted collection. A stale
    // projection here would silently re-enable this Rule on its next full save.
    await cardFor(panel).locator(".rule-edit").click();
    await panel.locator("#rule-name").fill("Edited after acknowledged toggle");
    await panel.locator("#rule-save").click();
    await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(2);
    const update = await page.evaluate(() => ruleCollectionOwner.records[1].request);
    expect(update.rule.enabled).toBe(false);
    expect(update.revision).toBe(saved.revision);
    await release(page, 1);
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open", false);
    expect(await page.evaluate(() => browserHarness.getState().requestRules.rules[0].enabled)).toBe(false);
    await expectHarnessClean(page, errors);
  });

  test(`queued Rule actions survive an earlier ACK replacing their source card (${surface})`, async ({page}) => {
    const {panel, errors} = await prepare(page, bundled, "toggle-duplicate");
    await cardFor(panel).locator(".rule-enabled").uncheck();
    await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
    await cardFor(panel).locator(".rule-duplicate").click();
    expect(await page.evaluate(() => ruleCollectionOwner.records.length)).toBe(1);
    await release(page);
    await expect.poll(() => page.evaluate(() => ruleCollectionOwner.records.length)).toBe(2);
    expect(await page.evaluate(() => ruleCollectionOwner.records[1].request.action)).toBe("duplicate");
    await release(page, 1);
    const saved = await page.evaluate(() => browserHarness.getState().requestRules);
    expect(saved.rules).toHaveLength(2);
    expect(await panel.evaluate(host => host._result.rules)).toEqual(saved.rules);
    expect(await panel.evaluate(host => host._result.revision)).toBe(saved.revision);
    await expect(panel.locator(".request-rule-card")).toHaveCount(2);
    expect(await page.evaluate(() => ruleCollectionOwner.toasts.some(([message, failed]) => failed))).toBe(false);
    await expectHarnessClean(page, errors);
  });
}
