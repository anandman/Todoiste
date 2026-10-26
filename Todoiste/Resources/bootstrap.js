// Bootstrap script injected by the Swift wrapper between mousetrap.js and
// todoist-shortcuts.js. Options (data-todoist-shortcuts-options) are set by
// a preceding inline script generated from UserDefaults — see WebView.swift.
(function () {
  "use strict";

  // The upstream link opens the native settings host. Its href is set to
  // todoiste://settings by the options script; WebView.swift intercepts that
  // URL and opens the Settings window.
  var optionsObserver = new MutationObserver(function () {
    var link = Array.from(document.querySelectorAll("a")).find(function (a) {
      return a.getAttribute("href") === "todoiste://settings";
    });
    if (link) {
      link.style.textDecoration = "underline";
      optionsObserver.disconnect();
    }
  });
  optionsObserver.observe(document.body, { childList: true, subtree: true });
})();
