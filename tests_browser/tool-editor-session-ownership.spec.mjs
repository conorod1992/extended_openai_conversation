import {expect, test} from "@playwright/test";
import {fixtureUrl, trackPageErrors, expectHarnessClean} from "./browser-helpers.mjs";

const repairedTool = {
  spec:{name:"session_repair",description:"Repair ownership",parameters:{type:"object",properties:{message:{type:"string",minLength:1}}}},
  function:{type:"template",value_template:"{{ message | default('Hello') }}"},
};

async function prepare(page, bundled, repair = false) {
  await page.goto(fixtureUrl("capabilities/functions", bundled ? "&bundle=1" : ""));
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("#function-add")).toBeVisible();
  await panel.evaluate(host => {host.hass={...host.hass,user:{id:"editor-owner",is_admin:true}};});
  await expect.poll(() => panel.evaluate(host => !host._eocCatalogBootstrap?.pending)).toBe(true);
  await panel.evaluate((host, {repair, repairedTool}) => {
    const original=host.hass.callWS.bind(host.hass), baseline=structuredClone(host._configData);
    const state={hold:null,held:[],calls:[],finished:0};
    window.toolSessionOwnership=state;
    host.hass.callWS=async request => {
      state.calls.push(structuredClone(request));
      const held=request.section === "tools" && (
        (state.hold === "load" && request.action === "serialize")
        || (state.hold === "validation" && request.action === "validate_yaml")
        || (state.hold === "catalogue" && request.action === "built_in_catalog"));
      const response=async () => {
        if(repair && request.action === "validate_yaml") return {valid:true,config:structuredClone(repairedTool),name:repairedTool.spec.name,type:"template"};
        if(repair && request.section === "function_repair" && request.action === "save_one") {
          return {valid:true,...structuredClone(baseline),revision:"unexpected-repair-save",function_repair:null};
        }
        return original(request);
      };
      if(!held)return response();
      return new Promise((resolve,reject) => state.held.push({request,finish:async failed => {
        try {if(failed)reject(Error("Retired editor request failed"));else resolve(await response());}
        finally {state.finished++;}
      }}));
    };
    if(repair) {
      const broken=structuredClone(repairedTool);broken.spec.parameters.properties.message.minLength="1";
      const error="minLength must be a non-negative integer";
      const snapshot={...structuredClone(baseline),function_repair:{invalid_tools:[{index:0,name:broken.spec.name,tool:broken,yaml:JSON.stringify(broken,null,2),validation_error:error}],
        invalid_count:1,group_issues:[],persisted_groups:baseline.config.function_groups,tools_valid:false,isolatable:true,validation_error:error}};
      host._selectedAgent().configuration_issue={field:"functions",repairable:true,message:error};
      host._configData=snapshot;host._result=snapshot;host._draft=structuredClone(snapshot.config);host._render();
    }
  }, {repair,repairedTool});
  return panel;
}

async function retire(panel, change) {
  await panel.evaluate((host, change) => {
    if(change === "user-roundtrip") {
      const user=host.hass.user;
      host.hass={...host.hass,user:{id:"another-editor-owner",is_admin:true}};
      host.hass={...host.hass,user};
    } else if(change === "entry") host._selectedAgent().entry_id="another-provider-entry";
    else if(change === "session") host._clearConfigDraft();
    else if(change === "dialog") {
      const dialog=host.shadowRoot.querySelector("#tool-dialog"), next=dialog.cloneNode(true);
      dialog.replaceWith(next);next.open=true;
      next.querySelector("#tool-yaml").value="Replacement editor YAML";
    }
  }, change);
}

async function finish(page, failed = false) {
  await page.evaluate(async failed => {
    const state=toolSessionOwnership;
    await Promise.all(state.held.map(held => held.finish(failed)));
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }, failed);
}

async function openHealthy(panel) {
  await panel.getByText("Show member functions",{exact:true}).click();
  await panel.locator(".edit-tool").first().click();
}

