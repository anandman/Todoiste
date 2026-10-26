import SwiftUI

struct SettingsView: View {
    private enum SettingsTab: Hashable {
        case `extension`
        case todoist
    }

    @State private var selectedTab: SettingsTab = .extension
    @State private var loadError: String?
    @State private var settingsIdentity = UUID()
    private let reloadState = ShortcutSettingsReloadState.shared

    var body: some View {
        TabView(selection: $selectedTab) {
            VStack(spacing: 0) {
                if let resources = try? ShortcutResources.bundled(), loadError == nil {
                    ShortcutSettingsWebView(resources: resources, onFailure: { loadError = $0 })
                        .id(settingsIdentity)
                } else {
                    ContentUnavailableView {
                        Label("Extension settings unavailable", systemImage: "keyboard")
                    } description: {
                        Text("The extension settings page could not be loaded.")
                    } actions: {
                        Button("Try Again") {
                            loadError = nil
                            settingsIdentity = UUID()
                        }
                    }
                }
                Divider()
                HStack {
                    Text("Changes take effect after reloading Todoiste.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Reload Todoiste") {
                        reloadState.markCurrentSettingsApplied()
                        NotificationCenter.default.post(name: .reloadWebView, object: nil)
                    }
                }
                .padding()
            }
            .tabItem { Label("Extension", systemImage: "keyboard") }
            .tag(SettingsTab.extension)

            Form {
                Section("Todoist App Settings") {
                    Text("Open Todoist's built-in settings page in the main app window.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Open Todoist Settings…") {
                        NotificationCenter.default.post(name: .openTodoistWebSettings, object: nil)
                    }
                }
            }
            .formStyle(.grouped)
            .padding()
            .tabItem { Label("Todoist", systemImage: "checklist") }
            .tag(SettingsTab.todoist)
        }
        .frame(width: 780, height: 720)
        .background(SettingsWindowCloseObserver {
            SettingsReloadReminder.presentIfNeeded(reloadState)
        })
    }
}
