import Foundation
import JavaScriptCore

/// Use upstream's own defaults/migrations when comparing saved and active options.
/// This context has only in-memory storage; normalization never changes preferences.
final class ShortcutSettingsOptionsNormalizer {
    static let bundled = ShortcutSettingsOptionsNormalizer(sourceURL:
        Bundle.main.url(forResource: "common", withExtension: "js", subdirectory: "Shortcuts"))
    private let source: String?

    init(sourceURL: URL?) {
        source = sourceURL.flatMap { try? String(contentsOf: $0, encoding: .utf8) }
    }

    func normalize(_ optionsJSON: String) -> String {
        guard let source, let context = JSContext() else { return optionsJSON }
        context.setObject(optionsJSON, forKeyedSubscript: "todoisteStoredOptions" as NSString)
        context.evaluateScript("""
          var console = {debug: function() {}, log: function() {}, warn: function() {}, error: function() {}};
          var chrome = {runtime: {}, storage: {sync: {
            get: function(keys, callback) { callback({options: todoisteStoredOptions}); },
            set: function(items, callback) { if (callback) callback(); }
          }}};
          var browser = chrome;
        """)
        context.evaluateScript(source)
        let result = context.evaluateScript("""
          var todoisteNormalizedOptions = null;
          var todoisteNormalizationFailed = false;
          todoistShortcutsLoadOptions(function(value) {
            todoisteNormalizedOptions = JSON.stringify(value);
          }, function() { todoisteNormalizationFailed = true; });
          todoisteNormalizationFailed ? null : todoisteNormalizedOptions;
        """)
        guard context.exception == nil, let normalized = result?.toString(),
              let value = try? JSONSerialization.jsonObject(with: Data(normalized.utf8)) as? [String: Any],
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else {
            // An unfamiliar upstream loader still gets a conservative comparison.
            return optionsJSON
        }
        return String(decoding: data, as: UTF8.self)
    }
}
