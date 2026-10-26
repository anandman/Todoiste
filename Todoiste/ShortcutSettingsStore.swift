import Foundation

extension Notification.Name {
    static let shortcutSettingsChanged = Notification.Name("shortcutSettingsChanged")
}

/// Chrome-style JSON storage, kept separate from other native preferences.
/// Only the one-time migration knows legacy option names.
final class ShortcutSettingsStore {
    static let storageKey = "todoiste.shortcuts.storage.v1"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    enum StorageError: LocalizedError {
        case invalidData, invalidArgument, unsupportedOperation
        var errorDescription: String? {
            switch self {
            case .invalidData: return "Saved extension settings are not valid JSON."
            case .invalidArgument: return "Extension settings must contain JSON-compatible values."
            case .unsupportedOperation: return "The extension requested an unsupported storage operation."
            }
        }
    }

    private func load() throws -> [String: Any] {
        if let saved = defaults.object(forKey: Self.storageKey) {
            guard let data = saved as? Data,
                  let items = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                throw StorageError.invalidData
            }
            return items
        }
        var options: [String: Any] = [:]
        if let mouse = defaults.string(forKey: "mouse-behavior") {
            options["mouse-behavior"] = mouse
        }
        if let cursor = defaults.string(forKey: "cursor-movement") {
            // Older native UI used a tag that differed from upstream's value.
            options["cursor-movement"] = cursor == "follows-task" ? "always-follows-task" : cursor
        }
        let data = try JSONSerialization.data(withJSONObject: options, options: [.sortedKeys])
        let items: [String: Any] = ["options": String(decoding: data, as: UTF8.self)]
        try save(items, notify: false)
        return items
    }

    private func save(_ items: [String: Any], notify: Bool = true) throws {
        guard JSONSerialization.isValidJSONObject(items) else { throw StorageError.invalidArgument }
        let data = try JSONSerialization.data(withJSONObject: items, options: [.sortedKeys])
        guard data != defaults.data(forKey: Self.storageKey) else { return }
        defaults.set(data, forKey: Self.storageKey)
        if notify { NotificationCenter.default.post(name: .shortcutSettingsChanged, object: self) }
    }

    private func keys(_ argument: Any, in items: [String: Any]) throws -> [String] {
        if argument is NSNull { return Array(items.keys) }
        if let key = argument as? String { return [key] }
        if let keys = argument as? [String] { return keys }
        throw StorageError.invalidArgument
    }

    func perform(operation: String, argument: Any) throws -> Any {
        var items = try load()
        switch operation {
        case "get":
            if var result = argument as? [String: Any] {
                for key in result.keys { if let value = items[key] { result[key] = value } }
                return result
            }
            var result: [String: Any] = [:]
            for key in try keys(argument, in: items) { result[key] = items[key] }
            return result
        case "set":
            guard let updates = argument as? [String: Any], JSONSerialization.isValidJSONObject(updates) else {
                throw StorageError.invalidArgument
            }
            items.merge(updates) { _, new in new }
            try save(items)
            return NSNull()
        case "remove":
            guard !(argument is NSNull) else { throw StorageError.invalidArgument }
            for key in try keys(argument, in: items) { items.removeValue(forKey: key) }
            try save(items)
            return NSNull()
        case "clear":
            try save([:])
            return NSNull()
        case "getBytesInUse":
            return try keys(argument, in: items).reduce(0) { count, key in
                guard let value = items[key] else { return count }
                let encoded = try JSONSerialization.data(withJSONObject: value,
                    options: [.fragmentsAllowed, .withoutEscapingSlashes, .sortedKeys])
                return count + key.utf8.count + encoded.count
            }
        default:
            throw StorageError.unsupportedOperation
        }
    }

    func optionsJSON() throws -> String {
        let items = try load()
        guard let raw = items["options"] else { return "{}" }
        guard let serialized = raw as? String,
              let value = try JSONSerialization.jsonObject(with: Data(serialized.utf8)) as? [String: Any] else {
            throw StorageError.invalidData
        }
        return String(decoding: try JSONSerialization.data(withJSONObject: value,
            options: [.sortedKeys]), as: UTF8.self)
    }

    func injectionSource() throws -> String {
        try Self.injectionSource(optionsJSON: optionsJSON())
    }

    static func injectionSource(optionsJSON: String) throws -> String {
        let literal = try JSONSerialization.data(withJSONObject: optionsJSON, options: [.fragmentsAllowed])
        return """
        (function() {
          function apply() {
            if (!document.body) { setTimeout(apply, 50); return; }
            document.body.setAttribute('data-todoist-shortcuts-options', \(String(decoding: literal, as: UTF8.self)));
            document.body.setAttribute('data-todoist-shortcuts-options-url', 'todoiste://settings');
          }
          apply();
        })();
        """
    }
}
