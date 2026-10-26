import AppKit
import SwiftUI
import WebKit

struct ShortcutSettingsWebView: NSViewRepresentable {
    let resources: ShortcutResources
    var store = ShortcutSettingsStore()
    var onFailure: (String) -> Void = { _ in }

    func makeCoordinator() -> ShortcutSettingsBridge {
        ShortcutSettingsBridge(resources: resources, store: store, onFailure: onFailure)
    }

    func makeNSView(context: Context) -> WKWebView { context.coordinator.makeWebView() }
    func updateNSView(_ webView: WKWebView, context: Context) {}

    static func dismantleNSView(_ webView: WKWebView, coordinator: ShortcutSettingsBridge) {
        webView.stopLoading()
        webView.configuration.userContentController.removeScriptMessageHandler(forName: ShortcutSettingsBridge.channel)
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
    }
}

/// This capability belongs only to a separate, ephemeral local settings view.
/// The main Todoist web view never receives this message handler or adapter.
@MainActor
final class ShortcutSettingsBridge: NSObject, WKScriptMessageHandlerWithReply, WKNavigationDelegate, WKUIDelegate {
    static let channel = "todoisteSettings"
    let resources: ShortcutResources
    private let store: ShortcutSettingsStore
    private let onFailure: (String) -> Void
    private let dialogs: ShortcutSettingsDialogPresenting
    private weak var webView: WKWebView?
    private(set) var readCount = 0
    private(set) var loadFailure: String?

    init(resources: ShortcutResources, store: ShortcutSettingsStore,
         dialogs: ShortcutSettingsDialogPresenting? = nil, onFailure: @escaping (String) -> Void = { _ in }) {
        self.resources = resources
        self.store = store
        self.onFailure = onFailure
        self.dialogs = dialogs ?? AppKitShortcutSettingsDialogs()
    }

    func makeWebView() -> WKWebView {
        let controller = WKUserContentController()
        controller.addScriptMessageHandler(self, contentWorld: .page, name: Self.channel)
        controller.addUserScript(WKUserScript(source: resources.adapterSource, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController = controller
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        webView = view
        view.loadFileURL(resources.optionsURL, allowingReadAccessTo: resources.directory)
        return view
    }

    func authorizes(frameURL: URL?, isMainFrame: Bool) -> Bool {
        guard isMainFrame, let frameURL, frameURL.isFileURL else { return false }
        return frameURL.standardizedFileURL.resolvingSymlinksInPath().path == resources.optionsURL.path
    }

    func userContentController(_ userContentController: WKUserContentController,
        didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard message.webView === webView,
              authorizes(frameURL: message.frameInfo.request.url, isMainFrame: message.frameInfo.isMainFrame),
              let body = message.body as? [String: Any], let operation = body["operation"] as? String else {
            replyHandler(nil, "Settings storage is available only to the local settings page.")
            return
        }
        do {
            let result = try store.perform(operation: operation, argument: body["argument"] ?? NSNull())
            if operation == "get" { readCount += 1 }
            replyHandler(result, nil)
        } catch {
            replyHandler(nil, error.localizedDescription)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
        decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if authorizes(frameURL: navigationAction.request.url, isMainFrame: navigationAction.targetFrame?.isMainFrame == true) {
            decisionHandler(.allow)
        } else {
            if navigationAction.navigationType == .linkActivated { openExternal(navigationAction.request.url) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
        for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if navigationAction.navigationType == .linkActivated { openExternal(navigationAction.request.url) }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        guard self.webView === webView, authorizes(frameURL: frame.request.url, isMainFrame: frame.isMainFrame) else {
            completionHandler()
            return
        }
        dialogs.present(ShortcutSettingsDialog(kind: .alert, message: message), in: webView.window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
        initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard self.webView === webView, authorizes(frameURL: frame.request.url, isMainFrame: frame.isMainFrame) else {
            completionHandler(false)
            return
        }
        dialogs.present(ShortcutSettingsDialog(kind: .confirm, message: message), in: webView.window) {
            completionHandler($0 != nil)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
        defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        guard self.webView === webView, authorizes(frameURL: frame.request.url, isMainFrame: frame.isMainFrame) else {
            completionHandler(nil)
            return
        }
        dialogs.present(ShortcutSettingsDialog(kind: .prompt, message: prompt, defaultText: defaultText),
            in: webView.window, completion: completionHandler)
    }

    private func openExternal(_ url: URL?) {
        guard let url, ["https", "http"].contains(url.scheme?.lowercased() ?? "") else { return }
        NSWorkspace.shared.open(url)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        loadFailure = error.localizedDescription
        onFailure(error.localizedDescription)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        loadFailure = error.localizedDescription
        onFailure(error.localizedDescription)
    }
}
