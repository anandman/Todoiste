import AppKit
import SwiftUI

/// Observe the window without replacing the delegate owned by SwiftUI.
struct SettingsWindowCloseObserver: NSViewRepresentable {
    let onClose: () -> Void

    func makeNSView(context: Context) -> SettingsWindowCloseObserverView {
        SettingsWindowCloseObserverView(onClose: onClose)
    }

    func updateNSView(_ view: SettingsWindowCloseObserverView, context: Context) {
        view.onClose = onClose
    }
}

final class SettingsWindowCloseObserverView: NSView {
    var onClose: () -> Void
    private var observer: NSObjectProtocol?

    init(onClose: @escaping () -> Void) {
        self.onClose = onClose
        super.init(frame: .zero)
    }

    required init?(coder: NSCoder) { nil }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        guard let window else { return }
        observer = NotificationCenter.default.addObserver(forName: NSWindow.willCloseNotification,
            object: window, queue: .main) { [weak self] _ in
            // Let the settings window finish closing before presenting a dialog.
            guard let handler = self?.onClose else { return }
            DispatchQueue.main.async(execute: handler)
        }
    }

    deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
    }
}

@MainActor
enum SettingsReloadReminder {
    private static var isPresenting = false

    static func presentIfNeeded(_ state: ShortcutSettingsReloadState) {
        guard state.needsReload, !isPresenting else { return }
        isPresenting = true
        let alert = NSAlert()
        alert.messageText = "Reload Todoiste to apply your settings?"
        alert.informativeText = "Your changes are saved. Reload now to use them in the main window, or apply them the next time you reload or open Todoiste."
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Reload Now")
        alert.addButton(withTitle: "Later")
        let finish: (NSApplication.ModalResponse) -> Void = { response in
            isPresenting = false
            if response == .alertFirstButtonReturn {
                state.markCurrentSettingsApplied()
                NotificationCenter.default.post(name: .reloadWebView, object: nil)
            }
        }
        if let window = NSApp.mainWindow, window.isVisible {
            alert.beginSheetModal(for: window, completionHandler: finish)
        } else {
            finish(alert.runModal())
        }
    }
}
