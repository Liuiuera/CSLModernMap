import AppKit
import WebKit

@MainActor private final class RmpFilePanelProbe: NSObject {
    var result: (isOpen: Bool, name: String)?

    @objc func cancelPanel(_ timer: Timer) {
        guard let panel = NSApp.modalWindow as? NSSavePanel else { return }
        let isOpen = panel is NSOpenPanel
        result = (isOpen, isOpen ? "" : panel.nameFieldStringValue)
        timer.invalidate()
        panel.cancel(nil)
    }
}

@MainActor extension NativeSmoke {
    private func waitForRmp(_ message: String, _ condition: () async throws -> Bool) async throws {
        for _ in 0..<100 {
            if try await condition() { return }
            try await Task.sleep(nanoseconds: 40_000_000)
        }
        throw AppFailure(message: message)
    }

    private func postRmpKey(_ key: String, code: UInt16, modifiers: NSEvent.ModifierFlags = []) {
        let character = modifiers.contains(.shift) ? key.uppercased() : key
        if let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: modifiers,
                                       timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: owner.window.windowNumber,
                                       context: nil, characters: character, charactersIgnoringModifiers: character,
                                       isARepeat: false, keyCode: code) {
            NSApp.postEvent(event, atStart: false)
        }
    }

    private func rmpGraph() async throws -> String {
        let save = try await owner.rmp.request("saveProject")
        return try jsonString(object(save["project"])["graph"] ?? [:])
    }

    private func observeRmpFilePanel(key: String, code: UInt16, modifiers: NSEvent.ModifierFlags) async throws -> (isOpen: Bool, name: String) {
        let probe = RmpFilePanelProbe()
        let observer = Timer(timeInterval: 0.05, target: probe, selector: #selector(RmpFilePanelProbe.cancelPanel(_:)),
                             userInfo: nil, repeats: true)
        RunLoop.main.add(observer, forMode: .modalPanel)
        defer { observer.invalidate() }
        postRmpKey(key, code: code, modifiers: modifiers)
        try await waitForRmp("Shortcut must open the native file panel") { probe.result != nil }
        guard let result = probe.result else { throw AppFailure(message: "Missing native file panel result") }
        return result
    }

    func inspectRmpNativeCommands() async throws {
        let projectURL = URL(fileURLWithPath: source)
        let project = object(try JSONSerialization.jsonObject(with: Data(contentsOf: projectURL)))
        try await owner.openMetro()
        try await owner.rmp.load(project, path: projectURL, provenance: ["cityName": "蒙德莱因"])
        owner.window.makeKeyAndOrderFront(nil)
        let rmp = owner.rmp
        let web = rmp.host.view
        try await settleLayout()
        let band = (owner.band.subviews.first as? NSScrollView)?.documentView as? NSStackView
        let bandActions = band?.arrangedSubviews.compactMap { $0.identifier?.rawValue }
        try requireLayout(bandActions == ["返回地图", "保存项目", "导出…"], "metro band contains only project actions")
        try requireLayout(owner.metroToolbarTitle.window === owner.window && owner.tabs.window == nil, "metro replaces inactive map tabs with its title")
        let cityMenu = owner.openCityMenuItem, projectMenu = owner.openProjectMenuItem
        try requireLayout(cityMenu?.keyEquivalent.isEmpty == true && projectMenu?.keyEquivalent == "o",
                          "project and city menus have independent commands in metro")
        guard let menu = owner.rmpMenuItem?.submenu else { throw AppFailure(message: "Missing native RMP menu") }
        let tools = RmpNativeCommand.elements + RmpNativeCommand.view
        try requireLayout(tools.allSatisfy { tool in menu.items.contains { $0.identifier?.rawValue == tool.rawValue } },
                          "native RMP menu exposes every insertion and view command")
        let grid = rmp.state["gridLines"] as? Bool ?? false
        owner.runRmpTool(.grid)
        try await waitForRmp("Native grid latches to page state") { rmp.state["gridLines"] as? Bool == !grid }
        let gridItem = menu.items.first { $0.identifier?.rawValue == RmpNativeCommand.grid.rawValue }!
        try requireLayout(owner.validateMenuItem(gridItem) && (gridItem.state == .on) == !grid, "native view check mark follows RMP")
        owner.runRmpTool(.names)
        try await waitForRmp("Native names toggle reaches the page") { rmp.state["showNames"] as? Bool == false }
        try requireLayout(!owner.rmpToolEnabled(.defaultNames), "default names depend on overall name visibility")
        checks.append("rmp-native-menus-view-state-and-file-shortcuts")

        let toolbar = "document.querySelector('[data-testid=editor-toolbar]')"
        let selects = "\(toolbar).querySelectorAll('select').length"
        try await waitForRmp("Only the station picker is visible before drawing") { try await web.evaluateJavaScript(selects) as? Int == 1 }
        _ = try await web.evaluateJavaScript("\(toolbar).querySelector('[data-testid=draw-line]').click(); null")
        try await waitForRmp("Drawing expands path and style settings") {
            guard textValue(rmp.state["mode"]).hasPrefix("line-") else { return false }
            return try await web.evaluateJavaScript(selects) as? Int == 3
        }
        owner.runRmpTool(.cancel)
        try await waitForRmp("Stopping drawing collapses line settings") { try await web.evaluateJavaScript(selects) as? Int == 1 }
        _ = try await web.evaluateJavaScript("\(toolbar).querySelector('[data-testid=insert-elements]').click(); null")
        try await waitForRmp("The unified menu contains all six insertion commands") {
            try await web.evaluateJavaScript("document.querySelector('[data-testid=insert-facility]')?.closest('[role=menu]')?.querySelectorAll('[role=menuitem]').length") as? Int == 6
        }
        _ = try await web.evaluateJavaScript("document.querySelector('[data-testid=insert-facility]').click(); null")
        try await waitForRmp("Web toolbar opens the native facility picker") { owner.rmpFacilitiesPopover?.isShown == true }
        guard let picker = owner.rmpFacilitiesPopover?.contentViewController as? RmpFacilityPicker else {
            throw AppFailure(message: "Missing native facility picker")
        }
        let originalCount = picker.results.stack.arrangedSubviews.count
        picker.search.stringValue = "airport"
        picker.controlTextDidChange(Notification(name: NSControl.textDidChangeNotification, object: picker.search))
        let matches = picker.results.stack.arrangedSubviews.compactMap { $0 as? ActionButton }
        try requireLayout(!matches.isEmpty && matches.count < originalCount && matches.allSatisfy { $0.image != nil },
                          "facility search narrows native results with thumbnails")
        matches[0].performClick(nil)
        try await waitForRmp("Facility must be armed in RMP") { object(rmp.state["insertion"])["kind"] as? String == "facility" }
        owner.runRmpTool(.cancel)
        try await waitForRmp("Native Cancel Placement must clear a facility") { !rmp.placementPending }
        checks.append("rmp-macos-compact-toolbar-and-native-facility-picker")

        let beforeRotation = try await rmpGraph()
        owner.runRmpTool(.rotateRight)
        try await waitForRmp("Rotation must change the graph") { try await rmpGraph() != beforeRotation }
        let rotated = try await rmpGraph()
        owner.window.makeFirstResponder(web); try await rmp.focusEditor()
        try requireLayout(rmp.webHasFocus, "WebKit has the native first responder for the canvas")
        postRmpKey("z", code: 6, modifiers: [.command])
        try await waitForRmp("Canvas Cmd+Z must undo the rotation") { try await rmpGraph() == beforeRotation }
        postRmpKey("z", code: 6, modifiers: [.command, .shift])
        try await waitForRmp("Canvas Shift+Cmd+Z must redo the rotation") { try await rmpGraph() == rotated }

        owner.runRmpTool(.title)
        try await waitForRmp("Native title must enter placement") {
            textValue(object(rmp.state["insertion"])["title"]).contains("蒙德莱因")
        }
        postRmpKey("\u{1b}", code: 53)
        try await waitForRmp("Canvas Esc must cancel an unplaced title") { !rmp.placementPending }
        let afterCanvasEscape = try await rmpGraph()
        try requireLayout(afterCanvasEscape == rotated, "canvas Esc does not change the graph")

        owner.runRmpTool(.title)
        try await waitForRmp("Native title must re-enter placement") { rmp.placementPending }
        _ = try await web.evaluateJavaScript("""
            const canvas = document.getElementById('canvas');
            const bounds = canvas.getBoundingClientRect();
            canvas.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, button: 0,
                clientX: bounds.x + bounds.width / 2, clientY: bounds.y + bounds.height / 2}));
            null
            """)
        try await waitForRmp("The placed title must expose its real textarea in the property panel") {
            try await web.evaluateJavaScript("Boolean(document.querySelector('[data-testid=side-panel-root] textarea'))") as? Bool == true
        }
        owner.window.makeFirstResponder(web)
        _ = try await web.evaluateJavaScript("document.querySelector('[data-testid=side-panel-root] textarea').focus(); null")
        try requireLayout(rmp.webHasFocus, "WebKit has the native first responder for the textarea")
        let titleGraph = try await rmpGraph()
        let title = try await web.evaluateJavaScript("document.activeElement.value") as? String ?? ""
        try requireLayout(title.contains("蒙德莱因"), "placed native title carries the city name")
        postRmpKey("z", code: 6, modifiers: [.command])
        try await settleLayout()
        let afterEmptyUndo = try await rmpGraph()
        try requireLayout(afterEmptyUndo == titleGraph, "empty textarea history does not undo the graph")
        _ = try await web.evaluateJavaScript("document.activeElement.setSelectionRange(document.activeElement.value.length, document.activeElement.value.length); null")
        postRmpKey("x", code: 7)
        let value = "document.activeElement?.value"
        try await waitForRmp("A physical key must type into the WebKit textarea") {
            try await web.evaluateJavaScript(value) as? String == title + "x"
        }
        postRmpKey("z", code: 6, modifiers: [.command])
        try await waitForRmp("Textarea Cmd+Z must undo text without removing the title") {
            try await web.evaluateJavaScript(value) as? String == title
        }
        postRmpKey("z", code: 6, modifiers: [.command, .shift])
        try await waitForRmp("Textarea Shift+Cmd+Z must redo text") {
            try await web.evaluateJavaScript(value) as? String == title + "x"
        }
        let focusedTag = try await web.evaluateJavaScript("document.activeElement.tagName") as? String
        try requireLayout(focusedTag == "TEXTAREA", "text history keeps the website input focused")
        checks.append("rmp-real-macos-canvas-and-web-textarea-undo-redo")
        try await capture("rmp-native-controls-and-textarea")

        let nativeText = NSTextView(frame: NSRect(x: 10, y: 10, width: 100, height: 30))
        nativeText.allowsUndo = true
        owner.root.addSubview(nativeText)
        defer { nativeText.removeFromSuperview() }
        owner.window.makeFirstResponder(nativeText)
        postRmpKey("x", code: 7)
        try await waitForRmp("A physical key must type into an AppKit text editor") { nativeText.string == "x" }
        postRmpKey("z", code: 6, modifiers: [.command])
        try await waitForRmp("AppKit Cmd+Z must only undo the native text") { nativeText.string.isEmpty }
        postRmpKey("z", code: 6, modifiers: [.command, .shift])
        try await waitForRmp("AppKit Shift+Cmd+Z must redo the native text") { nativeText.string == "x" }
        for tool in [RmpNativeCommand.legend, .text, .helper, .fill] {
            owner.runRmpTool(tool)
            try await waitForRmp("Drawing tool must be armed in RMP") { rmp.placementPending }
            let beforeEscape = try await rmpGraph()
            owner.window.makeFirstResponder(nativeText)
            postRmpKey("\u{1b}", code: 53)
            try await waitForRmp("Native Esc must cancel \(tool.rawValue)") { !rmp.placementPending }
            let afterEscape = try await rmpGraph()
            try requireLayout(afterEscape == beforeEscape && owner.window.firstResponder === nativeText,
                              "native Esc leaves the original graph and native input focus")
        }
        checks.append("rmp-native-text-undo-and-escape-from-canvas-and-native-controls")

        let scratch = output.appendingPathComponent("commands.rmp.json")
        _ = try await rmp.save(to: scratch)
        owner.window.makeFirstResponder(web)
        let openPanel = try await observeRmpFilePanel(key: "o", code: 31, modifiers: [.command])
        try requireLayout(openPanel.isOpen && rmp.path == scratch && !rmp.dirty, "Cmd+O opens the RMP file chooser and cancel keeps the project")
        owner.runRmpTool(.rotateLeft)
        try await waitForRmp("Rotation marks the project dirty") { rmp.dirty }
        let savePanel = try await observeRmpFilePanel(key: "s", code: 1, modifiers: [.command, .shift])
        try requireLayout(!savePanel.isOpen && savePanel.name.hasSuffix(".rmp.json") && rmp.path == scratch && rmp.dirty,
                          "Shift+Cmd+S opens a named save panel and cancel keeps the current path and changes")
        postRmpKey("s", code: 1, modifiers: [.command])
        try await waitForRmp("Cmd+S saves to the existing project path") { !rmp.dirty }
        let persisted = object(try JSONSerialization.jsonObject(with: Data(contentsOf: scratch)))
        let savedGraph = try await rmpGraph()
        try requireLayout(try jsonString(persisted["graph"] ?? [:]) == savedGraph, "Cmd+S persisted the current graph")
        checks.append("rmp-native-file-panels-and-save-shortcuts")
        try await owner.switchMapContext("map")
        try requireLayout(owner.rmpMenuItem?.isHidden == true && cityMenu?.keyEquivalent == "o",
                          "leaving metro restores the ordinary city shortcut and hides the metro menu")
        try requireLayout(owner.tabs.window === owner.window && owner.metroToolbarTitle.window == nil, "leaving metro restores map tabs")
    }
}
