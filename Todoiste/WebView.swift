import SwiftUI
import AVFoundation
import UserNotifications
import WebKit

extension Notification.Name {
    static let reloadWebView = Notification.Name("reloadWebView")
    static let openSettings = Notification.Name("openSettings")
    static let openTodoistWebSettings = Notification.Name("openTodoistWebSettings")
}

struct TodoistWebView: NSViewRepresentable {

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let contentController = WKUserContentController()
        context.coordinator.attachNotificationHandler(to: contentController)
        Self.injectScripts(into: contentController)
        config.userContentController = contentController
        config.websiteDataStore = .default()

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator

        // Use a standard Safari user agent so Todoist serves its full web app
        webView.customUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"

        context.coordinator.webView = webView
        context.coordinator.contentController = contentController

        webView.load(URLRequest(url: URL(string: "https://todoist.com/app")!))
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    // MARK: - Script Injection

    static func injectScripts(into controller: WKUserContentController) {
        injectNotificationBridge(into: controller)
        injectFile(named: "mousetrap", into: controller)
        injectOptions(into: controller)
        injectFile(named: "bootstrap", into: controller)
        injectFile(named: "todoist-shortcuts", into: controller)
    }

    // Bridge web notifications to native notifications because some WKWebView
    // contexts deny the Web Notification permission flow.
    private static func injectNotificationBridge(into controller: WKUserContentController) {
        let source = """
        (function () {
          var channel = (window.webkit && window.webkit.messageHandlers) ? window.webkit.messageHandlers.todoisteNotifications : null;
          function post(payload) {
            if (!channel) return;
            try {
              channel.postMessage(payload || {});
            } catch (_) {}
          }

          if (!("Notification" in window)) return;

          try {
            var OriginalNotification = Notification;

            function WrappedNotification(title, options) {
              post({
                title: String(title || ""),
                body: (options && options.body) ? String(options.body) : ""
              });
              return { close: function() {} };
            }

            WrappedNotification.prototype = OriginalNotification.prototype;
            Object.setPrototypeOf(WrappedNotification, OriginalNotification);
            WrappedNotification.permission = "granted";
            WrappedNotification.requestPermission = function(callback) {
              if (typeof callback === "function") callback("granted");
              return Promise.resolve("granted");
            };
            window.Notification = WrappedNotification;
          } catch (_) {}
        })();
        """

        controller.addUserScript(WKUserScript(
            source: source,
            injectionTime: .atDocumentStart,
            forMainFrameOnly: true
        ))
    }

    private static func injectFile(named name: String, into controller: WKUserContentController) {
        guard let url = Bundle.main.url(forResource: name, withExtension: "js"),
              let source = try? String(contentsOf: url, encoding: .utf8) else {
            print("[Todoiste] Failed to load \(name).js from bundle")
            return
        }
        controller.addUserScript(WKUserScript(
            source: source,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        ))
    }

    // Reads current settings from UserDefaults and injects them as the
    // data-todoist-shortcuts-options attribute on <body>. Also sets the
    // options URL to a custom scheme so the help modal link opens Settings.
    static func injectOptions(into controller: WKUserContentController) {
        let mouseBehavior = UserDefaults.standard.string(forKey: "mouse-behavior") ?? "focus-follows-mouse"
        let cursorMovement = UserDefaults.standard.string(forKey: "cursor-movement") ?? "follows-task-within-section"
        let opts: [String: String] = [
            "mouse-behavior": mouseBehavior,
            "cursor-movement": cursorMovement,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: opts),
              let json = String(data: data, encoding: .utf8) else { return }

        // Escape for embedding as a JS string literal
        let escaped = json
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")

        let source = """
        (function() {
          function apply() {
            if (!document.body) { setTimeout(apply, 50); return; }
            document.body.setAttribute('data-todoist-shortcuts-options', "\(escaped)");
            document.body.setAttribute('data-todoist-shortcuts-options-url', 'todoiste://settings');
          }
          apply();
        })();
        """
        controller.addUserScript(WKUserScript(
            source: source,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        ))
    }

    // MARK: - Coordinator

