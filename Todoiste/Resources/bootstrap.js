// Bootstrap script injected by the Swift wrapper between mousetrap.js and
// todoist-shortcuts.js. Options (data-todoist-shortcuts-options) are set by
// a preceding inline script generated from UserDefaults — see WebView.swift.
(function () {
  "use strict";

  // Relabel the Chrome extension's "Configure options" link to indicate it
  // opens the native Todoiste Settings window. The link's href is set to
  // todoiste://settings by the options script; WebView.swift intercepts that
  // URL and opens the Settings window.
  var optionsObserver = new MutationObserver(function () {
    var link = Array.from(document.querySelectorAll("a")).find(function (a) {
      return a.textContent === "Configure todoist-shortcuts options";
    });
    if (link) {
      link.textContent = "Configure todoist-shortcuts options";
      link.style.textDecoration = "underline";
      optionsObserver.disconnect();
    }
  });
  optionsObserver.observe(document.body, { childList: true, subtree: true });
})();
