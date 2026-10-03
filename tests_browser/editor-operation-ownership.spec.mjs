import {expect, test} from '@playwright/test';
import {fixtureUrl, trackPageErrors, expectHarnessClean, acceptConfirmation} from './browser-helpers.mjs';

async function gate(page, section, actions) {
  await page.evaluate(({section, actions}) => {
    const original = browserHarness.hass.callWS.bind(browserHarness.hass);
    window.pendingEditors = [];
    browserHarness.hass.callWS = async request => {
      if (request.section !== section || !actions.includes(request.action)) return original(request);
      return new Promise((resolve, reject) => pendingEditors.push({request, finish: async fail => {
        if (fail) reject(new Error('Delayed editor failure'));
        else resolve(request.action === 'import_preview'
          ? {title: request.document, summary: {model: 'gpt-5.6', tools: 0, function_groups: 0, speech_rules: 0}}
          : await original(request));
      }}));
    };
  }, {section, actions});
}

for (const kind of ['knowledge', 'memory']) {
  for (const update of [false, true]) {
    for (const failure of [false, true]) {
      for (const reverse of [false, true]) {
        test(`${kind} ${update ? 'update' : 'create'} replaced editor ${failure ? 'failure' : 'success'} ${reverse ? 'new first' : 'old first'}`, async ({page}) => {
          const errors = trackPageErrors(page);
          await page.goto(fixtureUrl(`data-memory/${kind === 'memory' ? 'memories' : 'knowledge'}`));
          const panel = page.locator('extended-openai-management-panel');
          const add = panel.locator(kind === 'memory' ? '#add-memory' : '#add-source');
          const content = panel.locator(`#${kind}-content`);
          const save = panel.locator(`#${kind}-save`);
          const dialog = panel.locator(`#${kind}-dialog`);
          const fill = async value => {
            if (kind === 'knowledge') await panel.locator('#knowledge-title').fill(value);
            await content.fill(value);
          };
          await add.click();
          await fill('Original editor');
          if (update) {
            await save.click();
            await expect(dialog).not.toBeVisible();
            await panel.locator(kind === 'memory' ? '.memory-edit-button' : '.edit-source').first().click();
            await fill('Old pending update');
          }
          await gate(page, kind === 'memory' ? 'memories' : 'knowledge', kind === 'memory' ? ['add', 'update', 'temporary_add'] : ['create', 'update']);
          await save.click();
          await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
          await dialog.getByRole('button', {name: 'Cancel', exact: true}).click();
          if (await panel.locator('#confirm-dialog').isVisible()) await acceptConfirmation(panel);
          await expect(dialog).not.toBeVisible();
          await add.click();
          await fill('Replacement draft');
          await expect(save).toBeEnabled();
          if (reverse) {
            await save.click();
            await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(2);
            await page.evaluate(() => pendingEditors[1].finish(false));
            await expect(dialog).not.toBeVisible();
            await add.click();
            await fill('Third draft');
          }
          await page.evaluate(failure => pendingEditors[0].finish(failure), failure);
          await expect(dialog).toBeVisible();
          await expect(content).toHaveValue(reverse ? 'Third draft' : 'Replacement draft');
          await expect(panel.locator(`#${kind}-error`)).toBeEmpty();
          await expect(save).toBeEnabled();
          await expectHarnessClean(page, errors);
        });
      }
    }
  }
}

for (const route of ['assistant/basics']) {
  for (const mode of ['new', 'current']) {
    for (const failure of [false, true]) {
      test(`import ${mode} preview owns document and reopened session on ${route} after ${failure ? 'failure' : 'success'}`, async ({page}) => {
        const errors = trackPageErrors(page);
        await page.goto(fixtureUrl(route));
        const panel = page.locator('extended-openai-management-panel');
        await panel.locator('.agent-actions-menu summary').click();
        await panel.locator('#import-agent').click();
        await panel.locator(`input[name="import-mode"][value="${mode}"]`).check();
        await gate(page, 'configuration', ['import_preview']);
        await panel.locator('#import-document').fill('document A');
        await panel.locator('#import-preview').click();
        await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
        await panel.locator('#import-document').fill('document B');
        await panel.locator('#import-preview').click();
        await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(2);
        await page.evaluate(() => pendingEditors[1].finish(false));
        await expect(panel.locator('#import-apply')).toBeEnabled();
        await expect(panel.locator('#import-summary')).toContainText('document B');
        await page.evaluate(failure => pendingEditors[0].finish(failure), failure);
        await expect(panel.locator('#import-apply')).toBeEnabled();
        await expect(panel.locator('#import-summary')).toContainText('document B');
        await panel.locator('#import-document').fill('document C');
        await panel.locator('#import-preview').click();
        await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(3);
        await panel.locator('#import-cancel').click();
        await panel.locator('.agent-actions-menu summary').click();
        await panel.locator('#import-agent').click();
        await panel.locator('#import-document').fill('document D');
        await page.evaluate(failure => pendingEditors[2].finish(failure), failure);
        await expect(panel.locator('#import-apply')).toBeDisabled();
        await expect(panel.locator('#import-summary')).toHaveText('Validate the document to preview it.');
        await expectHarnessClean(page, errors);
      });
    }
  }
}


for (const failure of [false, true]) {
  test(`import preview rejects a replaced configuration revision after ${failure ? 'failure' : 'success'}`, async ({page}) => {
    await page.goto(fixtureUrl('assistant/basics'));
    const panel = page.locator('extended-openai-management-panel');
    await panel.locator('.agent-actions-menu summary').click();
    await panel.locator('#import-agent').click();
    await gate(page, 'configuration', ['import_preview']);
    await panel.locator('#import-document').fill('revision-owned document');
    await panel.locator('#import-preview').click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
    await panel.evaluate(host => { host._configData = {...host._configData, revision: 'replacement-revision'}; });
    await page.evaluate(failure => pendingEditors[0].finish(failure), failure);
    await expect(panel.locator('#import-apply')).toBeDisabled();
    await expect(panel.locator('#import-summary')).toHaveText('Validate the document to preview it.');
  });
}