    class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        weak var webView: WKWebView?
        weak var contentController: WKUserContentController?
        private var reloadObserver: NSObjectProtocol?
        private var openWebSettingsObserver: NSObjectProtocol?
        private var hasRequestedNativeNotificationAuthorization = false
        private let notificationMessageHandlerName = "todoisteNotifications"

        override init() {
            super.init()
            reloadObserver = NotificationCenter.default.addObserver(
                forName: .reloadWebView,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                guard let self,
                      let wv = self.webView,
                      let cc = self.contentController else { return }
                cc.removeAllUserScripts()
                TodoistWebView.injectScripts(into: cc)
                wv.load(URLRequest(url: URL(string: "https://app.todoist.com/app")!))
            }

            openWebSettingsObserver = NotificationCenter.default.addObserver(
                forName: .openTodoistWebSettings,
                object: nil,
                queue: .main
            ) { [weak self] _ in
                guard let webView = self?.webView,
                      let url = URL(string: "https://app.todoist.com/app/settings") else { return }
                webView.load(URLRequest(url: url))
            }

        }

        deinit {
            if let obs = reloadObserver {
                NotificationCenter.default.removeObserver(obs)
            }
            if let obs = openWebSettingsObserver {
                NotificationCenter.default.removeObserver(obs)
            }
            contentController?.removeScriptMessageHandler(forName: notificationMessageHandlerName)
        }

