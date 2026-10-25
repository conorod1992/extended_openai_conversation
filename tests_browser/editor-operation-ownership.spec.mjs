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

async function identifyPanel(panel) {
  await panel.evaluate(host => {
    browserHarness.hass = {...host.hass, user:{id:'admin', is_admin:true}};
    host.hass = browserHarness.hass;
  });
}

async function replaceOwnedPanel(panel, route = null) {
  await panel.evaluate((host, route) => {
    window.detachedSavingEditor = host;
    const parent = host.parentElement, hass = host.hass;
    host.remove();
    if (route) history.pushState({}, '', `/extended-openai/${route}`);
    const next = document.createElement('extended-openai-management-panel');
    parent.append(next);
    next.hass = hass;
  }, route);
}

for (const bundle of [false, true]) for (const kind of ['knowledge', 'memory']) for (const newerDraft of [false, true]) {
  test(`${kind} detached acknowledgement refreshes its replacement and retains newer draft=${newerDraft} (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(`data-memory/${kind === 'memory' ? 'memories' : 'knowledge'}`, bundle ? '&bundle=1' : ''));
    const panel = page.locator('extended-openai-management-panel');
    const add = panel.locator(kind === 'memory' ? '#add-memory' : '#add-source');
    await expect(add).toBeVisible();
    await identifyPanel(panel);
    await add.click();
    if (kind === 'knowledge') await panel.locator('#knowledge-title').fill('Detached acknowledgement identity');
    await panel.locator(`#${kind}-content`).fill('Committed A after replacement');
    const section = kind === 'memory' ? 'memories' : 'knowledge', create = kind === 'memory' ? 'add' : 'create';
    await gate(page, section, [create, 'update']);
    await panel.locator(`#${kind}-save`).click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
    if (newerDraft) await panel.locator(`#${kind}-content`).fill('Newer unsaved B across replacement');
    await replaceOwnedPanel(panel);
    await expect.poll(() => panel.evaluate((host, kind) => !host._busy && Array.isArray(host._result?.[kind === 'memory' ? 'memories' : 'sources']), kind)).toBe(true);
    expect(await panel.evaluate((host, kind) => host._result[kind === 'memory' ? 'memories' : 'sources'].some(record => record.content === 'Committed A after replacement'), kind)).toBe(false);
    await page.evaluate(() => pendingEditors[0].finish(false));
    await expect.poll(() => panel.evaluate((host, kind) => host._result[kind === 'memory' ? 'memories' : 'sources']?.some(record => record.content === 'Committed A after replacement'), kind)).toBe(true);
    if (newerDraft) {
      await expect(panel.locator(`#${kind}-content`)).toHaveValue('Newer unsaved B across replacement');
      await expect(panel.locator(`#${kind}-dialog`)).toBeVisible();
      const identity = await panel.evaluate((host, kind) => kind === 'memory' ? host._editingMemory : host._editingSource, kind);
      await panel.locator(`#${kind}-save`).click();
      await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(2);
      expect(await page.evaluate(() => pendingEditors[1].request.action)).toBe('update');
      expect(await page.evaluate(() => pendingEditors[1].request)).toMatchObject(kind === 'memory'
        ? {memory_id:identity.memory_id, expected_revision:identity.revision}
        : {source_id:identity.source_id, expected_revision:identity.updated_at});
      await page.evaluate(() => pendingEditors[1].finish(false));
      await expect.poll(() => panel.evaluate((host, kind) => host._result[kind === 'memory' ? 'memories' : 'sources']?.filter(record => record.content === 'Newer unsaved B across replacement').length, kind)).toBe(1);
    }
    await expect(panel.locator('dialog[open]')).toHaveCount(0);
    expect(await page.evaluate(({section, create}) => browserHarness.calls.filter(call => call.section === section && call.action === create).length, {section, create})).toBe(1);
    await expectHarnessClean(page, errors);
  });
}

