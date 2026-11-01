import Combine
import Sparkle
import SwiftUI

/// One updater for the lifetime of the app; Sparkle owns checking and installation.
@MainActor
final class AppUpdater: ObservableObject {
    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var automaticallyChecksForUpdates = false
    let unavailableReason: String?
    private let controller: SPUStandardUpdaterController?

    init() {
        unavailableReason = UpdaterConfiguration.disabledReason(info: Bundle.main.infoDictionary ?? [:],
            bundleID: Bundle.main.bundleIdentifier, arguments: CommandLine.arguments)
        guard unavailableReason == nil else {
            controller = nil
            return
        }
        let controller = SPUStandardUpdaterController(startingUpdater: false,
            updaterDelegate: nil, userDriverDelegate: nil)
        self.controller = controller
        controller.updater.publisher(for: \.canCheckForUpdates)
            .receive(on: RunLoop.main).assign(to: &$canCheckForUpdates)
        controller.updater.publisher(for: \.automaticallyChecksForUpdates)
            .receive(on: RunLoop.main).assign(to: &$automaticallyChecksForUpdates)
        controller.startUpdater()
    }

    func checkForUpdates() {
        guard canCheckForUpdates else { return }
        controller?.checkForUpdates(nil)
    }

    func setAutomaticallyChecksForUpdates(_ enabled: Bool) {
        controller?.updater.automaticallyChecksForUpdates = enabled
    }
}

struct UpdatesSettingsView: View {
    @ObservedObject var updater: AppUpdater

    var body: some View {
        Form {
            Section("App Updates") {
                if let reason = updater.unavailableReason {
                    Text(reason).foregroundStyle(.secondary)
                } else {
                    Toggle("Check for updates automatically", isOn: Binding(
                        get: { updater.automaticallyChecksForUpdates },
                        set: updater.setAutomaticallyChecksForUpdates))
                    Text("Todoiste asks before downloading and installing an update.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Check for Updates…", action: updater.checkForUpdates)
                        .disabled(!updater.canCheckForUpdates)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}
