const test = require('node:test');
const assert = require('node:assert/strict');
const CameraFrames = require('../assets/camera_frames.js');
const flush = () => new Promise(resolve => setImmediate(resolve));
const image = () => ({removeAttribute() { delete this.src; }});
function setup(fetch) {
  const revoked = [];
  let serial = 0;
  const loader = new CameraFrames({fetch, createImage: () => ({decode: async () => {}}),
    urls: {createObjectURL: () => `blob:${++serial}`, revokeObjectURL: url => revoked.push(url)}});
  return {loader, revoked};
}
const response = {ok: true, blob: async () => ({type: 'image/jpeg'})};

test('eight cameras use at most two simultaneous requests and all get a turn', async () => {
  const pending = [];
  const {loader} = setup(() => new Promise(resolve => pending.push(resolve)));
  try {
    const images = Array.from({length: 8}, image);
    images.forEach((img, i) => loader.add(i, img, `/snapshot/${i}`));
    assert.equal(pending.length, 2);
    for (let i = 0; i < 8; i++) {
      pending[i](response);
      await flush();
      assert.ok(loader.active <= 2);
    }
    assert.ok(images.every(img => img.src?.startsWith('blob:')));
  } finally { loader.close(); }
});

test('hidden camera cannot publish a late frame and releases its blob', async () => {
  let resolve;
  const {loader, revoked} = setup(() => new Promise(r => { resolve = r; }));
  try {
    const img = image();
    loader.add('a', img, '/snapshot/a');
    loader.remove('a');
    resolve(response);
    await flush();
    assert.equal(img.src, undefined);
    assert.equal(revoked.length, 1);
  } finally { loader.close(); }
});

test('HTTP failure is shown and retry can recover', async () => {
  let calls = 0, errors = 0, frames = 0;
  const {loader} = setup(async () => ++calls === 1 ? {ok: false, status: 503, json: async () => ({error: 'RTSP unavailable'})} : response);
  try {
    loader.add('a', image(), '/snapshot/a', () => frames++, () => errors++);
    await flush();
    assert.equal(errors, 1);
    assert.equal(frames, 0);
    loader.entries.get('a').due = 0;
    loader.tick();
    await flush();
    assert.equal(frames, 1);
  } finally { loader.close(); }
});

test('switching pages releases displayed image resources', async () => {
  const {loader, revoked} = setup(async () => response);
  const img = image();
  loader.add('a', img, '/snapshot/a');
  await flush();
  const url = img.src;
  loader.close();
  assert.equal(img.src, undefined);
  assert.ok(revoked.includes(url));
});
