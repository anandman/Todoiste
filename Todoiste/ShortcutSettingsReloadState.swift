import Foundation

extension Notification.Name {
    static let reloadWebView = Notification.Name("reloadWebView")
}

/// Compare the saved options with the snapshot used by the running shortcuts.
/// Callers use this on the main thread alongside the settings and Todoist views.
final class ShortcutSettingsReloadState {
    static let shared = ShortcutSettingsReloadState()
    private let store: ShortcutSettingsStore
    private let normalize: (String) -> String
    private var appliedOptionsJSON: String?

    init(store: ShortcutSettingsStore = ShortcutSettingsStore(),
         normalize: @escaping (String) -> String = ShortcutSettingsOptionsNormalizer.bundled.normalize) {
        self.store = store
        self.normalize = normalize
        appliedOptionsJSON = (try? store.optionsJSON()).map(normalize)
    }

    var needsReload: Bool {
        guard let saved = try? store.optionsJSON() else { return false }
        return normalize(saved) != appliedOptionsJSON
    }

    func markApplied(optionsJSON: String) {
        appliedOptionsJSON = normalize(optionsJSON)
    }

    func markCurrentSettingsApplied() {
        if let saved = try? store.optionsJSON() { markApplied(optionsJSON: saved) }
    }
}
