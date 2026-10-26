import AppKit

struct ShortcutSettingsDialog {
    enum Kind { case alert, confirm, prompt }
    let kind: Kind
    let message: String
    var defaultText: String? = nil
}

@MainActor
protocol ShortcutSettingsDialogPresenting: AnyObject {
    /// A non-nil result acknowledges OK; prompt results may be empty strings.
    func present(_ request: ShortcutSettingsDialog, in window: NSWindow?, completion: @escaping (String?) -> Void)
}

@MainActor
final class AppKitShortcutSettingsDialogs: ShortcutSettingsDialogPresenting {
    func present(_ request: ShortcutSettingsDialog, in window: NSWindow?, completion: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = request.message
        alert.alertStyle = .informational
        alert.addButton(withTitle: "OK")
        if request.kind != .alert { alert.addButton(withTitle: "Cancel") }
        var input: NSTextField?
        if request.kind == .prompt {
            let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 340, height: 24))
            field.stringValue = request.defaultText ?? ""
            field.setAccessibilityLabel(request.message)
            alert.accessoryView = field
            alert.window.initialFirstResponder = field
            input = field
        }
        let finish: (NSApplication.ModalResponse) -> Void = { response in
            completion(response == .alertFirstButtonReturn ? input?.stringValue ?? "" : nil)
        }
        if let window {
            alert.beginSheetModal(for: window, completionHandler: finish)
        } else {
            finish(alert.runModal())
        }
    }
}