for (const bundle of [false, true]) for (const kind of ['knowledge', 'memory']) {
  test(`${kind} detached acknowledgement invalidates a replacement on another route (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(`data-memory/${kind === 'memory' ? 'memories' : 'knowledge'}`, bundle ? '&bundle=1' : ''));
    const panel = page.locator('extended-openai-management-panel');
    const add = panel.locator(kind === 'memory' ? '#add-memory' : '#add-source');
    await expect(add).toBeVisible();
    await identifyPanel(panel);
    await add.click();
    if (kind === 'knowledge') await panel.locator('#knowledge-title').fill('Saved while another route is visible');
    await panel.locator(`#${kind}-content`).fill('Committed from another route');
    const section = kind === 'memory' ? 'memories' : 'knowledge', create = kind === 'memory' ? 'add' : 'create';
    await gate(page, section, [create]);
    await panel.locator(`#${kind}-save`).click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
    await replaceOwnedPanel(panel, 'guide');
    await expect(panel.locator('.guide-search')).toBeVisible();
    const reads = await page.evaluate(section => browserHarness.calls.filter(call => call.section === section && call.action === 'list').length, section);
    await page.evaluate(() => pendingEditors[0].finish(false));
    await expect.poll(() => page.evaluate(() => Number(detachedSavingEditor._eocAgentMutations || 0))).toBe(0);
    expect(await page.evaluate(kind => detachedSavingEditor.shadowRoot.querySelector(`#${kind}-dialog`).open, kind)).toBe(false);
    await expect.poll(() => page.evaluate(() => Boolean(detachedSavingEditor._eocAcknowledgedCollection))).toBe(false);
    await expect.poll(() => panel.evaluate(host => Boolean(host._eocDraftRecoveryTimer))).toBe(false);
    await expect(panel.locator('.guide-search')).toBeVisible();
    expect(await panel.evaluate(host => host._viewKey())).toBe('guide');
    expect(await page.evaluate(section => browserHarness.calls.filter(call => call.section === section && call.action === 'list').length, section)).toBe(reads);
    await panel.evaluate((host, kind) => host._navigate('data-memory', kind === 'memory' ? 'memories' : 'knowledge'), kind);
    await expect.poll(() => panel.evaluate((host, kind) => host._result[kind === 'memory' ? 'memories' : 'sources']?.some(record => record.content === 'Committed from another route'), kind)).toBe(true);
    expect(await page.evaluate(({section, create}) => browserHarness.calls.filter(call => call.section === section && call.action === create).length, {section, create})).toBe(1);
    await expectHarnessClean(page, errors);
  });
}

for (const bundle of [false, true]) for (const kind of ['knowledge', 'memory']) for (const refresh of [false, true]) {
  test(`${kind} acknowledgement retains newer edits and updates the same record: refresh=${refresh} (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(`data-memory/${kind === 'memory' ? 'memories' : 'knowledge'}`, bundle ? '&bundle=1' : ''));
    const panel = page.locator('extended-openai-management-panel');
    await panel.locator(kind === 'memory' ? '#add-memory' : '#add-source').click();
    if (kind === 'knowledge') await panel.locator('#knowledge-title').fill('Saved source identity');
    const content = panel.locator(`#${kind}-content`), save = panel.locator(`#${kind}-save`), dialog = panel.locator(`#${kind}-dialog`);
    await content.fill('Submitted A');
    const section = kind === 'memory' ? 'memories' : 'knowledge';
    await gate(page, section, kind === 'memory' ? ['add', 'update'] : ['create', 'update']);
    await save.click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
    await content.fill('Newer unsaved B');
    if (refresh) await panel.evaluate(host => host._loadSection(true));
    await page.evaluate(() => pendingEditors[0].finish(false));
    await expect(save).toBeEnabled();
    await expect(dialog).toBeVisible();
    await expect(content).toHaveValue('Newer unsaved B');
    expect(await panel.evaluate(host => host._unsavedState.hasChanges())).toBe(true);
    await save.click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(2);
    expect(await page.evaluate(() => pendingEditors[1].request.action)).toBe('update');
    await page.evaluate(() => pendingEditors[1].finish(false));
    await expect(dialog).not.toBeVisible();
    const records = await panel.evaluate((host, kind) => host._result[kind === 'memory' ? 'memories' : 'sources'], kind);
    expect(records.filter(record => record.content === 'Newer unsaved B')).toHaveLength(1);
    expect(records.filter(record => record.content === 'Submitted A')).toHaveLength(0);
    await expectHarnessClean(page, errors);
  });
}

