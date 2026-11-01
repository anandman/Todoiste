import AppKit
import Sparkle

@MainActor
final class FeedObserver: NSObject, SPUUpdaterDelegate {
    var foundVersion: String?
    var finished = false
    var failure: Error?
    var noUpdateFound = false

    func updater(_ updater: SPUUpdater, didFindValidUpdate item: SUAppcastItem) {
        foundVersion = item.versionString
    }

    func updaterDidNotFindUpdate(_ updater: SPUUpdater) {
        noUpdateFound = true
    }

    func updater(_ updater: SPUUpdater, didFinishUpdateCycleFor updateCheck: SPUUpdateCheck, error: Error?) {
        failure = error
        finished = true
    }
}

@main
struct SparkleFeedTests {
    @MainActor static func main() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        let bundle = Bundle(url: URL(fileURLWithPath: CommandLine.arguments[1]))!
        let expectation = CommandLine.arguments[2]
        defer { if let id = bundle.bundleIdentifier { UserDefaults.standard.removePersistentDomain(forName: id) } }
        let observer = FeedObserver()
        let driver = SPUStandardUserDriver(hostBundle: bundle, delegate: nil)
        let updater = SPUUpdater(hostBundle: bundle, applicationBundle: bundle, userDriver: driver, delegate: observer)
        try updater.start()
        // Sparkle's probing mode never downloads, installs, or presents update UI.
        updater.checkForUpdateInformation()
        let deadline = Date().addingTimeInterval(20)
        while !observer.finished && Date() < deadline {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.02))
        }
        guard observer.finished else { throw NSError(domain: "FeedTests", code: 1,
            userInfo: [NSLocalizedDescriptionKey: "Timed out checking local fixture feed"]) }
        let noUpdate = observer.noUpdateFound
        switch expectation {
        case "newer": precondition(observer.foundVersion == "9" && observer.failure == nil, "valid signed newer version must be found")
        case "current": precondition(observer.foundVersion == nil && noUpdate, "current build must not be offered")
        case "error": precondition(observer.foundVersion == nil && observer.failure != nil && !noUpdate,
            "untrusted or unavailable feed must fail without offering an update")
        default: preconditionFailure("Unknown test expectation")
        }
        print("Sparkle local feed: \(expectation) passed")
    }
}
