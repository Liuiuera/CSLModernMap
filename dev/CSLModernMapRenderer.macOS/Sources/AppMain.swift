import AppKit
@main struct NativeApplication {
    @MainActor static func main() {
        signal(SIGPIPE, SIG_IGN)
        let application = NSApplication.shared
        let controller = InterfaceController()
        application.delegate = controller
        withExtendedLifetime(controller) { application.run() }
    }
}