for (const bundle of [false, true]) test(`retained Memory editor deletes from its acknowledged owner after a move (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl('data-memory/memories', bundle ? '&bundle=1' : ''));
  const panel = page.locator('extended-openai-management-panel');
  await expect(panel.locator('.memory-edit-button').first()).toBeVisible();
  await panel.evaluate(host => host._data.scopes.push({scope_id:'shared:household', scope_type:'shared', display_name:'Shared household', memory_count:0}));
  await panel.locator('.memory-edit-button').first().click();
  const content = panel.locator('#memory-content'), dialog = panel.locator('#memory-dialog');
  await content.fill('Submitted owner move');
  await panel.locator('#memory-owner').selectOption('shared:household');
  await gate(page, 'memories', ['update']);
  await panel.locator('#memory-save').click();
  await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
  await content.fill('Newer unsaved content after owner move');
  await page.evaluate(() => pendingEditors[0].finish(false));
  await expect(panel.locator('#memory-save')).toBeEnabled();
  await expect(dialog).toBeVisible();
  const id = await panel.evaluate(host => host._editingMemory.memory_id);
  expect(await page.evaluate(id => browserHarness.getState().memories.find(memory => memory.memory_id === id)?.scope_id, id)).toBe('shared:household');
  await panel.locator('#memory-delete').click();
  await acceptConfirmation(panel);
  await expect(dialog).not.toBeVisible();
  expect(await page.evaluate(() => browserHarness.calls.filter(call => call.section === 'memories' && call.action === 'delete').at(-1)?.scope_id)).toBe('shared:household');
  expect(await page.evaluate(id => browserHarness.getState().memories.some(memory => memory.memory_id === id), id)).toBe(false);
  expect(await panel.evaluate(host => host._data.scopes.find(scope => scope.scope_id === 'shared:household').memory_count)).toBe(0);
  await expectHarnessClean(page, errors);
});

for (const bundle of [false, true]) test(`pending Short-term creation locks its payload and restores controls on failure (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
  const errors = trackPageErrors(page);
  await page.goto(fixtureUrl('data-memory/memories', bundle ? '&bundle=1' : ''));
  const panel = page.locator('extended-openai-management-panel');
  await panel.locator('#add-memory').click();
  await panel.locator('#memory-type').selectOption('temporary');
  await panel.locator('#memory-content').fill('Temporary submitted once');
  await panel.locator('#memory-expiry').fill(await page.evaluate(() => new Date(Date.now() + 86_400_000).toISOString().slice(0,16)));
  await gate(page, 'memories', ['temporary_add']);
  await panel.locator('#memory-save').click();
  await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
  for (const id of ['content', 'category', 'type', 'owner', 'expiry']) await expect(panel.locator(`#memory-${id}`)).toBeDisabled();
  await page.evaluate(() => pendingEditors[0].finish(true));
  await expect(panel.locator('#memory-save')).toBeEnabled();
  await expect(panel.locator('#memory-content')).toBeEnabled();
  await expect(panel.locator('#memory-type')).toBeEnabled();
  await expect(panel.locator('#memory-content')).toHaveValue('Temporary submitted once');
  await expectHarnessClean(page, errors);
});

for (const bundle of [false, true]) for (const kind of ['knowledge', 'memory']) for (const failure of [false, true]) {
  test(`${kind} save survives an in-place reconnect refresh: failure=${failure} (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(`data-memory/${kind === 'memory' ? 'memories' : 'knowledge'}`, bundle ? '&bundle=1' : ''));
    const panel = page.locator('extended-openai-management-panel');
    await panel.locator(kind === 'memory' ? '#add-memory' : '#add-source').click();
    if (kind === 'knowledge') await panel.locator('#knowledge-title').fill('Reconnect acknowledgement');
    await panel.locator(`#${kind}-content`).fill('Saved once across reconnect');
    const section = kind === 'memory' ? 'memories' : 'knowledge';
    const action = kind === 'memory' ? 'add' : 'create';
    await gate(page, section, [action]);
    await panel.locator(`#${kind}-save`).click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
    const visit = await panel.evaluate(host => host._loadToken);
    await panel.evaluate(host => host._loadSection(true));
    expect(await panel.evaluate(host => host._loadToken)).not.toBe(visit);
    await expect(panel.locator(`#${kind}-dialog`)).toBeVisible();
    await page.evaluate(failure => pendingEditors[0].finish(failure), failure);
    if (failure) {
      await expect(panel.locator(`#${kind}-error`)).toContainText('Delayed editor failure');
      await expect(panel.locator(`#${kind}-save`)).toBeEnabled();
      await expect(panel.locator(`#${kind}-content`)).toHaveValue('Saved once across reconnect');
    } else {
      await expect(panel.locator(`#${kind}-dialog`)).not.toBeVisible();
      // This read finished before the write committed. Acknowledgement must
      // revalidate it so the successful save becomes visible without navigation.
      await expect.poll(() => panel.evaluate((host, kind) => host._result[kind === 'memory' ? 'memories' : 'sources']?.filter(record => record.content === 'Saved once across reconnect').length, kind)).toBe(1);
      expect(await page.evaluate(({section, action}) => browserHarness.calls.filter(call => call.section === section && call.action === action).length, {section, action})).toBe(1);
    }
    await expectHarnessClean(page, errors);
  });
}

