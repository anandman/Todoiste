import Foundation

@main
struct SettingsStoreTests {
    static func require(_ value: @autoclosure () -> Bool, _ message: String) {
        if !value() { fputs("FAILED: \(message)\n", stderr); exit(1) }
    }

    static func main() throws {
        let suite = "com.todoiste.settings-test.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let normalizer = ShortcutSettingsOptionsNormalizer(sourceURL: URL(fileURLWithPath: CommandLine.arguments[1]))
        let freshStore = ShortcutSettingsStore(defaults: defaults)
        let freshState = ShortcutSettingsReloadState(store: freshStore, normalize: normalizer.normalize)
        let normalizedDefaults = normalizer.normalize("{}")
        require(normalizedDefaults != "{}", "exact upstream loader supplies its defaults")
        _ = try freshStore.perform(operation: "set", argument: ["options": normalizedDefaults])
        require(!freshState.needsReload, "opening settings only canonicalizes defaults, without a false reminder")
        _ = try freshStore.perform(operation: "set", argument: ["options": "{\"mouse-behavior\":\"no-mouse-behavior\"}"])
        require(freshState.needsReload, "changing a default needs reload")
        _ = try freshStore.perform(operation: "set", argument: ["options": normalizedDefaults])
        require(!freshState.needsReload, "returning to an originally omitted default clears the reminder")
        let partialJSON = "{\"mouse-behavior\":\"no-mouse-behavior\"}"
        _ = try freshStore.perform(operation: "set", argument: ["options": partialJSON])
        let partialState = ShortcutSettingsReloadState(store: freshStore, normalize: normalizer.normalize)
        _ = try freshStore.perform(operation: "set", argument: ["options": normalizer.normalize(partialJSON)])
        require(!partialState.needsReload, "filling defaults in partially saved options is a no-op")
        let unknown = "{\"future\":{\"nested\":[true,null,\"x\"]}}"
        let decodedUnknown = try JSONSerialization.jsonObject(with: Data(normalizer.normalize(unknown).utf8)) as! [String: Any]
        require((decodedUnknown["future"] as? [String: Any])?["nested"] is [Any], "upstream normalization retains future nested options")
        defaults.removePersistentDomain(forName: suite)
        defaults.set("no-mouse-behavior", forKey: "mouse-behavior")
        defaults.set("follows-task", forKey: "cursor-movement")
        let store = ShortcutSettingsStore(defaults: defaults)
        let reloadState = ShortcutSettingsReloadState(store: store, normalize: normalizer.normalize)
        require(!reloadState.needsReload, "opening unchanged settings needs no reminder")
        let legacy = try JSONSerialization.jsonObject(with: Data(store.optionsJSON().utf8)) as! [String: Any]
        require(legacy["mouse-behavior"] as? String == "no-mouse-behavior", "legacy mouse setting migrated")
        require(legacy["cursor-movement"] as? String == "always-follows-task", "legacy native cursor value normalized")

        let options: [String: Any] = ["selection-prefix": ",", "keybindings": ["default": ["done": [], "deselect-all": ["; n"]]], "future": ["nested": [true, NSNull(), "quote\" slash\\ newline\n\u{2028}</script>"]]]
        let json = String(data: try JSONSerialization.data(withJSONObject: options), encoding: .utf8)!
        _ = try store.perform(operation: "set", argument: ["options": json, "new-key": [1, false, NSNull()]])
        require(reloadState.needsReload, "saved options require reload before reminder is cleared")
        reloadState.markApplied(optionsJSON: try store.optionsJSON())
        require(!reloadState.needsReload, "applying the saved options clears the reminder")
        _ = try store.perform(operation: "set", argument: ["unrelated": true])
        require(!reloadState.needsReload, "outer storage writes do not require runtime reload")
        _ = try store.perform(operation: "set", argument: ["options": "{\"temporary\":true}"])
        require(reloadState.needsReload, "new options after a reload require another reload")
        _ = try store.perform(operation: "set", argument: ["options": json])
        require(!reloadState.needsReload, "reverting to applied options avoids a stale reminder")
        _ = try store.perform(operation: "set", argument: ["options": "{\"temporary\":true}"])
        reloadState.markCurrentSettingsApplied()
        require(!reloadState.needsReload, "explicit reload acknowledges current settings")
        _ = try store.perform(operation: "set", argument: ["options": json])
        let restored = ShortcutSettingsStore(defaults: defaults)
        let result = try restored.perform(operation: "get", argument: NSNull()) as! [String: Any]
        require(result["new-key"] is [Any], "unknown outer storage keys survive restart")
        let decoded = try JSONSerialization.jsonObject(with: Data(restored.optionsJSON().utf8)) as! NSDictionary
        require(decoded == options as NSDictionary, "full settings JSON survives roundtrip")
        let selected = try restored.perform(operation: "get", argument: ["missing": false, "options": "{}"]) as! [String: Any]
        require(selected["missing"] as? Bool == false, "get defaults are honored")
        let before = defaults.data(forKey: ShortcutSettingsStore.storageKey)
        do {
            _ = try restored.perform(operation: "set", argument: ["invalid": Date()])
            fatalError("non-JSON write should fail")
        } catch {}
        require(defaults.data(forKey: ShortcutSettingsStore.storageKey) == before, "failed write leaves storage intact")
        _ = try restored.perform(operation: "remove", argument: ["new-key"])
        let removed = try restored.perform(operation: "get", argument: NSNull()) as! [String: Any]
        require(removed["new-key"] == nil, "remove deletes only requested keys")
        _ = try restored.perform(operation: "clear", argument: NSNull())
        let cleared = try restored.optionsJSON()
        require(cleared == "{}", "clear does not resurrect old preferences")
        require(reloadState.needsReload, "resetting saved options also requires runtime reload")
        print("Native settings store: migration, arbitrary JSON, restart, defaults, rejected writes and reset passed")
    }
}
