import fs from "node:fs";
import {expect, test} from '@playwright/test';
import {fixtureUrl, acceptConfirmation, expectHarnessClean, trackPageErrors} from './browser-helpers.mjs';

const reachabilityInventory = JSON.parse(fs.readFileSync(new URL('../tests_stress/frontend_control_reachability_inventory.json', import.meta.url), 'utf8'));
for (const viewport of reachabilityInventory.viewports) {
for (const action of ['edit', 'delete', 'malformed-fields', 'invalid-id', 'empty-id']) {
  test(`Group-only quarantine keeps native Functions controls accessible for ${action} (${viewport.name})`, async ({page}) => {
    const errors = trackPageErrors(page);
    await page.setViewportSize({width: viewport.width, height: viewport.height});
    await page.goto(fixtureUrl('overview'));
    await page.waitForFunction(() => window.browserHarness?.panel?._selectedAgent?.());
    await page.evaluate(async action => {
      const harness = window.browserHarness;
      const original = harness.hass.callWS.bind(harness.hass);
      const configuration = harness.getState().configuration;
      const raw = structuredClone(configuration.config.function_groups[0]);
      delete raw.name;
      if (action === 'invalid-id') raw.id = 'invalid group/id';
      if (action === 'empty-id') raw.id = '';
      if (action === 'malformed-fields') {
        raw.functions = {invalid: true};
        raw.loading_mode = 'unavailable-mode';
        raw.enabled = false;
        raw.guest_allowed = true;
      }
      let groups = [raw];
      let revision = configuration.revision;
      const repair = () => groups.some(group => !group.name) ? {
        invalid_tools: [], invalid_count: 0, isolatable: false, tools_valid: true,
        validation_error: 'Function Group name is required', group_issues: [{index: 0, validation_error: 'name is required'}],
        persisted_groups: structuredClone(groups), tools: configuration.config.functions,
      } : null;
      const snapshot = () => ({...configuration, revision, config: {...configuration.config, function_groups: groups.filter(group => group.name)}, function_repair: repair()});
      harness.groupRepairSnapshot = () => ({groups: structuredClone(groups), tools: structuredClone(configuration.config.functions)});
      harness.hass.callWS = async message => {
        if ((message.section === 'configuration' && message.action === 'get') || (message.section === 'function_repair' && message.action === 'configuration_get')) return snapshot();
        if (message.section === 'tools' && message.action === 'validate_current') return {valid: !repair(), errors: repair() ? {function_groups: 'Saved Function Groups need repair'} : {}, function_repair: repair()};
        if (message.section === 'tools' && ['save_group', 'delete_group'].includes(message.action)) {
          if (message.revision !== revision) throw new Error('stale revision');
          if (message.action === 'save_group') {
            if (message.original_id !== raw.id || !message.group.name || message.repair !== true) throw new Error('invalid replacement');
            groups = [structuredClone(message.group)];
          } else {
            if (!message.confirm || message.group_id !== raw.id) throw new Error('invalid deletion');
            groups = [];
          }
          revision += 'x';
          return {revision, functions: configuration.config.functions, function_groups: groups, function_repair: repair()};
        }
        return original(message);
      };
      await harness.panel._navigate('capabilities', 'functions');
    }, action);
    const panel = page.locator('extended-openai-management-panel');
    await expect(panel.getByRole('heading', {name: 'Function Tools & Groups', exact: true})).toBeVisible();
    await expect(panel.locator('#function-repair-editor')).toHaveCount(0);
    await expect(panel.locator('.tool-card').filter({hasText:'baseline_tool'})).toBeVisible();
    const card = panel.locator('.group-repair-entry');
    await expect(card).toBeVisible();
    await panel.locator('#validate-tools').click();
    await expect(panel.locator('#tool-status')).toContainText('need repair');
    await expect(panel.locator('#tool-status')).not.toContainText('All saved tools');
    if (action !== 'delete') {
      await card.locator('.edit-group').click();
      await expect(panel.locator('#group-dialog')).toHaveJSProperty('open', true);
      await expect(panel.locator('#group-id')).toHaveValue(action === 'invalid-id' ? 'invalid group/id' : action === 'empty-id' ? '' : 'baseline-group');
      if (['invalid-id', 'empty-id'].includes(action)) await panel.locator('#group-id').fill('repaired-group');
      await panel.locator('#group-name').fill('Repaired group');
      await panel.locator('#group-save').click();
      await expect(panel.locator('#group-dialog')).toHaveJSProperty('open', false);
      await expect(panel.locator(`.function-group-card[data-group-id="${['invalid-id', 'empty-id'].includes(action) ? 'repaired-group' : 'baseline-group'}"]`)).toContainText('Repaired group');
    } else {
      await card.locator('.delete-group').click();
      await acceptConfirmation(panel);
    }
    await expect(panel.locator('.group-repair-entry')).toHaveCount(0);
    await panel.locator('#validate-tools').click();
    await expect(panel.locator('#tool-status')).toHaveText('All saved tools and groups are valid');
    const saved = await page.evaluate(() => browserHarness.groupRepairSnapshot());
    expect(saved.tools.map(tool => tool.spec.name)).toEqual(['baseline_tool']);
    expect(saved.groups).toHaveLength(action !== 'delete' ? 1 : 0);
    if (action === 'malformed-fields') {
      expect(saved.groups[0]).toMatchObject({enabled: false, guest_allowed: true, loading_mode: 'on_demand', functions: []});
    }
    await expectHarnessClean(page, errors);
  });
}
}
