import {expect,test} from "@playwright/test";
import {fixtureUrl,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

const baseline={office:"user:owner",living:"shared:household"};
async function setup(page, bundled = false) {
  await page.goto(fixtureUrl("assistant/basics", bundled ? "&bundle=1" : ""));
  await page.waitForFunction(()=>browserHarness?.panel?._configData?.revision);
  await page.evaluate(async()=>{
    const h=browserHarness,p=h.panel,s=h.getState().configuration;
    h.hass.user={id:"voice-admin"};
    await h.hass.callWS({type:"extended_openai_conversation_responses/management",section:"configuration",action:"save",entry_id:"entry-1",subentry_id:"agent-1",revision:s.revision,title:s.title,config:{...s.config,voice_scope_policy:"device_mapping",voice_device_mappings:{office:"user:owner",living:"shared:household"}}});
    const send=h.hass.callWS.bind(h.hass);
    h.voiceRequests=[];h.voiceMode="success";
    h.hass.callWS=async message=>{
      if(message.type==="config/entity_registry/list") return ["office","living"].map(id=>({entity_id:`assist_satellite.${id}`,device_id:id}));
      if(message.type==="config/entity_registry/get") {
        h.voiceRequests.push(message.entity_id);
        if(h.voiceMode==="failed") throw Error("Entity registry unavailable");
        if(h.voiceMode==="pending") return new Promise(resolve=>{h.releaseVoice=resolve;});
        return {entity_id:message.entity_id,device_id:message.entity_id.split(".")[1]};
      }
      return send(message);
    };
    p._invalidateAfterMutation(p._agentId,"configuration","save");p._clearConfigDraft();await p._navigate("assistant","voice");
  });
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  return panel;
}
async function select(panel,row,entity) {
  await panel.locator(".voice-satellite-picker").nth(row).evaluate((picker,value)=>{
    picker.value=value;picker.dispatchEvent(new CustomEvent("value-changed",{detail:{value},bubbles:true,composed:true}));
  },entity);
}
async function navigate(panel,section) {await panel.evaluate((p,section)=>p._navigate("assistant",section),section);}
async function remount(page) {
  await page.evaluate(async()=>{
    const h=browserHarness,old=h.panel;old.remove();
    const p=document.createElement("extended-openai-management-panel");p.hass=h.hass;p.route={path:"/extended-openai/assistant/voice"};
    document.body.append(p);h.panel=p;
  });
  const panel=page.locator("extended-openai-management-panel");
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  return panel;
}

test("duplicate Voice rows survive route changes and remount until corrected without losing another mapping",async({page})=>{
  const errors=trackPageErrors(page);let panel=await setup(page);
  await select(panel,0,"assist_satellite.living");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("more than once");
  expect(await panel.evaluate(p=>p._configDirty)).toBe(true);
  expect(await panel.evaluate(p=>p._draft.voice_device_mappings)).toEqual(baseline);
  await navigate(panel,"basics");
  const before=await page.evaluate(()=>browserHarness.calls.filter(c=>c.section==="configuration"&&c.action==="save").length);
  await panel.locator("#save-config").click();
  expect(await page.evaluate(()=>browserHarness.calls.filter(c=>c.section==="configuration"&&c.action==="save").length)).toBe(before);
  await navigate(panel,"voice");panel=await remount(page);
  await expect(panel.locator(".voice-device-id").nth(0)).toHaveValue("living");
  await expect(panel.locator(".voice-device-id").nth(1)).toHaveValue("living");
  expect(await panel.evaluate(p=>p._configDirty)).toBe(true);
  await select(panel,0,"assist_satellite.office");
  await expect.poll(()=>panel.evaluate(p=>p._configDirty)).toBe(false);
  await panel.locator(".voice-owner-type").nth(0).selectOption("unretained");
  await panel.locator("#save-config").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({office:"unretained",living:"shared:household"});
  await expectHarnessClean(page,errors);
});

for (const bundled of [false, true]) test(`off-route deferred Voice recovery after Discard saves the displayed mapping; bundle=${bundled}`, async ({page}) => {
  const errors = trackPageErrors(page), source = await setup(page, bundled);
  await source.locator(".voice-owner-type").nth(0).selectOption("unretained");
  await source.evaluate(host => {
    window.deferredVoiceSource = host;
    const target = document.createElement("extended-openai-management-panel");
    target.id = "voice-destination";
    target.hass = host.hass;
    document.body.append(target);
    browserHarness.panel = target;
  });
  const target = page.locator("#voice-destination");
  await expect(target.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await target.locator("#add-voice-mapping").click();
  await expect(target.locator("[data-voice-mapping-row]")).toHaveCount(3);
  await expect(target.locator('[data-error="voice_device_mappings"]')).toContainText("empty assignment");
  // On Voice, competing rows now require explicit review (covered by
  // voice-competing-recovery.spec.mjs). Keep this positive control for global
  // Discard before that review, including Save of the subsequently shown map.
  await navigate(target, "basics");
  await target.evaluate(async host => { deferredVoiceSource.remove(); await host._loadSection(true); });
  await expect.poll(() => page.evaluate(() => deferredVoiceSource._configDirty)).toBe(false);
  await target.locator("#revert-config").click();
  await target.evaluate(host => host._loadSection(true));
  await navigate(target, "voice");
  await expect(target.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await expect(target.locator(".voice-owner-type").nth(0)).toHaveValue("unretained");
  expect(await target.evaluate(host => host._draft.voice_device_mappings)).toEqual({...baseline, office:"unretained"});
  await target.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({...baseline, office:"unretained"});
  await expect.poll(() => target.evaluate(host => host._configDirty)).toBe(false);
  await expectHarnessClean(page, errors);
});

for (const bundled of [false, true]) test(`already committed Voice recovery stays clean and editable; bundle=${bundled}`, async ({page}) => {
  const errors = trackPageErrors(page), source = await setup(page, bundled);
  await source.locator(".voice-owner-type").nth(0).selectOption("unretained");
  await source.evaluate(host => {
    const send = host.hass.callWS.bind(host.hass);
    let fail = true;
    host.hass.callWS = async request => {
      const reply = await send(request);
      if (request.section === "configuration" && request.action === "save" && fail) {
        fail = false;
        throw {code:"offline", message:"Save reply lost"};
      }
      return reply;
    };
  });
  await source.locator("#save-config").click();
  await expect.poll(() => source.evaluate(host => host._configurationSaving)).toBe(false);
  await source.evaluate(host => {
    window.committedVoiceSource = host;
    const target = document.createElement("extended-openai-management-panel");
    target.id = "committed-destination"; target.hass = host.hass;
    document.body.append(target); browserHarness.panel = target;
  });
  const target = page.locator("#committed-destination");
  await expect(target.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await target.evaluate(async host => {
    window.committedVoiceRow = host.shadowRoot.querySelector("[data-voice-mapping-row]");
    committedVoiceSource.remove(); await host._loadSection(true);
  });
  await expect.poll(() => target.evaluate(host => host._configDirty)).toBe(false);
  expect(await target.evaluate(host => host.shadowRoot.querySelector("[data-voice-mapping-row]") === committedVoiceRow)).toBe(true);
  await target.locator(".voice-owner-type").nth(0).selectOption("shared");
  await select(target, 0, "assist_satellite.kitchen");
  await expect.poll(() => target.evaluate(host => host._draft.voice_device_mappings)).toEqual({kitchen:"shared:household", living:"shared:household"});
  await target.locator("#save-config").click();
  await expect.poll(() => page.evaluate(() => browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({kitchen:"shared:household", living:"shared:household"});
  await expect.poll(() => target.evaluate(host => host._configDirty)).toBe(false);
  await expectHarnessClean(page, errors);
});

test("failed Voice lookup remains editable after navigation and remount and blocks global Save",async({page})=>{
  const errors=trackPageErrors(page);let panel=await setup(page);
  await page.evaluate(()=>{browserHarness.voiceMode="failed";});
  await select(panel,0,"assist_satellite.unavailable");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("available");
  await navigate(panel,"basics");await panel.locator("#save-config").click();
  expect(await page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual(baseline);
  await navigate(panel,"voice");panel=await remount(page);
  await expect(panel.locator(".voice-satellite-picker").nth(0)).toHaveJSProperty("value","assist_satellite.unavailable");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("available");
  await page.evaluate(()=>{browserHarness.voiceMode="success";});
  await select(panel,0,"assist_satellite.office");
  await panel.locator(".voice-owner-type").nth(0).selectOption("unretained");await panel.locator("#save-config").click();
  await expect.poll(()=>page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual({office:"unretained",living:"shared:household"});
  await expectHarnessClean(page,errors);
});

test("empty and pending Voice rows stay dirty off route and Discard retires late registry completions",async({page})=>{
  const errors=trackPageErrors(page),panel=await setup(page);
  await panel.locator("#add-voice-mapping").click();
  expect(await panel.evaluate(p=>p._configDirty)).toBe(true);
  await navigate(panel,"basics");await panel.locator("#save-config").click();
  expect(await page.evaluate(()=>browserHarness.getState().configuration.config.voice_device_mappings)).toEqual(baseline);
  await panel.locator("#revert-config").click();await navigate(panel,"voice");
  await expect(panel.locator("[data-voice-mapping-row]")).toHaveCount(2);
  await page.evaluate(()=>{browserHarness.voiceMode="pending";});await select(panel,0,"assist_satellite.changed");
  await expect(panel.locator('[data-error="voice_device_mappings"]')).toContainText("Wait");
  await panel.locator("#revert-config").click();
  await page.evaluate(()=>browserHarness.releaseVoice({entity_id:"assist_satellite.changed",device_id:"late-device"}));
  await expect.poll(()=>panel.evaluate(p=>p._configDirty)).toBe(false);
  expect(await panel.evaluate(p=>p._draft.voice_device_mappings)).toEqual(baseline);
  await expect(panel.locator(".voice-device-id").nth(0)).toHaveValue("office");
  await expectHarnessClean(page,errors);
});
