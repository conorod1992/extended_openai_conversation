import {expect,test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

test("Client validation and uncertain retry guards preserve the current data-editor error",async({page})=>{
  for(const bundled of [false,true]) for(const kind of ["temporary","persistent","knowledge"]) {
    const errors=trackPageErrors(page),panel=await openDataCollection(page,kind,kind==="temporary"?12:1,bundled);
    const editor=kind==="temporary"?"temporary-memory":kind==="persistent"?"memory":"knowledge";
    if(kind==="temporary") await panel.evaluate(async host=>{
      dataCollectionBackend.state.temporary[0].expires_at=new Date(Date.now()+2*86400000).toISOString();
      await host._loadSection(true);
    });
    await panel.locator(kind==="temporary"?"button.edit-temporary-memory":kind==="persistent"?".memory-edit-button":".source-edit-button").first().click();
    const original=await panel.evaluate((host,kind)=>structuredClone(kind==="temporary"?host._temporaryMemoryDraft:kind==="persistent"?host._editingMemory:host._editingSource),kind);
    await panel.evaluate((host,kind)=>{
      const send=host.hass.callWS.bind(host.hass),probe=window.dataGuardProbe={attempts:0,unknown:false};
      host.hass.callWS=async message=>{
        if(message.section!==(kind==="knowledge"?"knowledge":"memories")||message.action!==(kind==="temporary"?"temporary_update":"update")) return send(message);
        probe.attempts++;
        if(!probe.unknown) throw {code:"eoai_mutation_rejected",message:"Authoritative validation rejection"};
        await send(message);
        throw {code:"unknown_error",message:"Connection lost after storing the edit"};
      };
    },kind);
    await panel.locator(`#${editor}-content`).fill("First rejected edit");
    await panel.locator(`#${editor}-save`).click();
    await expect(panel.locator(`#${editor}-error`)).toContainText("Authoritative validation rejection");
    const firstError=await panel.locator(`#${editor}-error`).textContent();
    await panel.locator(`#${editor}-content`).fill("");
    await panel.locator(`#${editor}-save`).click();
    expect(await panel.locator(`#${editor}-content`).evaluate(control=>control.validity.valueMissing)).toBe(true);
    expect(await page.evaluate(()=>dataGuardProbe.attempts)).toBe(1,"native required-field validation does not admit another mutation");
    await expect(panel.locator(`#${editor}-error`)).toHaveText(firstError);
    expect(await panel.evaluate((host,kind)=>kind==="temporary"?host._temporaryMemoryDraft:kind==="persistent"?host._editingMemory:host._editingSource,kind)).toEqual(original);
    if(kind==="temporary") {
      await panel.locator("#temporary-memory-form").evaluate(form=>form.dispatchEvent(new Event("submit",{bubbles:true,cancelable:true})));
      await expect(panel.locator("#temporary-memory-error")).toContainText("Memory, category, and expiry are required");
      expect(await page.evaluate(()=>dataGuardProbe.attempts)).toBe(1);
    } else {
      await panel.locator(`#${editor}-content`).fill("Possibly committed correction");
      await page.evaluate(()=>{dataGuardProbe.unknown=true;});
      await panel.locator(`#${editor}-save`).click();
      await expect(panel.locator(`#${editor}-error`)).toContainText("outcome is unknown");
      await expect(panel.locator(`#${editor}-save`)).toBeDisabled();
      const uncertain=await panel.locator(`#${editor}-error`).textContent();
      await panel.locator(`#${editor}-content`).fill("New input cannot erase the unresolved outcome");
      await panel.evaluate(async(host,editor)=>{
        const save=host.shadowRoot.querySelector(`#${editor}-save`);save.disabled=false;
        await host[editor==="knowledge"?"_saveKnowledge":"_saveMemory"]();
        save.disabled=true;
      },editor);
      expect(await page.evaluate(()=>dataGuardProbe.attempts)).toBe(2,"the uncertain editor marker blocks transport even without the disabled button");
      await expect(panel.locator(`#${editor}-error`)).toHaveText(uncertain);
    }
    await expectHarnessClean(page,errors);
  }
});
