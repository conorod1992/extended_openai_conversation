import {expect, test} from "@playwright/test";
import {fixtureUrl, expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";

async function replacePanel(panel) {
  await panel.evaluate(host => {
    const parent=host.parentElement, hass=host.hass;
    host.remove();
    const next=document.createElement("extended-openai-management-panel");
    parent.append(next); next.hass=hass;
  });
  await expect.poll(() => panel.evaluate(host => !host._busy && Boolean(host._configData?.config))).toBe(true);
}

const input = (panel, key) => panel.locator(`[data-config="${key}"]`);
async function edit(control, value) {
  if (await control.evaluate(node => node.tagName === "SELECT")) {
    expect(await control.locator("option").evaluateAll(options => options.map(option => option.value))).toContain(String(value));
    await control.selectOption(String(value));
  }
  else await control.fill(String(value));
}

// A bounded cross-product of state transitions, with literal expected authored
// and persisted values. Only the transport is controlled; every UI binding,
// save/read merge, navigation guard and disconnectedCallback is production code.
for (const bundled of [false,true]) for (const retention of [false,true])
  for (const readOutcome of ["success","failure"]) for (const relation of ["disjoint","equal","conflict"]) {
    test(`ownership matrix: ${retention ? "Retention" : "Basics"}, read=${readOutcome}, edit=${relation} (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const errors=trackPageErrors(page);
      await page.goto(fixtureUrl("assistant/basics", `&agents=2${bundled ? "&bundle=1" : ""}`));
      const panel=page.locator("extended-openai-management-panel");
      await expect(input(panel,"max_tokens")).toHaveValue("1200");
      if (retention) {
        // The small fixture normally lists only 7/30. Use supported production
        // retention values so submitted, later and conflicting choices differ.
        await page.evaluate(() => {
          const state=browserHarness.getState();
          for (const key of ["usage_request_retention_days","usage_run_retention_days"]) state.configuration.options[key]=[0,7,30,90].map(value => ({value,label:`${value} days`}));
          localStorage.setItem("extended-openai-browser-harness-state-v3",JSON.stringify(state));
        });
        await page.goto(fixtureUrl("assistant/basics", `&agents=2${bundled ? "&bundle=1" : ""}`));
        await expect(input(panel,"max_tokens")).toHaveValue("1200");
      }
      await panel.evaluate(host => {
        host.hass={...host.hass, user:{id:"admin", is_admin:true}};
        window.matrixOriginalConfig=structuredClone(host._configData.config);
      });
      if (retention) {
        await panel.locator("#agent").selectOption("scale-agent-1");
        await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
        await panel.locator("#agent").selectOption("agent-1");
        await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "agent-1")).toBe(true);
        await panel.evaluate(host => host._navigate("usage-maintenance","retention"));
      }
      const primary=retention ? "usage_request_retention_days" : "max_tokens";
      const secondary=retention ? "usage_run_retention_days" : "__title";
      const submitted=retention ? 7 : 1400, later=retention ? 0 : 1500, conflicting=retention ? 90 : 1600;
      await edit(input(panel,primary),submitted);
      await panel.evaluate(host => {
        const original=host.hass.callWS.bind(host.hass), state={writes:[], entered:false, refreshEntered:false, failedReads:0};
        window.ownershipMatrix=state;
        host.hass.callWS=async request => {
          if (request.section === "configuration" && request.action === "save") {
            state.writes.push(structuredClone(request));
            if (state.writes.length === 1) {
              state.entered=true;
              await new Promise(resolve => {state.releaseWrite=resolve;});
            }
          }
          if (request.section === "configuration" && request.action === "get" && state.holdRefresh) {
            state.holdRefresh=false; state.refreshEntered=true;
            await new Promise(resolve => {state.releaseRead=resolve;});
            if (state.failRead) {state.failedReads++; throw new Error("Controlled ownership matrix read failure");}
          }
          return original(request);
        };
        state.source=host;
      });
      await panel.locator("#save-config").click();
      await expect.poll(() => page.evaluate(() => ownershipMatrix.entered)).toBe(true);
      await edit(input(panel,primary),later);
      await replacePanel(panel);
      if (relation === "equal") await replacePanel(panel);
      const picker=panel.locator("#agent");
      if (await picker.isEnabled()) await picker.selectOption("scale-agent-1");
      await expect(picker).toHaveValue("agent-1");
      expect(await panel.evaluate(host => host._agentId)).toBe("agent-1");
      await page.evaluate(readOutcome => {
        ownershipMatrix.failRead=readOutcome === "failure";
        ownershipMatrix.holdRefresh=true;
        ownershipMatrix.releaseWrite();
      },readOutcome);
      await expect.poll(() => page.evaluate(() => ownershipMatrix.refreshEntered)).toBe(true);
      if (relation === "disjoint") await edit(input(panel,secondary),retention ? 7 : "Current target title");
      else await edit(input(panel,primary),relation === "equal" ? later : conflicting);
      await page.evaluate(() => ownershipMatrix.releaseRead());
      if (readOutcome === "failure") {
        await expect.poll(() => page.evaluate(() => ownershipMatrix.failedReads)).toBe(1);
        expect(await page.evaluate(() => ownershipMatrix.source._configDirty)).toBe(true);
        // A new visit retries certification. Neither visit may replay the save.
        await panel.evaluate(host => host._navigate("guide"));
        await page.goBack();
      }
      if (relation === "conflict") {
        const copy=panel.getByRole("heading",{name:"Recovered unsaved configuration"});
        await expect(copy).toBeVisible();
        expect(JSON.parse(await panel.getByRole("textbox", {name:"Recovered configuration values", exact:true}).inputValue()).config[primary]).toBe(later);
        expect(await panel.evaluate((host,key) => host._draft[key],primary)).toBe(conflicting);
        await panel.getByRole("button",{name:"Discard recovered edits"}).click();
        await expect(copy).toHaveCount(0);
      }
      await expect.poll(() => page.evaluate(() => Boolean(ownershipMatrix.source._configDirty || ownershipMatrix.source._eocAcknowledgedConfiguration))).toBe(false);
      await expect(input(panel,primary)).toHaveValue(String(relation === "conflict" ? conflicting : later));
      if (relation === "disjoint") await expect(input(panel,secondary)).toHaveValue(retention ? "7" : "Current target title");
      expect(await page.evaluate(() => ownershipMatrix.writes.length)).toBe(1);
      const expected={ [primary]:relation === "conflict" ? conflicting : later,
        ...(retention && relation === "disjoint" ? {usage_run_retention_days:7} : {}) };
      await panel.locator("#save-config").click();
      if (retention && expected[primary] === 0) {
        await expect(panel.locator("#confirm-dialog")).toBeVisible();
        await expect(panel.locator("#confirm-message")).toContainText("request details");
        await panel.locator("#confirm-accept").click();
      }
      await expect.poll(() => panel.evaluate(host => host._configurationSaving || host._configDirty)).toBe(false);
      const writes=await page.evaluate(() => ownershipMatrix.writes);
      expect(writes).toHaveLength(2);
      expect(writes[1].config).toEqual(expected);
      if (!retention && relation === "disjoint") expect(writes[1].title).toBe("Current target title");
      else expect(Object.hasOwn(writes[1],"title")).toBe(false);
      const persisted=await page.evaluate(() => browserHarness.getState().configuration);
      expect(persisted.config).toEqual({...await page.evaluate(() => matrixOriginalConfig), ...expected});
      // Navigation and Back retain a clean saved baseline; an actual A -> B -> A
      // switch then verifies that no retired retained source is resurrected.
      await panel.evaluate(host => host._navigate("guide"));
      await page.goBack();
      await expect(input(panel,primary)).toHaveValue(String(expected[primary]));
      await panel.locator("#agent").selectOption("scale-agent-1");
      await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
      await panel.locator("#agent").selectOption("agent-1");
      await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "agent-1")).toBe(true);
      await expect(input(panel,primary)).toHaveValue(String(expected[primary]));
      await replacePanel(panel);
      await expect(input(panel,primary)).toHaveValue(String(expected[primary]));
      expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(false);
      expect(await page.evaluate(() => ownershipMatrix.writes.length)).toBe(2);
      await expectHarnessClean(page,errors);
    });
  }
