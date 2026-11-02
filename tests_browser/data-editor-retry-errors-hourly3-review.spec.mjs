import {expect,test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

test("Successful data-editor retries retire prior rejection messages without losing newer input",async({page})=>{
  for(const bundled of [false,true]) for(const kind of ["temporary","persistent","knowledge"]) {
    const errors=trackPageErrors(page),panel=await openDataCollection(page,kind,kind==="temporary"?12:1,bundled);
    const editor=kind==="temporary"?"temporary-memory":kind==="persistent"?"memory":"knowledge";
    const before=await panel.evaluate(async(host,kind)=>{
      if(kind==="temporary") {
        dataCollectionBackend.state.temporary[0].expires_at=new Date(Date.now()+2*86400000).toISOString().slice(0,19)+"Z";
        await host._loadSection(true);
      }
      return structuredClone(dataCollectionBackend.state);
    },kind);
    await panel.locator(kind==="temporary"?"button.edit-temporary-memory":kind==="persistent"?".memory-edit-button":".source-edit-button").first().click();
    await panel.locator(`#${editor}-content`).fill("First submitted fact");
    await panel.evaluate((host,kind)=>{
      const send=host.hass.callWS.bind(host.hass);
      const probe=window.dataEditorRetry={attempts:0,rejectNext:true,holdNext:false};
      host.hass.callWS=async message=>{
        if(message.section!==(kind==="knowledge"?"knowledge":"memories")||message.action!==(kind==="temporary"?"temporary_update":"update")) return send(message);
        probe.attempts++;
        if(probe.rejectNext) {probe.rejectNext=false;throw {code:"eoai_mutation_rejected",message:"The submitted values were rejected"};}
        const result=await send(message);
        if(probe.holdNext) {probe.holdNext=false;await new Promise(resolve=>{probe.release=resolve;});}
        return result;
      };
    },kind);
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-error`)).toContainText("submitted values were rejected");
    expect(await page.evaluate(()=>dataCollectionBackend.state)).toEqual(before);
    expect(await page.evaluate(()=>dataCollectionBackend.calls.filter(call=>["update","temporary_update"].includes(call.action)).length)).toBe(0);

    await panel.locator(`#${editor}-content`).fill("Corrected fact committed by retry");
    if(kind==="temporary") {
      const day=await page.evaluate(()=>new Date(Date.now()+3*86400000).toISOString().slice(0,10));
      await panel.locator("#temporary-memory-expiry").fill(`${day}T12:34:56`);
    }
    await page.evaluate(()=>{dataEditorRetry.holdNext=true;});
    await panel.locator(`#${editor}-save`).click();
    await page.waitForFunction(()=>dataEditorRetry.release);
    await panel.locator(`#${editor}-content`).fill("A newer edit remains after the corrected save");
    await page.evaluate(()=>dataEditorRetry.release());
    await expect(panel.locator("#toast")).toContainText("updated");
    await expect(panel.locator(`#${editor}-dialog`)).toHaveJSProperty("open",true);
    await expect(panel.locator(`#${editor}-save`)).toBeEnabled();
    expect.soft(await panel.locator(`#${editor}-error`).textContent(),`${kind}/${bundled}: old rejection cannot remain an active alert after success`).toBe("");
    await expect(panel.locator(`#${editor}-content`)).toHaveValue("A newer edit remains after the corrected save");

    await page.evaluate(()=>{dataEditorRetry.rejectNext=true;});
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-error`)).toContainText("submitted values were rejected");
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-dialog`)).toHaveJSProperty("open",false);
    const actual=await page.evaluate(kind=>({records:kind==="temporary"?dataCollectionBackend.state.temporary:kind==="persistent"?dataCollectionBackend.state.memories:dataCollectionBackend.state.sources,
      writes:dataCollectionBackend.calls.filter(call=>["update","temporary_update"].includes(call.action)),attempts:dataEditorRetry.attempts}),kind);
    expect(actual.attempts).toBe(4);
    expect(actual.writes.map(call=>call.content)).toEqual(["Corrected fact committed by retry","A newer edit remains after the corrected save"]);
    expect(actual.records[0].content).toBe("A newer edit remains after the corrected save");
    await expectHarnessClean(page,errors);
  }
});