for(const bundled of [false,true]) {
  const version=bundled ? "bundle" : "source";
  for(const change of ["user-roundtrip","entry","session","dialog"]) {
    test(`Tool YAML loading cannot publish after ${change} (${version})`, async ({page}) => {
      const errors=trackPageErrors(page), panel=await prepare(page,bundled);
      await page.evaluate(() => {toolSessionOwnership.hold="load";});
      await openHealthy(panel);
      await expect.poll(() => page.evaluate(() => toolSessionOwnership.held.length)).toBe(1);
      await retire(panel,change);
      const before=await panel.evaluate(host => ({yaml:host.shadowRoot.querySelector("#tool-yaml").value,
        status:host.shadowRoot.querySelector("#tool-error").textContent,disabled:host.shadowRoot.querySelector("#tool-save").disabled}));
      await finish(page);
      expect(await panel.evaluate(host => ({yaml:host.shadowRoot.querySelector("#tool-yaml").value,
        status:host.shadowRoot.querySelector("#tool-error").textContent,disabled:host.shadowRoot.querySelector("#tool-save").disabled}))).toEqual(before);
      await expectHarnessClean(page,errors);
    });
  }

  test(`Tool YAML loading failure cannot replace current diagnostics after a user roundtrip (${version})`, async ({page}) => {
    const errors=trackPageErrors(page), panel=await prepare(page,bundled);
    await page.evaluate(() => {toolSessionOwnership.hold="load";});await openHealthy(panel);
    await expect.poll(() => page.evaluate(() => toolSessionOwnership.held.length)).toBe(1);
    await retire(panel,"user-roundtrip");
    await panel.evaluate(host => {host.shadowRoot.querySelector("#tool-error").textContent="Current owner diagnostics";});
    await finish(page,true);
    await expect(panel.locator("#tool-error")).toHaveText("Current owner diagnostics");
    await expectHarnessClean(page,errors);
  });

  for(const change of ["user-roundtrip","entry","session"]) {
    test(`Tool Validate ignores retired ${change} diagnostics (${version})`, async ({page}) => {
      const errors=trackPageErrors(page), panel=await prepare(page,bundled);
      await openHealthy(panel);await expect(panel.locator("#tool-save")).toBeEnabled();
      await page.evaluate(() => {toolSessionOwnership.hold="validation";});
      await panel.locator("#tool-validate").click();
      await expect.poll(() => page.evaluate(() => toolSessionOwnership.held.length)).toBe(1);
      await retire(panel,change);
      await panel.evaluate(host => {host.shadowRoot.querySelector("#tool-error").textContent="Current owner diagnostics";});
      await finish(page);
      await expect(panel.locator("#tool-error")).toHaveText("Current owner diagnostics");
      await expectHarnessClean(page,errors);
    });
  }

  for(const change of ["user-roundtrip","session"]) {
    test(`Tool repair validation cannot authorize a write after ${change} (${version})`, async ({page}) => {
      const errors=trackPageErrors(page), panel=await prepare(page,bundled,true);
      await panel.locator(".edit-invalid-tool").click();
      await panel.evaluate(async (host, tool) => {
        const {getToolYamlEditor}=await import("/custom_components/extended_openai_conversation_responses/frontend/tool-yaml-editor-adapter.js");
        getToolYamlEditor(host).setYaml(JSON.stringify(tool,null,2));
      }, repairedTool);
      await page.evaluate(() => {toolSessionOwnership.hold="validation";});
      await panel.locator("#tool-save").click();
      await expect.poll(() => page.evaluate(() => toolSessionOwnership.held.length)).toBe(1);
      await retire(panel,change);await finish(page);
      expect(await page.evaluate(() => toolSessionOwnership.calls.filter(call => call.section === "function_repair" && call.action === "save_one").length)).toBe(0);
      await expectHarnessClean(page,errors);
    });
  }

  test(`Repair preset catalogue cannot bind a retired user roundtrip (${version})`, async ({page}) => {
    const errors=trackPageErrors(page), panel=await prepare(page,bundled,true);
    await page.evaluate(() => {toolSessionOwnership.hold="catalogue";});
    await panel.locator(".edit-invalid-tool").click();
    await expect.poll(() => page.evaluate(() => toolSessionOwnership.held.length)).toBe(1);
    await retire(panel,"user-roundtrip");
    await panel.evaluate(host => {host.shadowRoot.querySelector("#built-in-function").innerHTML='<option value="current" selected>Current owner preset</option>';});
    await finish(page);
    await expect(panel.locator("#built-in-function")).toHaveValue("current");
    await expectHarnessClean(page,errors);
  });
}
