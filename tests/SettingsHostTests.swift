import AppKit
import Foundation
import JavaScriptCore
import WebKit

@MainActor
final class ScriptedSettingsDialogs: ShortcutSettingsDialogPresenting {
    var requests: [ShortcutSettingsDialog] = []
    var replies: [String?] = ["", "", "typed value", nil]
    func present(_ request: ShortcutSettingsDialog, in window: NSWindow?, completion: @escaping (String?) -> Void) {
        requests.append(request)
        completion(replies.removeFirst())
    }
}

@main
struct SettingsHostTests {
    @MainActor static func require(_ value: @autoclosure () -> Bool, _ message: String) {
        if !value() { fputs("FAILED: \(message)\n", stderr); exit(1) }
    }

    @MainActor static func wait(_ description: String, until predicate: () -> Bool) {
        let limit = Date().addingTimeInterval(15)
        while !predicate() && Date() < limit {
            RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.01))
        }
        require(predicate(), "Timed out: \(description)")
    }

    @MainActor static func evaluate(_ web: WKWebView, _ script: String) throws -> Any {
        var output: Result<Any, Error>?
        web.callAsyncJavaScript(script, arguments: [:], in: nil, in: .page) { output = $0 }
        wait("JavaScript reply") { output != nil }
        return try output!.get()
    }

    @MainActor static func main() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let adapter = URL(fileURLWithPath: CommandLine.arguments[2])
        let resources = try ShortcutResources(directory: root, adapter: adapter)
        let suite = "com.todoiste.webkit-settings-test.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = ShortcutSettingsStore(defaults: defaults)
        let dialogs = ScriptedSettingsDialogs()
        let bridge = ShortcutSettingsBridge(resources: resources, store: store, dialogs: dialogs)
        require(bridge.authorizes(frameURL: resources.optionsURL, isMainFrame: true), "local settings page allowed")
        require(bridge.authorizes(frameURL: URL(string: resources.optionsURL.absoluteString + "#new-controls"), isMainFrame: true), "same-page anchors remain usable")
        require(!bridge.authorizes(frameURL: resources.optionsURL, isMainFrame: false), "subframe denied")
        require(!bridge.authorizes(frameURL: root.appendingPathComponent("other.html"), isMainFrame: true), "other local file denied")
        require(!bridge.authorizes(frameURL: URL(string: "https://app.todoist.com/app")!, isMainFrame: true), "remote page denied")
        let web = bridge.makeWebView()
        // A real, visible-to-WebKit view hierarchy without opening a user window.
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 800, height: 700),
            styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = web
        defer { web.stopLoading(); window.contentView = nil }
        wait("upstream page requests its settings") { bridge.readCount > 0 || bridge.loadFailure != nil }
        require(bridge.loadFailure == nil, "local upstream options page loaded")
        web.configuration.userContentController.addUserScript(WKUserScript(source: """
          window.todoisteSettingsErrors = [];
          window.addEventListener('error', e => window.todoisteSettingsErrors.push(String(e.message)));
          window.addEventListener('unhandledrejection', e => window.todoisteSettingsErrors.push(String(e.reason)));
        """, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let readsBeforeReload = bridge.readCount
        web.reload()
        wait("settings page reload") { bridge.readCount > readsBeforeReload || bridge.loadFailure != nil }
        require(bridge.loadFailure == nil, "upstream options page reloads")
        let errors = try evaluate(web, "return JSON.stringify(window.todoisteSettingsErrors || []);") as! String
        require(errors == "[]", "upstream UI starts without JavaScript errors: \(errors)")
        let confirmed = try evaluate(web, "return confirm('Apply extension settings?');") as! Bool
        require(confirmed, "upstream confirmation dialogs receive the user's response")
        _ = try evaluate(web, "alert('Upstream load error'); return 'acknowledged';")
        let entered = try evaluate(web, "return prompt('New shortcut', 'original');") as! String
        require(entered == "typed value", "upstream prompts return user text")
        let canceled = try evaluate(web, "return prompt('Cancel this request', 'original');")
        require(canceled is NSNull, "cancelled prompts return null")
        require(dialogs.requests.count == 4 && dialogs.requests[1].message == "Upstream load error" &&
            dialogs.requests[2].defaultText == "original", "dialog messages and defaults are preserved")
        let iconURL = try evaluate(web, "return chrome.runtime.getURL('icon128.png');") as! String
        require(iconURL == resources.directory.appendingPathComponent("icon128.png").absoluteString,
            "runtime resource paths resolve within the actual bundle")
        let result = try evaluate(web, """
          await chrome.storage.sync.get(null);
          const value = {future: {nested: [true, null, 'quote" slash\\\\ line\\n\\u2028</script>']},
            'selection-prefix': ',', keybindings: {default: {'deselect-all': ['; n']}}};
          await chrome.storage.sync.set({options: JSON.stringify(value), futureStorage: [true, null]});
          const read = await browser.storage.sync.get(null);
          return JSON.stringify({value: JSON.parse(read.options), extra: read.futureStorage});
        """) as! String
        let saved = try JSONSerialization.jsonObject(with: Data(result.utf8)) as! [String: Any]
        let native = try JSONSerialization.jsonObject(with: Data(store.optionsJSON().utf8)) as! NSDictionary
        require(native == saved["value"] as! NSDictionary, "real WKWebView stores whole unknown JSON")
        require(saved["extra"] is [Any], "unfamiliar storage keys survive bridge")
        let restarted = ShortcutSettingsStore(defaults: defaults)
        let restartedJSON = try restarted.optionsJSON()
        let originalJSON = try store.optionsJSON()
        require(restartedJSON == originalJSON, "native persistence survives fresh store")
        let context = JSContext()!
        context.evaluateScript("var document={body:{setAttribute:function(k,v){this[k]=v}}};")
        context.evaluateScript(try restarted.injectionSource())
        require(context.exception == nil, "injection safely handles nested quotes, Unicode, newline and script text")
        let injected = context.evaluateScript("JSON.parse(document.body['data-todoist-shortcuts-options'])")!.toDictionary()! as NSDictionary
        require(injected == native, "all saved options reach runtime injection")
        // Navigation policy also rejects subframes. Bypass that defense in
        // this test to exercise the independent message-frame authorization.
        web.navigationDelegate = nil
        let denied = try evaluate(web, """
          return await new Promise(resolve => {
            const frame = document.createElement('iframe');
            const listener = event => {
              if (event.source !== frame.contentWindow) return;
              window.removeEventListener('message', listener); frame.remove(); resolve(event.data);
            };
            window.addEventListener('message', listener);
            frame.srcdoc = `<script>confirm('Untrusted frame');window.webkit.messageHandlers.todoisteSettings.postMessage({operation:'set',argument:{unauthorized:true}})
              .then(()=>parent.postMessage('allowed','*')).catch(()=>parent.postMessage('blocked','*'))</script>`;
            document.body.appendChild(frame);
          });
        """) as! String
        require(denied == "blocked", "actual subframe storage request is denied")
        require(dialogs.requests.count == 4, "subframe cannot present native dialogs")
        let items = try store.perform(operation: "get", argument: NSNull()) as! [String: Any]
        require(items["unauthorized"] == nil, "denied request leaves store intact")
        web.navigationDelegate = bridge
        let readsBeforeFreshPage = bridge.readCount
        web.reload()
        wait("fresh upstream page") { bridge.readCount > readsBeforeFreshPage || bridge.loadFailure != nil }
        require(bridge.loadFailure == nil, "saved settings reopen in the upstream UI")
        _ = try evaluate(web, "await chrome.storage.sync.get(null); return 'ready';")
        let beforeControlChange = defaults.data(forKey: ShortcutSettingsStore.storageKey)
        let chosen = try evaluate(web, """
          const radio = Array.from(document.querySelectorAll('input[type=radio]'))
            .find(input => input.id && !input.disabled && !input.checked);
          if (!radio) return null;
          radio.click();
          if (radio.form) radio.form.requestSubmit();
          return radio.id;
        """)
        if let identifier = chosen as? String {
            wait("upstream control saves through native adapter") {
                defaults.data(forKey: ShortcutSettingsStore.storageKey) != beforeControlChange
            }
            let readsBeforeControlReload = bridge.readCount
            web.reload()
            wait("upstream UI restores chosen control") { bridge.readCount > readsBeforeControlReload || bridge.loadFailure != nil }
            let literal = String(decoding: try JSONSerialization.data(withJSONObject: identifier, options: [.fragmentsAllowed]), as: UTF8.self)
            let checked = try evaluate(web, "await chrome.storage.sync.get(null); return !!document.getElementById(\(literal))?.checked;") as! Bool
            require(checked, "actual upstream control selection survives a fresh page")
        } else {
            print("No standard radio control in this upstream UI; generic storage roundtrip verified")
        }
        print("WebKit settings host: upstream UI/control reload, dialogs, generic storage, injection and subframe isolation passed (\(root.lastPathComponent))")
    }
}
