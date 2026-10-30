import {expect, test} from "@playwright/test";
import {expectHarnessClean, fixtureUrl, trackPageErrors} from "./browser-helpers.mjs";

async function holdDuplicateCatalog(page, panel, holdWrite = false) {
  await panel.evaluate((host, holdWrite) => {
    host.hass={...host.hass,user:{id:"admin",is_admin:true}};
    const original=host.hass.callWS.bind(host.hass);
    window.catalogSelection={source:host,creates:0,held:false};
    host.hass.callWS=async request => {
      if(request.section === "configuration" && request.action === "duplicate") {
        catalogSelection.creates++;
        if(holdWrite) {
          catalogSelection.writeEntered=true;
          await new Promise(resolve => {catalogSelection.releaseWrite=resolve;});
        }
        return {subentry_id:"copy-agent",title:"Copy agent"};
      }
      const result=await original(request);
      if(catalogSelection.creates && request.action === "agents" && !request.section) {
        result.agents.push({...result.agents[0],subentry_id:"copy-agent",title:"Copy agent"});
        if(!catalogSelection.held) {
          catalogSelection.held=true;
          await new Promise(resolve => {catalogSelection.release=resolve;});
          if(catalogSelection.reject) throw new Error("Controlled catalog refresh failure");
          if(catalogSelection.revoke) result.is_admin=false;
          if(catalogSelection.removeSelected) result.agents=result.agents.filter(item => item.subentry_id !== "scale-agent-1");
        }
      }
      if(request.section === "configuration" && request.action === "get" && request.subentry_id === "copy-agent") {
        result.title="Copy agent"; result.revision="copy-r1";
      }
      return result;
    };
  },holdWrite);
  await panel.locator(".agent-actions-menu > summary").click();
  await panel.locator("#duplicate-agent").click();
  await expect.poll(() => page.evaluate(holdWrite => holdWrite ? catalogSelection.writeEntered : catalogSelection.held,holdWrite)).toBe(true);
}

async function releaseCatalog(page) {
  await page.evaluate(() => catalogSelection.release());
  await expect.poll(() => page.evaluate(() => catalogSelection.source._eocAssistantDuplicatePending || false)).toBe(false);
}

async function replacePanel(panel) {
  await panel.evaluate(host => {
    const parent=host.parentElement,hass=host.hass; host.remove();
    const next=document.createElement("extended-openai-management-panel");
    parent.append(next); next.hass=hass;
  });
  await expect.poll(() => panel.evaluate(host => !host._busy && Boolean(host._configData?.config))).toBe(true);
}

async function seedRetainedAdminScope(panel) {
  await panel.evaluate(host => {
    const scopes=[...host._data.scopes,{scope_id:"__anonymous__",scope_type:"legacy",display_name:"Legacy anonymous"}];
    host._data.scopes=scopes; host._scopeId="__anonymous__";
    const key=`${host._agentId}|scopes|memory`;
    host._scopeCatalogCache.set(key,scopes); host._eocScopeCatalogTimes.set(key,Date.now());
  });
}

