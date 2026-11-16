import test from 'node:test';
import assert from 'node:assert/strict';
import { LiveTranslation } from '../web/live.mjs';
const flush = () => new Promise(setImmediate);
test('nodes serialize; waiting edits coalesce; failure does not retry', async () => {
  let finish;
  const calls = [];
  const a = new LiveTranslation({ valid: () => true, run: () => {
    calls.push('a'); return new Promise(resolve => { finish = resolve; });
  } });
  let text = 'old';
  const b = new LiveTranslation({ valid: () => true, run: async () => { calls.push(text); throw Error('offline'); } });
  a.schedule(true); await flush(); b.schedule(true); text = 'latest'; b.schedule(true);
  await flush(); assert.deepEqual(calls, ['a']);
  finish(); await flush(); assert.deepEqual(calls, ['a', 'latest']);
  await flush(); assert.equal(calls.length, 2);
  a.dispose(); b.dispose();
});
test('detached graph and cancelled reservations do not execute', async () => {
  let calls = 0;
  const a = new LiveTranslation({ valid: () => false, run: () => calls++ });
  a.schedule(true); await flush(); assert.equal(calls, 0);
  a.valid = () => true; a.schedule(true); a.cancel(); await flush(); assert.equal(calls, 0);
  a.dispose();
});
