import {expect, test} from "@playwright/test";
import {expectHarnessClean, trackPageErrors} from "./browser-helpers.mjs";
import {openDataCollection} from "./data-collection-helpers.mjs";

const editors = [
  {kind:"knowledge", section:"knowledge", action:"update", edit:".source-edit-button", id:"knowledge", record:"source-0"},
  {kind:"persistent", section:"memories", action:"update", edit:".memory-edit-button", id:"memory", record:"memory-0"},
  {kind:"temporary", section:"memories", action:"temporary_update", edit:".edit-temporary-memory", id:"temporary-memory", record:"temporary-0"},
];

for (const bundled of [false, true]) {
  for (const editor of editors) {
    test(`${editor.kind} duplicate confirmation requires the same draft and revision (${bundled})`, async ({page}) => {
      const errors = trackPageErrors(page);
      const panel = await openDataCollection(page, editor.kind, 3, bundled);
      await panel.evaluate((host, {section, action}) => {
        const send = host.hass.callWS.bind(host.hass);
        window.duplicateEditRequests = [];
        host.hass.callWS = async request => {
          if (request.section === section && request.action === action) {
            duplicateEditRequests.push(structuredClone(request));
            if (!request.confirm_duplicate) return {status:"duplicate"};
          }
          return send(request);
        };
      }, editor);

      await panel.locator(`button${editor.edit}[data-id="${editor.record}"]`).click();
      const content = panel.locator(`#${editor.id}-content`);
      const save = panel.locator(`#${editor.id}-save`);
      const error = panel.locator(`#${editor.id}-error`);
      await content.fill("First duplicate draft");
      await save.click();
      await expect(error).toContainText("Select Save again");
      await expect(content).toHaveValue("First duplicate draft");

      await content.fill("Changed duplicate draft");
      await save.click();
      await expect(error).toContainText("Select Save again");
      await expect(content).toHaveValue("Changed duplicate draft");
      await save.click();
      await expect(panel.locator(`#${editor.id}-dialog`)).not.toBeVisible();

      const requests = await page.evaluate(() => duplicateEditRequests);
      expect(requests).toHaveLength(3);
      expect(requests[0]).toMatchObject({content:"First duplicate draft", confirm_duplicate:false});
      expect(requests[1]).toMatchObject({content:"Changed duplicate draft", confirm_duplicate:false});
      expect(requests[2]).toEqual({...requests[1], confirm_duplicate:true});
      expect(requests[2].expected_revision).toBeTruthy();
      await expectHarnessClean(page, errors);
    });
  }
}
