import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { ActionsUSB, CMD } from '../lib/transport.js';
import { view } from '../lib/formats.js';

const profile = JSON.parse(await readFile(new URL('../tools/x6_profile.json', import.meta.url)));

function fakeUSB(responses, overrides = {}) {
  const calls = [];
  const device = { vendorId: 0x10d6, productId: 0x10d6,
    configuration: { interfaces: [{interfaceNumber: 0, alternates: [{interfaceClass: 255, alternateSetting: 0,
      endpoints: [{type: 'bulk', direction: 'in', endpointNumber: 1}, {type: 'bulk', direction: 'out', endpointNumber: 2}]}]}]},
    open: async () => {}, claimInterface: async () => {}, selectAlternateInterface: async () => {},
    close: async () => { calls.push('close'); },
    transferOut: async (endpoint, data) => { calls.push({endpoint, data: data.slice()}); return {status: 'ok', bytesWritten: data.length}; },
    transferIn: async () => {
      const data = responses.shift();
      if (!data) throw new Error('No response');
      return {status: 'ok', data: view(data)};
    }, ...overrides };
  return { device, calls };
}

function response(data = [], status = 0) {
  const packet = new Uint8Array(data.length + 13);
  packet.set(data);
  view(packet).setUint32(data.length, 0x53425355, true);
  packet[packet.length - 1] = status;
  return packet;
}

test('Actions command layout and split status/data replies match the native protocol', async () => {
  const reply = response([1, 2, 3, 4]);
  const {device, calls} = fakeUSB([reply.subarray(0, 2), reply.subarray(2, 10), reply.subarray(10)]);
  const usb = new ActionsUSB(device, profile);
  await usb.open();
  assert.deepEqual(await usb.command(CMD.READ_RAM, 4, 0x118000, true), new Uint8Array([1, 2, 3, 4]));
  const packet = calls[0].data;
  assert.equal(packet.length, 31);
  assert.equal(view(packet).getUint32(0, true), 0x43425355);
  assert.equal(packet[12], 0x80);
  assert.equal(packet[15], 0xcd);
  assert.equal(view(packet).getUint32(16, true), 0x93);
  assert.equal(view(packet).getUint32(24, true), 0x118000);
});

test('status, signature, tag and residue errors invalidate the session without retry', async () => {
  for (const offset of [0, 4, 8, 12]) {
    const packet = response();
    packet[offset] ^= 1;
    const {device, calls} = fakeUSB([packet]);
    const usb = new ActionsUSB(device, {...profile, write_delay_ms: 0});
    await usb.open();
    await assert.rejects(usb.writeFlash(0xc0000000, new Uint8Array(32768)), /bestätigt/);
    await assert.rejects(usb.reset(), /beendet/);
    assert.equal(calls.filter(call => call?.data).length, 2); // one CBW and one block only
  }
});

test('timeout, disconnection and short OUT never allow a following command', async () => {
  for (const transferOut of [async () => new Promise(() => {}), async () => {throw new Error('disconnected');},
    async () => ({status: 'ok', bytesWritten: 3})]) {
    const {device} = fakeUSB([], {transferOut});
    const usb = new ActionsUSB(device, {...profile, usb_timeout_ms: 15});
    await usb.open();
    await assert.rejects(usb.info());
    assert.equal(usb.alive, false);
    await assert.rejects(usb.info(), /beendet/);
  }
});

test('wrong identity is rejected before device open', async () => {
  let opened = false;
  const {device} = fakeUSB([], {productId: 0x1101, open: async () => { opened = true; }});
  await assert.rejects(new ActionsUSB(device, profile).open(), /ADFU/);
  assert.equal(opened, false);
});

test('flash write addresses advance in sectors and tags reset', async () => {
  const {device, calls} = fakeUSB([response(), response()]);
  const usb = new ActionsUSB(device, {...profile, write_delay_ms: 0});
  await usb.open();
  await usb.writeFlash(0xc0000000, new Uint8Array(16384 + 512));
  const packets = calls.filter(call => call?.data?.length === 31).map(call => view(call.data));
  assert.deepEqual(packets.map(packet => packet.getUint32(24, true)), [0xc0000000, 0xc0000020]);
  assert.deepEqual(packets.map(packet => packet.getUint32(20, true)), [32, 1]);
  assert.deepEqual(packets.map(packet => packet.getUint32(4, true)), [0, 0]);
});

test('interrupted LFI reads retain only confirmed blocks and use the read prefix', async () => {
  const block = new Uint8Array(512).fill(0x5a);
  const {device, calls} = fakeUSB([response(block), response(block, 1)]);
  const usb = new ActionsUSB(device, {...profile, lfi_size: 1536, transfer_size: 512});
  await usb.open();
  await assert.rejects(usb.readLfi(), error => {
    assert.deepEqual(error.partialLfi, block);
    return true;
  });
  assert.equal(usb.alive, false);
  const packets = calls.filter(call => call?.data?.length === 31).map(call => view(call.data));
  assert.deepEqual(packets.map(packet => packet.getUint32(24, true)), [0x80000000, 0x80000001]);
});
