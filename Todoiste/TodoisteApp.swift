import SwiftUI
import UserNotifications

final class NotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list, .sound, .badge])
    }
}

@main
struct TodoisteApp: App {
    private let notificationDelegate = NotificationDelegate()
    private let settingsOnly = CommandLine.arguments.contains("--settings-only")

    init() {
        UNUserNotificationCenter.current().delegate = notificationDelegate
    }

    var body: some Scene {
        WindowGroup {
            if settingsOnly {
                SettingsView()
            } else {
                ContentView()
                    .frame(minWidth: 800, minHeight: 600)
            }
        }
        .defaultSize(width: settingsOnly ? 780 : 1200, height: settingsOnly ? 720 : 800)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .toolbar) {
                Button("Reload") {
                    NotificationCenter.default.post(name: .reloadWebView, object: nil)
                }
                .keyboardShortcut("r")
            }
        }

        Settings {
            SettingsView()
        }
    }
}

struct ContentView: View {
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        TodoistWebView()
            .onReceive(NotificationCenter.default.publisher(for: .openSettings)) { _ in
                openSettings()
            }
    }
}
