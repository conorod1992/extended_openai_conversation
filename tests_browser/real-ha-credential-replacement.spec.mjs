import {expect, test} from '@playwright/test';
import {openColdHaRoute} from './real-ha-shell-helpers.mjs';

test.skip(!process.env.REAL_HA_CREDENTIAL_PHASE, 'requires genuine HA credential acceptance');
test('API key replacement uses native management WebSocket', async ({context, page}) => {
  const errors = [];
  page.on('pageerror', error => {
    const detail = [error.message, error.stack].join('\n');
    if (detail.includes('/extended_openai_conversation_responses/')) errors.push(detail);
  });
  page.on('console', message => {
    if (message.type() === 'error' && message.location().url?.includes('/extended_openai_conversation_responses/')) errors.push(message.text());
  });
  const panel = await openColdHaRoute(context, page, 'usage-maintenance/diagnostics');
  await panel.locator('#agent').selectOption(process.env.REAL_HA_CREDENTIAL_AGENT);
  await panel.locator('#eoc-change-api-key').click();
  const dialog = panel.locator('#eoc-api-key-dialog');
  await dialog.locator('#eoc-new-api-key').fill(process.env.REAL_HA_CREDENTIAL_PHASE === 'invalid' ? 'sk-browser-invalid' : 'sk-browser-replacement');
  await dialog.locator('#eoc-api-key-save').click();
  if (process.env.REAL_HA_CREDENTIAL_PHASE === 'invalid') {
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('#eoc-api-key-error')).toContainText('existing API key');
    await expect(dialog.locator('#eoc-api-key-save')).toBeEnabled();
    await dialog.locator('#eoc-api-key-cancel').click();
  } else {
    await expect(dialog).not.toBeVisible();
    await expect(panel.locator('#test-result')).toContainText('Credential updated or validated');
    await expect(panel.locator('#eoc-auth-recovery')).toHaveCount(0);
    await panel.locator('#test-agent').click();
    await expect(panel.locator('#test-result')).toContainText('Minimal gpt-5.6 request succeeded');
    await expect(panel.locator('#test-result')).toContainText('Function schema accepted');
  }
  await expect(panel.locator('#test-agent')).toBeEnabled();
  expect(errors).toEqual([]);
});
