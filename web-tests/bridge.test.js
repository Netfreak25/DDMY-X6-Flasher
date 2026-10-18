import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sessionFromLocation, bridgeRequest } from '../lib/bridge.js';

const storage = () => {const data = new Map(); return {getItem: key => data.get(key), setItem: (key, value) => data.set(key, value)};};
const page = hash => ({hash, pathname: '/DDMY-X6-Flasher/', origin: 'https://netfreak25.github.io'});

test('Pages opens without pairing; explicit pairing stays in the project tab', () => {
  const saved = storage();
  assert.equal(sessionFromLocation(page(''), saved), null);
  const session = sessionFromLocation(page('#session=secret&bridge=http://127.0.0.1:8765'), saved);
  assert.deepEqual(session, {token: 'secret', origin: 'http://127.0.0.1:8765'});
  assert.deepEqual(sessionFromLocation(page(''), saved), session);
  assert.equal(sessionFromLocation({...page(''), pathname: '/another-project/'}, saved), null);
});

test('pairing rejects remote token destinations and missing sessions make no request', async () => {
  for (const origin of ['https://evil.example', 'http://localhost', 'http://127.0.0.1:8765/path', 'http://user@127.0.0.1']) {
    assert.throws(() => sessionFromLocation(page(`#session=secret&bridge=${origin}`), storage()), /Ungültige/);
  }
  await assert.rejects(bridgeRequest(null, 'status'), /Windows-Helfer/);
});
