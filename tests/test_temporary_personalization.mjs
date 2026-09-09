import test from 'node:test';
import assert from 'node:assert/strict';
import { enableTemporaryPersonalization, ensureTemporaryChatPersonalization } from '../bin/oracle_temporary_personalization.mjs';

function fixture({
  enabled = false,
  temporary = true,
  ambiguous = false,
  enabledLabel = 'Personalized',
  disabledLabel = 'Unpersonalized',
} = {}) {
  let opened = false;
  let clicks = 0;
  globalThis.location = { origin: 'https://chatgpt.com', href: `https://chatgpt.com/?temporary-chat=${temporary}` };
  const base = { isConnected: true, getClientRects: () => [1] };
  const trigger = { ...base, getAttribute: () => enabled ? enabledLabel : disabledLabel, click: () => { opened = true; clicks++; } };
  const menu = { querySelectorAll: () => [yes, no] };
  const row = (label, selected, click) => ({ ...base, querySelector: () => ({ textContent: label }), getAttribute: () => String(selected()), closest: () => menu, click });
  const yes = row(enabledLabel, () => enabled, () => { enabled = true; opened = false; clicks++; });
  const no = row(disabledLabel, () => !enabled, () => { throw Error('must not disable'); });
  globalThis.document = { querySelectorAll: selector => selector === 'button' ? (ambiguous ? [trigger, trigger] : [trigger]) : (opened ? [yes, no] : []) };
  return () => clicks;
}

test('enables only temporary personalization and is idempotent', async () => {
  const clicks = fixture();
  assert.deepEqual(await enableTemporaryPersonalization(), { enabled: true, changed: true });
  assert.equal(clicks(), 2);
  assert.deepEqual(await enableTemporaryPersonalization(), { enabled: true, changed: false });
  assert.equal(clicks(), 2);
});
test('supports the Korean temporary-chat personalization controls', async () => {
  const clicks = fixture({ enabledLabel: '맞춤화', disabledLabel: '맞춤화 안 함' });
  assert.deepEqual(await enableTemporaryPersonalization(), { enabled: true, changed: true });
  assert.equal(clicks(), 2);
  assert.deepEqual(await enableTemporaryPersonalization(), { enabled: true, changed: false });
  assert.equal(clicks(), 2);
});
test('supports the current English non-personalized label', async () => {
  const clicks = fixture({ disabledLabel: 'Non-personalized' });
  assert.deepEqual(await enableTemporaryPersonalization(), { enabled: true, changed: true });
  assert.equal(clicks(), 2);
});
test('regular chat is rejected without clicks', async () => {
  const clicks = fixture({ temporary: false });
  await assert.rejects(enableTemporaryPersonalization, /temporary/);
  assert.equal(clicks(), 0);
});
test('ambiguous control is rejected without clicks', async () => {
  const clicks = fixture({ ambiguous: true });
  await assert.rejects(enableTemporaryPersonalization, /ambiguous/);
  assert.equal(clicks(), 0);
});
test('CDP exception cannot become successful confirmation', async () => {
  await assert.rejects(() => ensureTemporaryChatPersonalization({ evaluate: async () => ({ exceptionDetails: { text: 'failed' } }) }), /not confirmed/);
});
