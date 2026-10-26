import AppKit

@main
struct SettingsWindowCloseTests {
    @MainActor static func require(_ value: @autoclosure () -> Bool, _ message: String) {
        if !value() { fputs("FAILED: \(message)\n", stderr); exit(1) }
    }

    @MainActor static func main() {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        var closes = 0
        let view = SettingsWindowCloseObserverView { closes += 1 }
        let first = NSWindow(contentRect: .zero, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        let second = NSWindow(contentRect: .zero, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        first.isReleasedWhenClosed = false
        second.isReleasedWhenClosed = false
        first.contentView = view
        first.performClose(nil)
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        require(closes == 1, "closing the settings window delivers its deferred callback once")
        first.contentView = nil
        second.contentView = view
        first.close()
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        require(closes == 1, "moving the observer detaches the old window")
        second.performClose(nil)
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        require(closes == 2, "a recreated settings window is observed")
        second.contentView = nil
        print("Native settings window: deferred close callback and window reattachment passed")
    }
}
