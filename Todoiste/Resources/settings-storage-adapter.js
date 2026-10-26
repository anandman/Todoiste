// Native-only adapter injected into the local upstream settings page before
// its scripts execute. Vendored settings files stay unchanged.
(function () {
  'use strict';
  const resourceRoot = __TODOISTE_RESOURCE_ROOT__;
  const channel = window.webkit.messageHandlers.todoisteSettings;
  const runtime = {
    getURL(path) {
      const url = new URL(String(path).replace(/^\/+/, ''), resourceRoot).href;
      if (!url.startsWith(resourceRoot)) throw new Error('Resource URL is outside the settings bundle.');
      return url;
    },
  };
  let tail = Promise.resolve();

  function request(operation, argument, callback) {
    const result = tail.then(() => channel.postMessage({operation, argument}));
    tail = result.catch(() => {});
    const value = result.then((reply) =>
      operation === 'get' || operation === 'getBytesInUse' ? reply : undefined);
    if (typeof callback !== 'function') return value;
    value.then((reply) => callback(reply), (error) => {
      runtime.lastError = {message: String(error.message || error)};
      try { callback(); } finally { delete runtime.lastError; }
    });
  }

  const sync = {
    get(keys, callback) {
      if (typeof keys === 'function') return request('get', null, keys);
      return request('get', keys === undefined ? null : keys, callback);
    },
    set(items, callback) { return request('set', items, callback); },
    remove(keys, callback) { return request('remove', keys, callback); },
    clear(callback) { return request('clear', null, callback); },
    getBytesInUse(keys, callback) {
      if (typeof keys === 'function') return request('getBytesInUse', null, keys);
      return request('getBytesInUse', keys === undefined ? null : keys, callback);
    },
  };
  window.chrome = {storage: {sync}, runtime};
  window.browser = window.chrome;
})();