for (const bundle of [false, true]) for (const kind of ['knowledge', 'memory']) {
  test(`${kind} post-acknowledgement read failure preserves the successful save (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl(`data-memory/${kind === 'memory' ? 'memories' : 'knowledge'}`, bundle ? '&bundle=1' : ''));
    const panel = page.locator('extended-openai-management-panel');
    await panel.locator(kind === 'memory' ? '#add-memory' : '#add-source').click();
    if (kind === 'knowledge') await panel.locator('#knowledge-title').fill('Saved before failed read');
    await panel.locator(`#${kind}-content`).fill('Save succeeded independently of refresh');
    const section = kind === 'memory' ? 'memories' : 'knowledge', action = kind === 'memory' ? 'add' : 'create';
    await gate(page, section, [action]);
    await panel.locator(`#${kind}-save`).click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
    await panel.evaluate(host => host._loadSection(true));
    await page.evaluate(section => {
      const original = browserHarness.hass.callWS.bind(browserHarness.hass);
      let fail = true;
      browserHarness.hass.callWS = request => {
        if (fail && request.section === section && request.action === 'list') {
          fail = false;
          return Promise.reject(new Error('Post-save collection read unavailable'));
        }
        return original(request);
      };
    }, section);
    await page.evaluate(() => pendingEditors[0].finish(false));
    await expect(panel.locator(`#${kind}-dialog`)).not.toBeVisible();
    await expect(panel.locator('.route-error-state')).toContainText('Post-save collection read unavailable');
    expect(await page.evaluate(kind => browserHarness.getState()[kind === 'memory' ? 'memories' : 'knowledgeSources'].filter(record => record.content === 'Save succeeded independently of refresh').length, kind)).toBe(1);
    await panel.locator('#retry-route').click();
    await expect.poll(() => panel.evaluate((host, kind) => host._result[kind === 'memory' ? 'memories' : 'sources']?.some(record => record.content === 'Save succeeded independently of refresh'), kind)).toBe(true);
    expect(await page.evaluate(({section, action}) => browserHarness.calls.filter(call => call.section === section && call.action === action).length, {section, action})).toBe(1);
    await expectHarnessClean(page, errors);
  });
}

for (const bundle of [false, true]) for (const destination of ['guide', 'agent']) {
  test(`post-acknowledgement collection read cannot overwrite a later ${destination} (${bundle ? 'bundle' : 'source'})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.goto(fixtureUrl('data-memory/knowledge', `&agents=2${bundle ? '&bundle=1' : ''}`));
    const panel = page.locator('extended-openai-management-panel');
    await panel.locator('#add-source').click();
    await panel.locator('#knowledge-title').fill('Read belongs to its original owner');
    await panel.locator('#knowledge-content').fill('Saved in the original assistant');
    await gate(page, 'knowledge', ['create']);
    await panel.locator('#knowledge-save').click();
    await expect.poll(() => page.evaluate(() => pendingEditors.length)).toBe(1);
    await panel.evaluate(host => host._loadSection(true));
    await page.evaluate(() => {
      const original = browserHarness.hass.callWS.bind(browserHarness.hass);
      let held = false;
      browserHarness.hass.callWS = async request => {
        const response = await original(request);
        if (!held && request.section === 'knowledge' && request.action === 'list') {
          held = true;
          return new Promise(resolve => {window.releaseAcknowledgedRead = () => resolve(response);});
        }
        return response;
      };
    });
    await page.evaluate(() => pendingEditors[0].finish(false));
    await expect.poll(() => page.evaluate(() => typeof window.releaseAcknowledgedRead)).toBe('function');
    if (destination === 'guide') await panel.evaluate(host => host._navigate('guide'));
    else await panel.locator('#agent').selectOption('scale-agent-1');
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    const owner = await panel.evaluate(host => ({agent:host._agentId, route:host._viewKey(), result:JSON.stringify(host._result)}));
    await page.evaluate(() => releaseAcknowledgedRead());
    await expect.poll(() => panel.evaluate(host => host._busy)).toBe(false);
    expect(await panel.evaluate(host => ({agent:host._agentId, route:host._viewKey(), result:JSON.stringify(host._result)}))).toEqual(owner);
    if (destination === 'guide') await expect(panel.locator('.guide-search')).toBeVisible();
    else await expect(panel.locator('#agent')).toHaveValue('scale-agent-1');
    await expect(panel.locator('dialog[open]')).toHaveCount(0);
    await expectHarnessClean(page, errors);
  });
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
