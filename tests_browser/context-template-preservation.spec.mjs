import {test, expect} from "@playwright/test";
import {fixtureUrl,trackPageErrors,expectHarnessClean} from "./browser-helpers.mjs";

for(const bundled of [false,true]) for(const mixed of [false,true]) for(const action of ["current_datetime_template","exposed_entities_template","preview","edit"]) {
  test(`context bytes survive ${action} mixed=${mixed} bundle=${bundled}`,async ({page})=>{
    const errors=trackPageErrors(page), url=fixtureUrl("assistant/prompt-context",bundled?"&bundle=1":"");
    await page.goto(url);
    const panel=page.locator("extended-openai-management-panel");
    await expect(panel.locator("#prompt-editor")).toBeVisible();
    const ending=mixed?"\r\n":"\n";
    const original={prompt:`\nSystem instructions${ending}Keep the authored layout.`,current_datetime_template:`\n\nDate${ending}{{ now() }}`,exposed_entities_template:`\nDevices${ending}{{ exposed_entities }}`};
    await panel.evaluate(async(host,original)=>{await host._call("configuration","save",{config:{...original,current_datetime_enabled:true,exposed_entities_enabled:true},revision:host._configData.revision});},original);
    await page.goto(url);
    for(const [key,value] of Object.entries(original)) await expect(panel.locator(`[data-config="${key}"]`)).toHaveValue(value.replace(/\r\n?/g,"\n"));
    await panel.evaluate(host=>{
      window.previewDraft=host._draft; window.previewPayloads=[];
      const call=host.hass.callWS.bind(host.hass);
      host.hass.callWS=async message=>{if(message.section==="configuration"&&message.action==="request_preview"){previewPayloads.push(structuredClone(message));return {sections:[],notes:[]};} return call(message);};
    });
    let expected;
    if(action==="current_datetime_template"||action==="exposed_entities_template") {
      await panel.locator("details.advanced-context-formatting > summary").click();
      await panel.locator(`[data-template-key="${action}"]`).click();
      expected={[action]:""};
    } else {
      if(action==="edit") await panel.locator("#prompt-editor").fill("Edited instructions\nwith a new line");
      const before=await panel.evaluate(host=>({draft:structuredClone(host._draft),dirty:host._configDirty,keys:[...host._eocDirtyConfigKeys||[]]}));
      for(let visit=0;visit<2;visit++) {
        await panel.locator("#preview-request").click();
        await expect(panel.locator("#prompt-preview-status")).toHaveClass(/valid/);
        await panel.locator("#prompt-preview-close").click();
      }
      expect(await panel.evaluate(host=>({draft:structuredClone(host._draft),dirty:host._configDirty,keys:[...host._eocDirtyConfigKeys||[]]}))).toEqual(before);
      expect(await panel.evaluate(host=>host._draft===window.previewDraft)).toBe(true);
      const previews=await page.evaluate(()=>previewPayloads);
      for(const preview of previews) expect(preview.config).toMatchObject({...original,...action==="edit"?{prompt:"Edited instructions\nwith a new line"}:{}});
      await panel.locator('[data-config="current_datetime_enabled"]').uncheck();
      expected={current_datetime_enabled:false,...action==="edit"?{prompt:"Edited instructions\nwith a new line"}:{}};
    }
    await panel.locator("#save-config").click();
    await expect(panel.locator("#save-config")).toHaveCount(0);
    const saved=await page.evaluate(()=>browserHarness.calls.filter(call=>call.section==="configuration"&&call.action==="save").at(-1));
    expect(saved.config).toEqual(expected);
    expect(await page.evaluate(()=>browserHarness.getState().configuration.config)).toMatchObject({...original,...expected});
    await page.goto(url);
    for(const [key,value] of Object.entries({...original,...expected})) if(typeof value==="string") await expect(panel.locator(`[data-config="${key}"]`)).toHaveValue(value.replace(/\r\n?/g,"\n"));
    await expectHarnessClean(page,errors);
  });
}
