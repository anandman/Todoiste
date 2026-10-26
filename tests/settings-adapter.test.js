const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function adapter(handler) {
  const file = path.join(__dirname, '../Todoiste/Resources/settings-storage-adapter.js');
  assert.ok(fs.existsSync(file), 'the local settings storage adapter is bundled');
  const calls = [];
  const world = {URL, console, location: {href: 'file:///bundle/Shortcuts/options-page.html'}};
  world.window = world;
  world.webkit = {messageHandlers: {todoisteSettings: {postMessage: (request) => {
    calls.push(JSON.parse(JSON.stringify(request)));
    return handler(request);
  }}}};
  vm.runInNewContext(fs.readFileSync(file, 'utf8').replace(
    '__TODOISTE_RESOURCE_ROOT__', JSON.stringify('file:///bundle/Shortcuts/')), world);
  return {api: world.chrome, browser: world.browser, calls};
}

test('Promise storage preserves unfamiliar keys and nested JSON', async () => {
  const value = {options: '{"future":{"nested":[true,null,"; n"]}}', another: [1, false]};
  const {api, calls, browser} = adapter(async () => value);
  assert.deepEqual(await api.storage.sync.get(null), value);
  assert.deepEqual(calls, [{operation: 'get', argument: null}]);
  assert.equal(browser, api);
});

test('callback get forwards defaults without flattening them', async () => {
  const {api, calls} = adapter(async () => ({future: false}));
  const result = await new Promise((resolve) => api.storage.sync.get({future: false}, resolve));
  assert.deepEqual(result, {future: false});
  assert.deepEqual(calls, [{operation: 'get', argument: {future: false}}]);
});

test('set/remove/clear/getBytesInUse support callbacks and Promises', async () => {
  const {api, calls} = adapter(async (request) => request.operation === 'getBytesInUse' ? 7 : null);
  assert.equal(await api.storage.sync.set({arbitrary: [true, null]}), undefined);
  await new Promise((resolve) => api.storage.sync.remove(['arbitrary'], resolve));
  await api.storage.sync.clear();
  assert.equal(await api.storage.sync.getBytesInUse('future'), 7);
  assert.deepEqual(calls, [
    {operation: 'set', argument: {arbitrary: [true, null]}},
    {operation: 'remove', argument: ['arbitrary']},
    {operation: 'clear', argument: null},
    {operation: 'getBytesInUse', argument: 'future'},
  ]);
});

test('native failures reject Promises and expose callback-scoped lastError', async () => {
  const {api} = adapter(async () => { throw new Error('Disk unavailable'); });
  await assert.rejects(api.storage.sync.get(null), /Disk unavailable/);
  const message = await new Promise((resolve) => api.storage.sync.set({}, () => {
    resolve(api.runtime.lastError.message);
  }));
  assert.equal(message, 'Disk unavailable');
  assert.equal(api.runtime.lastError, undefined);
});

test('requests remain ordered and a failed request does not poison the queue', async () => {
  let release;
  const {api, calls} = adapter((request) => request.operation === 'set' ?
    new Promise((resolve) => { release = resolve; }) : Promise.resolve({latest: true}));
  const write = api.storage.sync.set({future: 'value'});
  const read = api.storage.sync.get(null);
  await Promise.resolve();
  assert.equal(calls.length, 1);
  release(null);
  await write;
  assert.deepEqual(await read, {latest: true});
});

test('runtime resource URLs stay relative to the bundle root', () => {
  const {api} = adapter(async () => null);
  assert.equal(api.runtime.getURL('images/new.svg'), 'file:///bundle/Shortcuts/images/new.svg');
});

test('resource URLs cannot escape the bundled upstream directory', () => {
  const {api} = adapter(async () => null);
  assert.throws(() => api.runtime.getURL('../outside.js'), /resource|bundle/i);
  assert.throws(() => api.runtime.getURL('https://example.com/code.js'), /resource|bundle/i);
});
