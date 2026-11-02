import {expect,test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {acceptConfirmation,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

test("Short-term Memory acknowledges the submitted values while preserving later editor input",async({page})=>{
  for(const bundled of [false,true]) for(const finish of ["save","discard","replacement","unchanged"]) {
  const errors=trackPageErrors(page);
  const panel=await openDataCollection(page,"temporary",12,bundled);
  await panel.evaluate(host=>{host._hass.config.time_zone="Etc/GMT+5";});
  await panel.locator("button.edit-temporary-memory").first().click();
  const initialExpiry=await panel.locator("#temporary-memory-expiry").inputValue();
  await panel.locator("#temporary-memory-content").fill("Submitted short-term fact");
  await panel.evaluate(host=>{
    const send=host.hass.callWS.bind(host.hass);
    host.hass.callWS=async message=>{
      const result=await send(message);
      if(message.section==="memories"&&message.action==="temporary_update"&&!window.firstShortTermReplyHeld) {
        window.firstShortTermReplyHeld=true;
        await new Promise(resolve=>{window.releaseShortTermReply=resolve;});
      }
      return result;
    };
  });
  await panel.locator("#temporary-memory-save").click();
  await page.waitForFunction(()=>window.releaseShortTermReply);
  await expect(panel.locator("#temporary-memory-content")).toBeEnabled();
  const day=await page.evaluate(()=>new Date(Date.now()+2*86400000).toISOString().slice(0,10));
  const laterExpiry=`${day}T09:17:23`,laterISO=`${day}T14:17:23.000Z`;
  if(finish!=="unchanged") {
  await panel.locator("#temporary-memory-content").fill("Newer short-term fact typed while saving");
  await panel.locator("#temporary-memory-category").fill("newer");
  await panel.locator("#temporary-memory-expiry").fill(laterExpiry);
  }
  if(finish==="replacement") {
    await panel.locator("#temporary-memory-dialog .close-temporary-editor.secondary").click();
    await panel.locator('button.edit-temporary-memory[data-id="temporary-1"]').click();
    await panel.locator("#temporary-memory-content").fill("Another memory keeps its own draft");
  }
  await page.evaluate(()=>window.releaseShortTermReply());
  if(finish==="unchanged") {
    await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open",false);
    expect(await panel.evaluate(host=>host._temporaryMemoryDraft)).toBeNull();
    expect(await page.evaluate(()=>dataCollectionBackend.state.temporary[0].content)).toBe("Submitted short-term fact");
    await expectHarnessClean(page,errors);
    continue;
  }
  await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open",true);
  if(finish==="replacement") {
    await expect(panel.locator("#temporary-memory-content")).toHaveValue("Another memory keeps its own draft");
    await expect(panel.locator("#temporary-memory-save")).toBeEnabled();
    expect(await panel.evaluate(p=>p._temporaryMemoryDraft.memory_id)).toBe("temporary-1");
    await panel.locator("#temporary-memory-save").click();
    await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open",false);
    expect(await page.evaluate(()=>dataCollectionBackend.state.temporary[1].content)).toBe("Another memory keeps its own draft");
    await expectHarnessClean(page,errors);
    continue;
  }
  await expect(panel.locator("#temporary-memory-content")).toHaveValue("Newer short-term fact typed while saving");
  expect(await panel.evaluate(p=>p._temporaryMemoryDraft.content)).toBe("Submitted short-term fact");
  expect(await panel.evaluate(p=>p._temporaryMemoryDraft.expires_at)).toBe(initialExpiry);
  expect(await panel.evaluate(p=>p._temporaryMemoryDraft.original_expiry)).toBe("2099-09-01T12:00:00Z");
  await expect(panel.locator("#temporary-memory-expiry")).toHaveValue(laterExpiry);
  expect(await page.evaluate(()=>dataCollectionBackend.state.temporary[0].content)).toBe("Submitted short-term fact");
  await expect(panel.locator("#temporary-memory-save")).toBeEnabled();
  if(finish==="save") {
    await panel.locator("#temporary-memory-save").click();
    await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open",false);
    const sent=await page.evaluate(()=>dataCollectionBackend.calls.filter(call=>call.action==="temporary_update"));
    expect(sent.map(call=>[call.content,call.category])).toEqual([["Submitted short-term fact","general"],["Newer short-term fact typed while saving","newer"]]);
    expect(sent.map(call=>call.expires_at)).toEqual(["2099-09-01T12:00:00Z",laterISO]);
    expect(await page.evaluate(()=>dataCollectionBackend.state.temporary[0].content)).toBe("Newer short-term fact typed while saving");
    expect(await page.evaluate(()=>dataCollectionBackend.state.temporary[0].expires_at)).toBe(laterISO);
  } else {
    await panel.locator("#temporary-memory-dialog .close-temporary-editor.icon").click();
    await acceptConfirmation(panel);
    await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open",false);
    await panel.locator("button.edit-temporary-memory").first().click();
    await expect(panel.locator("#temporary-memory-content")).toHaveValue("Submitted short-term fact");
    await expect(panel.locator("#temporary-memory-expiry")).toHaveValue(initialExpiry);
    expect(await page.evaluate(()=>dataCollectionBackend.calls.filter(call=>call.action==="temporary_update").length)).toBe(1);
  }
  await expectHarnessClean(page,errors);
  }
});
