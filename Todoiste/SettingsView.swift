import SwiftUI

struct SettingsView: View {
    private enum SettingsTab: Hashable {
        case `extension`
        case todoist
    }

    @AppStorage("mouse-behavior") private var mouseBehavior = "focus-follows-mouse"
    @AppStorage("cursor-movement") private var cursorMovement = "follows-task-within-section"
    @State private var selectedTab: SettingsTab = .extension
    @State private var showReloadPrompt = false

    var body: some View {
        TabView(selection: $selectedTab) {
            Form {
                Section("Keyboard Navigation") {
                    Picker("Mouse behavior", selection: $mouseBehavior) {
                        Text("Cursor follows mouse").tag("focus-follows-mouse")
                        Text("Cursor follows mouse (delay after window focus)").tag("focus-follows-mouse-delay-after-window-focus")
                        Text("Mouse doesn't move cursor").tag("no-mouse-behavior")
                    }

                    Picker("Cursor movement", selection: $cursorMovement) {
                        Text("Stays within section").tag("follows-task-within-section")
                        Text("Always follows task").tag("follows-task")
                    }
                }

                Section {
                    Text("Changes take effect after reloading.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .padding()
            .tabItem {
                Label("Extension", systemImage: "keyboard")
            }
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
            .tabItem {
                Label("Todoist", systemImage: "checklist")
            }
            .tag(SettingsTab.todoist)
        }
        .frame(width: 520, height: 300)
        .onChange(of: mouseBehavior) { _, _ in
            showReloadPrompt = true
        }
        .onChange(of: cursorMovement) { _, _ in
            showReloadPrompt = true
        }
        .alert("Reload Todoiste?", isPresented: $showReloadPrompt) {
            Button("Reload Now") {
                NotificationCenter.default.post(name: .reloadWebView, object: nil)
            }
            Button("Later", role: .cancel) {}
        } message: {
            Text("Apply your extension setting changes now?")
        }
    }
}
