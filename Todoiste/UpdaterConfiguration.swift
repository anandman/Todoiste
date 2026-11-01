import Foundation

enum UpdaterConfiguration {
    static let feedURL = "https://github.com/anandman/Todoiste/releases/latest/download/appcast.xml"

    static func disabledReason(info: [String: Any], bundleID: String?, arguments: [String]) -> String? {
        guard bundleID == "com.todoiste.app", !arguments.contains("--settings-only") else {
            return "Update checks are disabled in this preview."
        }
        guard info["SUFeedURL"] as? String == feedURL,
              let key = info["SUPublicEDKey"] as? String,
              let data = Data(base64Encoded: key), data.count == 32,
              data.base64EncodedString() == key,
              info["SURequireSignedFeed"] as? Bool == true,
              info["SUVerifyUpdateBeforeExtraction"] as? Bool == true,
              info["SUAllowsAutomaticUpdates"] as? Bool == false,
              info["SUEnableInstallerLauncherService"] as? Bool == false,
              info["SUEnableDownloaderService"] as? Bool == false else {
            return "Updates aren’t configured for this build."
        }
        return nil
    }
}
