import Foundation

/// One unmodified upstream src tree serves both runtime and settings assets.
struct ShortcutResources {
    let directory: URL
    let optionsURL: URL
    let adapterSource: String

    enum ResourceError: LocalizedError {
        case unavailable, unsafePath
        var errorDescription: String? { "Extension settings are unavailable in this build." }
    }

    init(directory: URL, adapter: URL) throws {
        let root = directory.standardizedFileURL.resolvingSymlinksInPath()
        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("manifest.json"))) as? [String: Any]
        let page = (manifest?["options_ui"] as? [String: Any])?["page"] as? String ?? manifest?["options_page"] as? String
        guard let page, !page.isEmpty, !page.hasPrefix("/"), !page.contains("\\"),
              URLComponents(string: page)?.scheme == nil,
              !page.split(separator: "/").contains("..") else { throw ResourceError.unsafePath }
        let url = root.appendingPathComponent(page).standardizedFileURL.resolvingSymlinksInPath()
        guard url.path.hasPrefix(root.path + "/"), FileManager.default.fileExists(atPath: url.path) else {
            throw ResourceError.unavailable
        }
        self.directory = root
        self.optionsURL = url
        let literal = try JSONSerialization.data(withJSONObject: root.absoluteString, options: [.fragmentsAllowed])
        self.adapterSource = try String(contentsOf: adapter, encoding: .utf8)
            .replacingOccurrences(of: "__TODOISTE_RESOURCE_ROOT__", with: String(decoding: literal, as: UTF8.self))
    }

    static func bundled() throws -> ShortcutResources {
        guard let resources = Bundle.main.resourceURL,
              let adapter = Bundle.main.url(forResource: "settings-storage-adapter", withExtension: "js") else {
            throw ResourceError.unavailable
        }
        return try ShortcutResources(directory: resources.appendingPathComponent("Shortcuts", isDirectory: true), adapter: adapter)
    }
}