        func attachNotificationHandler(to contentController: WKUserContentController) {
            contentController.removeScriptMessageHandler(forName: notificationMessageHandlerName)
            contentController.add(self, name: notificationMessageHandlerName)
        }

        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == notificationMessageHandlerName else { return }
            if let body = message.body as? [String: Any] {
                let title = (body["title"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                let text = body["body"] as? String
                sendNativeNotification(title: title.isEmpty ? "Todoist" : title, body: text)
            }
        }

        private func sendNativeNotification(title: String, body: String?) {
            let content = UNMutableNotificationContent()
            content.title = title
            if let body, !body.isEmpty {
                content.body = body
            }
            content.sound = .default

            let id = "todoiste.web.\(UUID().uuidString)"
            let request = UNNotificationRequest(
                identifier: id,
                content: content,
                trigger: UNTimeIntervalNotificationTrigger(timeInterval: 0.1, repeats: false)
            )

            UNUserNotificationCenter.current().add(request) { error in
                if let error {
                    print("[Todoiste] Failed to post native notification: \(error)")
                }
            }
        }

        // Navigation policy: keep Todoist + auth providers in-app, open everything else externally
        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.allow)
                return
            }

            // Custom URL scheme: open native Settings window
            if url.scheme == "todoiste" {
                NotificationCenter.default.post(name: .openSettings, object: nil)
                decisionHandler(.cancel)
                return
            }

            let host = url.host ?? ""
            let path = url.path

            // Keep only Todoist app/auth routes in-app. Non-app routes (e.g. file URLs)
            // should open externally so the webview doesn't get stuck on a document view.
            let isTodoistInAppRoute =
                host.hasSuffix("todoist.com") &&
                (path == "/" || path.hasPrefix("/app") || path.hasPrefix("/auth") || path.hasPrefix("/oauth"))

            // OAuth providers should remain in-app.
            let oauthDomains = [
                "google.com",
                "accounts.google.com",
                "appleid.apple.com",
                "facebook.com",
            ]
            let isOAuthRoute = oauthDomains.contains { host.hasSuffix($0) }

            if isTodoistInAppRoute || isOAuthRoute {
                decisionHandler(.allow)
                return
            }

            // If a non-app URL targets the main frame, open externally to keep the
            // Todoist app UI in the webview.
            if navigationAction.targetFrame?.isMainFrame != false {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }

            // User-initiated links to external sites in subframes open externally.
            if navigationAction.navigationType == .linkActivated {
                NSWorkspace.shared.open(url)
                decisionHandler(.cancel)
                return
            }

            // Allow other requests (API calls, resources, etc.)
            decisionHandler(.allow)
        }

        // Handle popup windows (e.g., OAuth flows that open a new window)
        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            if let url = navigationAction.request.url {
                // Custom URL scheme: open native Settings window
                if url.scheme == "todoiste" {
                    NotificationCenter.default.post(name: .openSettings, object: nil)
                } else {
                    // Load popup URLs in the main webview (e.g., OAuth flows)
                    webView.load(URLRequest(url: url))
                }
            }
            return nil
        }

        // Enable <input type="file"> pickers in WKWebView (attachments, uploads).
        func webView(
            _ webView: WKWebView,
            runOpenPanelWith parameters: WKOpenPanelParameters,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping ([URL]?) -> Void
        ) {
            let panel = NSOpenPanel()
            panel.canChooseFiles = true
            panel.canChooseDirectories = false
            panel.allowsMultipleSelection = parameters.allowsMultipleSelection

            panel.begin { response in
                completionHandler(response == .OK ? panel.urls : nil)
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            requestNativeNotificationAuthorizationIfNeeded()
        }

        private func requestNativeNotificationAuthorizationIfNeeded() {
            guard !hasRequestedNativeNotificationAuthorization else { return }
            hasRequestedNativeNotificationAuthorization = true

            let center = UNUserNotificationCenter.current()
            center.getNotificationSettings { settings in
                switch settings.authorizationStatus {
                case .authorized:
                    return
                case .provisional:
                    return
                case .ephemeral:
                    return
                case .denied:
                    return
                case .notDetermined:
                    center.requestAuthorization(options: [.alert, .badge, .sound]) { _, _ in }
                @unknown default:
                    return
                }
            }
        }

        // Handle Web Notification permission requests from Todoist.
        @available(macOS 11.0, *)
        func webView(
            _ webView: WKWebView,
            requestNotificationPermissionFor securityOrigin: WKSecurityOrigin,
            decisionHandler: @escaping (WKPermissionDecision) -> Void
        ) {
            let center = UNUserNotificationCenter.current()

            center.getNotificationSettings { settings in
                DispatchQueue.main.async {
                    switch settings.authorizationStatus {
                    case .authorized, .provisional, .ephemeral:
                        decisionHandler(.grant)
                    case .denied:
                        decisionHandler(.deny)
                    case .notDetermined:
                        center.requestAuthorization(options: [.alert, .badge, .sound]) { granted, _ in
                            DispatchQueue.main.async {
                                decisionHandler(granted ? .grant : .deny)
                            }
                        }
                    @unknown default:
                        decisionHandler(.deny)
                    }
                }
            }
        }

        // Handle getUserMedia permissions (e.g., Todoist Ramble microphone input).
        @available(macOS 12.0, *)
        func webView(
            _ webView: WKWebView,
            requestMediaCapturePermissionFor origin: WKSecurityOrigin,
            initiatedByFrame frame: WKFrameInfo,
            type: WKMediaCaptureType,
            decisionHandler: @escaping (WKPermissionDecision) -> Void
        ) {
            func decide(_ decision: WKPermissionDecision) {
                DispatchQueue.main.async {
                    decisionHandler(decision)
                }
            }

            switch type {
            case .microphone:
                handleMicrophonePermission(decide)
            case .camera:
                handleCameraPermission(decide)
            case .cameraAndMicrophone:
                handleCameraAndMicrophonePermission(decide)
            @unknown default:
                decide(.deny)
            }
        }

        @available(macOS 12.0, *)
        private func handleMicrophonePermission(_ decide: @escaping (WKPermissionDecision) -> Void) {
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .authorized:
                decide(.grant)
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    decide(granted ? .grant : .deny)
                }
            case .denied, .restricted:
                decide(.deny)
            @unknown default:
                decide(.deny)
            }
        }

        @available(macOS 12.0, *)
        private func handleCameraPermission(_ decide: @escaping (WKPermissionDecision) -> Void) {
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized:
                decide(.grant)
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .video) { granted in
                    decide(granted ? .grant : .deny)
                }
            case .denied, .restricted:
                decide(.deny)
            @unknown default:
                decide(.deny)
            }
        }

        @available(macOS 12.0, *)
        private func handleCameraAndMicrophonePermission(_ decide: @escaping (WKPermissionDecision) -> Void) {
            handleCameraPermission { cameraDecision in
                guard cameraDecision == .grant else {
                    decide(.deny)
                    return
                }
                self.handleMicrophonePermission { micDecision in
                    decide(micDecision == .grant ? .grant : .deny)
                }
            }
        }
    }
}