for(const bundled of [false,true]) {
  for(const editDuringWrite of [false,true]) {
    test(`duplicate selection owns the original write intent, edit=${editDuringWrite} (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const errors=trackPageErrors(page);
      await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
      const panel=page.locator("extended-openai-management-panel"),tokens=panel.locator('[data-config="max_tokens"]');
      await expect(tokens).toHaveValue("1200");
      await holdDuplicateCatalog(page,panel,true);
      let laterEdit=false;
      if(editDuringWrite) {
        if(await tokens.isEditable()) {await tokens.fill("1777"); await tokens.press("Tab"); laterEdit=true;}
      } else if(await panel.locator("#agent").isEnabled()) {
        await panel.locator("#agent").selectOption("scale-agent-1");
        await expect.poll(() => panel.evaluate(host => !host._busy)).toBe(true);
      }
      const currentAgent=await panel.evaluate(host => host._agentId);
      await page.evaluate(() => catalogSelection.releaseWrite());
      await expect.poll(() => page.evaluate(() => catalogSelection.held)).toBe(true);
      await releaseCatalog(page);
      await expect(panel.locator("#agent")).toHaveValue(laterEdit ? "agent-1" : currentAgent === "scale-agent-1" ? currentAgent : "copy-agent");
      await expect(tokens).toHaveValue(laterEdit ? "1777" : "1200");
      expect(await page.evaluate(() => catalogSelection.creates)).toBe(1);
      await expectHarnessClean(page,errors);
    });
  }

  for(const history of ["clean","edit","switch","switch-edit"]) {
    test(`duplicate catalog respects later authoring: ${history} (${bundled ? "bundle" : "source"})`, async ({page}) => {
      const errors=trackPageErrors(page);
      await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
      const panel=page.locator("extended-openai-management-panel");
      const tokens=panel.locator('[data-config="max_tokens"]');
      await expect(tokens).toHaveValue("1200");
      await holdDuplicateCatalog(page,panel);
      if(history.startsWith("switch")) {
        await panel.locator("#agent").selectOption("scale-agent-1");
        await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
      }
      if(history.endsWith("edit")) {
        await tokens.fill("1777"); await tokens.press("Tab");
      }
      await releaseCatalog(page);
      const expectedAgent=history === "clean" ? "copy-agent" : history.startsWith("switch") ? "scale-agent-1" : "agent-1";
      await expect(panel.locator("#agent")).toHaveValue(expectedAgent);
      await expect(tokens).toHaveValue(history.endsWith("edit") ? "1777" : "1200");
      expect(await panel.evaluate(host => host._configDirty)).toBe(history.endsWith("edit"));
      // Preserving the later selection must not make the completed copy
      // inaccessible from the assistant catalogue.
      await expect(panel.locator('#agent option[value="copy-agent"]')).toHaveCount(1);
      expect(await page.evaluate(() => catalogSelection.creates)).toBe(1);
      await expectHarnessClean(page,errors);
    });
  }

  test(`detached duplicate catalog cannot redirect the next reconnect (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    const tokens=panel.locator('[data-config="max_tokens"]');
    await expect(tokens).toHaveValue("1200");
    await holdDuplicateCatalog(page,panel);
    await tokens.fill("1777"); await tokens.press("Tab");
    await replacePanel(panel);
    await expect(tokens).toHaveValue("1777");
    await releaseCatalog(page);
    await replacePanel(panel);
    await expect(panel.locator("#agent")).toHaveValue("agent-1");
    await expect(tokens).toHaveValue("1777");
    expect(await page.evaluate(() => catalogSelection.creates)).toBe(1);
    await expectHarnessClean(page,errors);
  });

  test(`duplicate catalog cannot retire a later Request Rule editor (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    await holdDuplicateCatalog(page,panel);
    await panel.evaluate(host => host._navigate("capabilities","request-rules"));
    await panel.getByRole("button",{name:"Create rule",exact:true}).first().click();
    await panel.locator("#rule-name").fill("Later rule authoring");
    await releaseCatalog(page);
    await expect(panel.locator("#agent")).toHaveValue("agent-1");
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open",true);
    await expect(panel.locator("#rule-name")).toHaveValue("Later rule authoring");
    expect(await page.evaluate(() => catalogSelection.creates)).toBe(1);
    await expectHarnessClean(page,errors);
  });

  test(`obsolete failed catalog cannot close a later Request Rule editor (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    await holdDuplicateCatalog(page,panel);
    await panel.evaluate(host => host._navigate("capabilities","request-rules"));
    await panel.getByRole("button",{name:"Create rule",exact:true}).first().click();
    await panel.locator("#rule-name").fill("Rule after catalog request");
    await page.evaluate(() => {catalogSelection.reject=true;});
    await releaseCatalog(page);
    await expect(panel.locator("#rule-dialog")).toHaveJSProperty("open",true);
    await expect(panel.locator("#rule-name")).toHaveValue("Rule after catalog request");
    expect(await panel.evaluate(host => host._error || null)).toBe(null);
    expect(await page.evaluate(() => catalogSelection.creates)).toBe(1);
    await expectHarnessClean(page,errors);
  });

  test(`new-agent import keeps the current assistant's dirty configuration (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel"),tokens=panel.locator('[data-config="max_tokens"]');
    await expect(tokens).toHaveValue("1200");
    await panel.evaluate(host => {
      const original=host.hass.callWS.bind(host.hass);
      window.importCatalog={writes:0};
      host.hass.callWS=async request => {
        if(request.section === "configuration" && request.action === "import_preview") {
          return {title:"Imported agent",summary:{model:"gpt-4o",tools:0,function_groups:0,speech_rules:0}};
        }
        if(request.section === "configuration" && request.action === "import") {
          if(request.mode !== "new") throw new Error("Fixture only permits a new-agent import");
          importCatalog.writes++;
          return {status:"created",subentry_id:"imported-agent",title:"Imported agent"};
        }
        const result=await original(request);
        if(importCatalog.writes && request.action === "agents" && !request.section) result.agents.push({...result.agents[0],subentry_id:"imported-agent",title:"Imported agent"});
        return result;
      };
    });
    await tokens.fill("1777"); await tokens.press("Tab");
    await panel.locator(".agent-actions-menu > summary").click();
    await panel.locator("#import-agent").click();
    await panel.locator("#import-document").fill(JSON.stringify({title:"Imported agent",config:{functions:[]}}));
    await panel.locator("#import-preview").click();
    await expect(panel.locator("#import-apply")).toBeEnabled();
    await panel.locator("#import-apply").click();
    await expect(panel.locator("#import-dialog")).toHaveJSProperty("open",false);
    await expect.poll(() => panel.evaluate(host => !host._busy)).toBe(true);
    await expect(panel.locator("#agent")).toHaveValue("agent-1");
    await expect(tokens).toHaveValue("1777");
    expect(await panel.evaluate(host => host._configDirty)).toBe(true);
    await expect(panel.locator('#agent option[value="imported-agent"]')).toHaveCount(1);
    expect(await page.evaluate(() => importCatalog.writes)).toBe(1);
    await expectHarnessClean(page,errors);
  });

  test(`catalog permission downgrade preserves the owner but retires writable configuration (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel"),tokens=panel.locator('[data-config="max_tokens"]');
    await expect(tokens).toHaveValue("1200");
    await holdDuplicateCatalog(page,panel);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
    await tokens.fill("1777"); await tokens.press("Tab");
    await seedRetainedAdminScope(panel);
    await page.evaluate(() => {catalogSelection.revoke=true;});
    await releaseCatalog(page);
    await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
    await expect(tokens).toHaveCount(0);
    await expect(panel.locator("#save-config")).toHaveCount(0);
    expect(await panel.evaluate(host => ({tokens:host._draft.max_tokens,dirty:host._configDirty,admin:host._data.is_admin})))
      .toEqual({tokens:1777,dirty:true,admin:false});
    expect(await panel.evaluate(host => ({scope:host._scopeId,cache:host._scopeCatalogCache.size,times:host._eocScopeCatalogTimes.size})))
      .toEqual({scope:"user:test-user",cache:0,times:0});
    expect(await page.evaluate(() => catalogSelection.creates)).toBe(1);
    await expectHarnessClean(page,errors);
  });

  test(`current catalog refresh keeps the mounted assistant before another panel's preference (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    await page.evaluate(() => localStorage.setItem("extended-openai-agent","scale-agent-1"));
    await panel.evaluate(host => host._loadAgents());
    await expect(panel.locator("#agent")).toHaveValue("agent-1");
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    expect(await panel.evaluate(host => host._configDirty)).toBe(false);
    await expectHarnessClean(page,errors);
  });

  test(`old catalog membership cannot regress a later assistant save (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator('[data-config="max_tokens"]')).toHaveValue("1200");
    await holdDuplicateCatalog(page,panel);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
    await panel.locator('[data-config="__title"]').fill("B saved after catalog started");
    await panel.locator('[data-config="chat_model"]').fill("gpt-4.1");
    await panel.locator('[data-config="chat_model"]').press("Tab");
    await expect.poll(() => panel.evaluate(host => !host._eocPendingModelPreparation)).toBe(true);
    await panel.locator("#save-config").click();
    await expect.poll(() => panel.evaluate(host => !host._configurationSaving && !host._configDirty)).toBe(true);
    const expected={title:"B saved after catalog started",model:"gpt-4.1"};
    expect(await panel.evaluate(host => ({title:host._selectedAgent().title,model:host._selectedAgent().model}))).toEqual(expected);
    await releaseCatalog(page);
    await expect(panel.locator("#agent")).toHaveValue("scale-agent-1");
    expect(await panel.evaluate(host => ({title:host._selectedAgent().title,model:host._selectedAgent().model}))).toEqual(expected);
    await expect(panel.locator('#agent option[value="copy-agent"]')).toHaveCount(1);
    await expectHarnessClean(page,errors);
  });

  test(`missing authored owner cannot suppress a catalog permission downgrade (${bundled ? "bundle" : "source"})`, async ({page}) => {
    const errors=trackPageErrors(page);
    await page.goto(fixtureUrl("assistant/basics",`&agents=2${bundled ? "&bundle=1" : ""}`));
    const panel=page.locator("extended-openai-management-panel"),tokens=panel.locator('[data-config="max_tokens"]');
    await expect(tokens).toHaveValue("1200");
    await holdDuplicateCatalog(page,panel);
    await panel.locator("#agent").selectOption("scale-agent-1");
    await expect.poll(() => panel.evaluate(host => !host._busy && host._agentId === "scale-agent-1")).toBe(true);
    await tokens.fill("1777"); await tokens.press("Tab");
    await seedRetainedAdminScope(panel);
    await page.evaluate(() => {catalogSelection.revoke=true;catalogSelection.removeSelected=true;});
    await releaseCatalog(page);
    await expect(tokens).toHaveCount(0);
    await expect(panel.locator("#save-config")).toHaveCount(0);
    expect(await panel.evaluate(host => ({tokens:host._draft.max_tokens,dirty:host._configDirty,admin:host._data.is_admin,
      agent:host._agentId,entry:host._selectedAgent()?.entry_id})))
      .toEqual({tokens:1777,dirty:true,admin:false,agent:"scale-agent-1",entry:"scale-entry-1"});
    expect(await panel.evaluate(host => ({scope:host._scopeId,cache:host._scopeCatalogCache.size,times:host._eocScopeCatalogTimes.size})))
      .toEqual({scope:"user:test-user",cache:0,times:0});
    await expectHarnessClean(page,errors);
  });
}
