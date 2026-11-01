import Foundation

@main
struct UpdaterConfigurationTests {
    static func require(_ value: @autoclosure () -> Bool, _ message: String) {
        if !value() { fputs("FAILED: \(message)\n", stderr); exit(1) }
    }

    static func main() {
        // Public RFC8032 test vector, only for isolated test builds.
        let key = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
        let info: [String: Any] = [
            "SUFeedURL": "https://github.com/anandman/Todoiste/releases/latest/download/appcast.xml",
            "SUPublicEDKey": key, "SURequireSignedFeed": true,
            "SUVerifyUpdateBeforeExtraction": true, "SUAllowsAutomaticUpdates": false,
            "SUEnableInstallerLauncherService": false, "SUEnableDownloaderService": false,
        ]
        require(UpdaterConfiguration.disabledReason(info: info, bundleID: "com.todoiste.app", arguments: []) == nil,
            "a configured production updater can start")
        require(UpdaterConfiguration.disabledReason(info: info, bundleID: "com.todoiste.app", arguments: ["--settings-only"]) != nil,
            "settings smoke cannot start production update traffic")
        require(UpdaterConfiguration.disabledReason(info: info, bundleID: "com.todoiste.settings-preview", arguments: []) != nil,
            "experimental previews cannot install production updates")
        for (field, invalid) in [("SUPublicEDKey", "" as Any), ("SUPublicEDKey", "placeholder" as Any),
                                 ("SUFeedURL", "http://github.com/feed" as Any),
                                 ("SUFeedURL", "https://example.org/feed" as Any),
                                 ("SURequireSignedFeed", false as Any),
                                 ("SUVerifyUpdateBeforeExtraction", false as Any),
                                 ("SUAllowsAutomaticUpdates", true as Any),
                                 ("SUEnableInstallerLauncherService", true as Any),
                                 ("SUEnableDownloaderService", true as Any)] {
            var altered = info; altered[field] = invalid
            require(UpdaterConfiguration.disabledReason(info: altered, bundleID: "com.todoiste.app", arguments: []) != nil,
                "unsafe or incomplete updater configuration fails closed: \(field)")
        }
        print("Updater configuration: production, preview isolation, signed feed and prompt-only guards passed")
    }
}
