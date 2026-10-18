import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { sessionFromLocation, bridgeRequest, pairHelper, saveSession } from '../lib/bridge.js';

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
  await assert.rejects(bridgeRequest(null, 'status'), /DDMY X6 Flasher/);
});

test('direct Pages pairing uses only the configured loopback and saves the returned session', async () => {
  const origin = 'http://127.0.0.1:8765', saved = storage(), calls = [];
  const fetch = mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({origin, token: 'paired-secret'})};
  });
  try {
    assert.deepEqual(saveSession(await pairHelper(origin, 1000), page(''), saved), {origin, token: 'paired-secret'});
    assert.deepEqual(sessionFromLocation(page(''), saved), {origin, token: 'paired-secret'});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, `${origin}/api/pair`);
    assert.deepEqual(calls[0].options.headers, {'X-X6-Pair': '1'});
    assert.equal(calls[0].options.method, 'POST');
    assert.equal(calls[0].options.redirect, 'error');
    assert.equal(calls[0].options.credentials, 'omit');
  } finally { fetch.mock.restore(); }
});

test('pairing rejects remote destinations, foreign returned origins and invalid tokens', async () => {
  const origin = 'http://127.0.0.1:8765';
  let answer = {origin: 'http://127.0.0.1:9999', token: 'secret'};
  const fetch = mock.method(globalThis, 'fetch', async () => ({ok: true, json: async () => answer}));
  try {
    await assert.rejects(pairHelper('https://example.com', 1000), /Ungültige/);
    assert.equal(fetch.mock.callCount(), 0);
    await assert.rejects(pairHelper(origin, 1000), /abweichende/);
    answer = {origin, token: 123};
    await assert.rejects(pairHelper(origin, 1000), /Ungültige/);
  } finally { fetch.mock.restore(); }
});

test('an unresponsive helper times out without automatic retries', async () => {
  const fetch = mock.method(globalThis, 'fetch', (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('Timed out')));
  }));
  try {
    await assert.rejects(pairHelper('http://127.0.0.1:8765', 10), /Windows-App konnte nicht gekoppelt/);
    assert.equal(fetch.mock.callCount(), 1);
  } finally { fetch.mock.restore(); }
});
