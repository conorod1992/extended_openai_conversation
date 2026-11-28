import {expect,test} from "@playwright/test";
import {openDataCollection} from "./data-collection-helpers.mjs";
import {acceptConfirmation,expectHarnessClean,trackPageErrors} from "./browser-helpers.mjs";

for (const bundled of [false, true]) test(`Short-term Memory mutations retain backend expiry order (${bundled ? "shipped" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "temporary", 12, bundled);
  await panel.evaluate(async host => {
    host.hass.config.time_zone = "UTC";
    const [first, second, third] = dataCollectionBackend.state.temporary;
    first.expires_at = "2027-01-15T12:00:00+02:00";
    second.expires_at = "2027-01-15T09:00:00-02:00";
    third.expires_at = "2027-01-15T11:00:00Z";
    dataCollectionBackend.state.temporary = [third, second, first];
    await host._loadSection(true);
  });
  const cardIds = () => panel.locator(".memory-list article").evaluateAll(nodes => nodes.map(node => node.dataset.memoryId));
  const readCount = () => page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "temporary_list").length);
  const scopeCount = () => panel.evaluate(host => host._data.scopes.find(scope => scope.scope_id === host._scopeId).temporary_memory_count || 0);
  await expect.poll(cardIds).toEqual(["temporary-0", "temporary-1", "temporary-2"]);
  const initialReads = await readCount();
  const initialCount = await scopeCount();

  await panel.locator('[data-memory-id="temporary-0"] button.edit-temporary-memory').click();
  await panel.locator("#temporary-memory-expiry").fill("2027-01-15T13:00");
  await panel.locator("#temporary-memory-save").click();
  await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open", false);
  await expect.poll(cardIds).toEqual(["temporary-1", "temporary-2", "temporary-0"]);

  async function addMemory(content, expiry) {
    await panel.locator("#add-memory").click();
    await panel.locator("#memory-content").fill(content);
    await panel.locator("#memory-expiry").fill(expiry);
    await panel.locator("#memory-save").click();
    await expect(panel.locator("#memory-dialog")).toHaveJSProperty("open", false);
  }

  await addMemory("New memory at a tied instant", "2027-01-15T11:00");
  await expect.poll(cardIds).toEqual(["temporary-1", "temporary-12", "temporary-2", "temporary-0"]);
  expect(await scopeCount()).toBe(initialCount + 1);

  await addMemory("Temporary 0", "2027-01-15T09:00");
  await expect.poll(cardIds).toEqual(["temporary-0", "temporary-1", "temporary-12", "temporary-2"]);
  expect(await scopeCount()).toBe(initialCount + 1);
  expect(await readCount()).toBe(initialReads);
  await expectHarnessClean(page, errors);
});

for (const concurrentExpiry of [false, true]) test(`A Short-term Memory acknowledgement refreshes with ${concurrentExpiry ? "a changed collection" : "no order"}`, async ({page}) => {
  const panel = await openDataCollection(page, "temporary", 12);
  await panel.evaluate((host, concurrentExpiry) => {
    const send = host.hass.callWS.bind(host.hass);
    host.hass.callWS = async message => {
      if (message.action === "temporary_update" && concurrentExpiry) dataCollectionBackend.state.temporary.shift();
      const result = await send(message);
      if (message.action === "temporary_update" && !concurrentExpiry) delete result.memory_order;
      return result;
    };
  }, concurrentExpiry);
  const reads = () => page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "temporary_list").length);
  const initialReads = await reads();
  await panel.locator('[data-memory-id="temporary-2"] button.edit-temporary-memory').click();
  await panel.locator("#temporary-memory-expiry").fill("2027-01-15T13:00");
  await panel.locator("#temporary-memory-save").click();
  await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open", false);
  await expect(panel.locator(".memory-list article").first()).toHaveAttribute("data-memory-id", "temporary-2");
  await expect(panel.locator('[data-memory-id="temporary-0"]')).toHaveCount(concurrentExpiry ? 0 : 1);
  expect(await reads()).toBe(initialReads + 1);
});

for (const bundled of [false, true]) test(`Short-term Memory can retain or replace a UTC-only expiry (${bundled ? "shipped" : "source"})`, async ({page}) => {
  const errors = trackPageErrors(page);
  const panel = await openDataCollection(page, "temporary", 12, bundled);
  const originalExpiry = "9999-12-31T23:59:59.123456+00:00";
  await panel.evaluate(async (host, expires_at) => {
    Object.assign(dataCollectionBackend.state.temporary[0], {expires_at, expires_at_local:null});
    await host._loadSection();
  }, originalExpiry);
  const card = panel.locator('[data-memory-id="temporary-0"]');
  await expect(card).toContainText(originalExpiry);
  const edit = card.locator("button.edit-temporary-memory");
  await edit.click();
  const expiry = panel.locator("#temporary-memory-expiry");
  await expect(panel.locator("#temporary-memory-expiry-label")).toHaveText("Expires (UTC)");
  await expect(expiry).toHaveValue("9999-12-31T23:59:59");
  await panel.locator("#temporary-memory-content").fill("Retained the exact instant");
  await panel.locator("#temporary-memory-save").click();
  await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open", false);
  await edit.click();
  const tomorrow = await page.evaluate(() => new Date(Date.now() + 86400000).toISOString().slice(0, 10));
  await expiry.fill(`${tomorrow}T15:17:23`);
  await panel.locator("#temporary-memory-save").click();
  await expect(panel.locator("#temporary-memory-dialog")).toHaveJSProperty("open", false);
  const updates = await page.evaluate(() => dataCollectionBackend.calls.filter(call => call.action === "temporary_update"));
  expect(updates.map(call => call.expires_at)).toEqual([originalExpiry, `${tomorrow}T15:17:23Z`]);
  expect(updates.every(call => !Object.hasOwn(call, "local_times"))).toBe(true);
  await expectHarnessClean(page, errors);
});

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
    expect(sent.map(call=>call.expires_at)).toEqual(["2099-09-01T12:00:00Z",laterExpiry]);
    expect(sent[1].local_times).toEqual({expires_at:laterExpiry});
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
