import AppKit
import WebKit

@MainActor private final class NativeTransitAlertAction: NSObject {
    let title: String

    init(_ text: String) { title = localized(text) }

    private func findButton(_ view: NSView) -> NSButton? {
        if let button = view as? NSButton, button.title == title { return button }
        for child in view.subviews {
            if let button = findButton(child) { return button }
        }
        return nil
    }

    @objc func answer(_ timer: Timer) {
        guard let content = NSApp.modalWindow?.contentView, let button = findButton(content) else { return }
        timer.invalidate()
        button.performClick(nil)
    }
}

@MainActor extension NativeSmoke {
    private func answerTransitAlert(_ title: String) -> Timer {
        let timer = Timer(timeInterval: 0.02, target: NativeTransitAlertAction(title),
                          selector: #selector(NativeTransitAlertAction.answer(_:)), userInfo: nil, repeats: true)
        RunLoop.main.add(timer, forMode: .modalPanel)
        return timer
    }

    func inspectTransitWorkflow() async throws {
        let normal = try jsonString(owner.snapshot["fields"] ?? [:])
        try await owner.switchMapContext("transit")
        let transitOptions = try jsonString(owner.snapshot["transitOptions"] ?? [:])
        for page in ["lines", "stations", "base"] {
            owner.sidebarNavigation.transitPage = page; owner.renderTransitSettings()
            try requireLayout(owner.transitForm!.fields.allSatisfy { field in
                let key = field.view.identifier?.rawValue ?? ""
                return !key.contains("spacing") && !key.contains("schematic") && !key.contains("water_smoothing")
            }, "public transit never exposes metro generation settings")
        }
        owner.sidebarNavigation.transitPage = "stations"; owner.renderTransitSettings()
        _ = try setting(owner.transitForm!, "transit.station_diameter")
        owner.sidebarNavigation.transitPage = "lines"; owner.renderTransitSettings()
        let lineWidth = try setting(owner.transitForm!, "transit.line_width")
        try requireLayout(lineWidth.slider?.maxValue == 20 && (lineWidth.high ?? 0) > 20, "transit width slider is capped independently of the numeric field")
        let labelStyle = try setting(owner.transitForm!, "transit.line_label_style")
        try requireLayout(labelStyle.popup?.itemArray.count == 5 &&
            labelStyle.popup?.itemArray.allSatisfy { $0.image != nil } == true, "five transit label choices show thumbnails")
        let catalog = try await owner.engine.request("getTransitLines", ["metro": true])
        let lines = array(catalog["lines"])
        try requireLayout(!lines.isEmpty, "real input contains transit lines")
        let types = Array(Set(lines.map { textValue($0["type"]) })).sorted()
        for type in types { _ = try await owner.engine.request("setTransitType", ["metro": true, "key": type, "enabled": true, "documentEpoch": owner.epoch]) }
        guard let sampleLine = lines.first(where: { ($0["stops"] as? Int ?? 0) > 1 })?["id"] as? Int else {
            throw AppFailure(message: "Real input contains no line with multiple stops")
        }
        _ = try await owner.engine.request("setTransitLines", ["metro": true, "lineIds": [sampleLine], "documentEpoch": owner.epoch])
        try await owner.refreshSnapshot()
        try requireLayout(try jsonString(owner.snapshot["transitOptions"] ?? [:]) == transitOptions, "metro inputs preserve public transit options")
        owner.sidebarNavigation.transitPage = "lines"; owner.renderTransitSettings()
        owner.showLines(metro: false)
        for _ in 0..<100 where !owner.panels.contains(where: { $0.identifier?.rawValue == "transit.lines.selector" && $0.isVisible }) { try await Task.sleep(nanoseconds: 50_000_000) }
        guard let panel = owner.panels.first(where: { $0.identifier?.rawValue == "transit.lines.selector" && $0.isVisible }),
              let selector = (panel as? EditorWindow)?.retained.first as? TransitLineSelector else { throw AppFailure(message: "Transit selector did not open") }
        owner.showLines(metro: false)
        try requireLayout(owner.panels.filter { $0.identifier == panel.identifier && $0.isVisible }.count == 1, "one selector owns one draft")
        selector.search.stringValue = "no-matching-line-98463"; selector.rebuild()
        let payload = try jsonString(selector.selection.payload)
        selector.selectVisible(false)
        try requireLayout(try jsonString(selector.selection.payload) == payload, "empty filtered bulk preserves hidden selected lines")
        selector.cancel()
        try await owner.refreshSnapshot()
        try requireLayout(try jsonString(owner.snapshot["transitOptions"] ?? [:]) == transitOptions, "cancelled selector never writes options")
        checks.append("transit-explicit-settings-selector-single-draft-cancel-and-mode-isolation")

        let mapCollapsed = owner.rightItem.isCollapsed
        try await owner.openMetro()
        try requireLayout(owner.rmp.hasProject && owner.rmp.dirty, "independent generator produces unsaved editable project")
        try requireLayout(owner.rightItem.isCollapsed && owner.restoreInspector.isHidden, "metro uses only webpage editing panels")
        let web = owner.rmp.host.view
        let toolbar = try await web.evaluateJavaScript("document.querySelectorAll('[data-testid=editor-toolbar]').length") as? Int
        try requireLayout(toolbar == 1, "exactly one webpage toolbar")
        let initial = try await owner.rmp.request("saveProject")
        let originalGraph = try jsonString(object(initial["project"])["graph"] ?? [:])
        try requireLayout(!array(object(object(initial["project"])["graph"])["nodes"]).isEmpty, "generated graph contains stations and editable nodes")
        let file = output.appendingPathComponent("transit-workflow.rmp.json")
        let saved = try await owner.rmp.save(to: file)
        try requireLayout(saved && !owner.rmp.dirty, "generated project saves with a clean baseline")
        _ = try await owner.rmp.request("rotateMap", ["delta": 90])
        try requireLayout(owner.rmp.dirty && owner.rmp.state["canUndo"] as? Bool == true, "edit updates native dirty/history state")
        _ = try await owner.rmp.request("undo")
        let undone = try await owner.rmp.request("saveProject")
        try requireLayout(try jsonString(object(undone["project"])["graph"] ?? [:]) == originalGraph, "undo restores full graph")
        _ = try await owner.rmp.request("redo")
        _ = try await owner.rmp.save(to: file)
        let stored = try Data(contentsOf: file)
        let expected = try await owner.rmp.request("saveProject")
        try await owner.rmp.load(object(try JSONSerialization.jsonObject(with: stored)), path: file)
        let reopened = try await owner.rmp.request("saveProject")
        try requireLayout(try jsonString(expected["project"] ?? [:]) == jsonString(reopened["project"] ?? [:]), "saved edited project reopens losslessly")
        let activeID = owner.rmp.projectID
        do { try await owner.rmp.load(["version": 999], path: nil); throw AppFailure(message: "Invalid project was accepted") }
        catch { try requireLayout(owner.rmp.projectID == activeID && owner.rmp.path == file && owner.rmp.hasProject, "failed open preserves original project identity and file") }
        let afterFailure = try await owner.rmp.request("saveProject")
        try requireLayout(try jsonString(afterFailure["project"] ?? [:]) == jsonString(reopened["project"] ?? [:]), "failed open preserves original graph")
        checks.append("rmp-generated-edit-undo-redo-save-reopen-and-failed-open-preservation")

        _ = try await owner.rmp.request("setViewOptions", ["options": ["detailsVisible": true]])
        owner.toggleInspector()
        for _ in 0..<100 where owner.rmp.state["detailsVisible"] as? Bool != false { try await Task.sleep(nanoseconds: 20_000_000) }
        try requireLayout(owner.rmp.state["detailsVisible"] as? Bool == false && owner.rightItem.isCollapsed, "native details shortcut controls webpage details only")
        owner.toggleInspector()
        for _ in 0..<100 where owner.rmp.state["detailsVisible"] as? Bool != true { try await Task.sleep(nanoseconds: 20_000_000) }
        owner.window.setContentSize(NSSize(width: 1100, height: 700)); try await settleLayout()
        try await capture("fgh-metro-narrow-light")
        owner.window.appearance = NSAppearance(named: .darkAqua)
        try await owner.rmp.setAppearance(); try await capture("fgh-metro-narrow-dark")
        owner.window.appearance = NSAppearance(named: .aqua)
        try await owner.rmp.setAppearance()
        let beforeLanguage = try await owner.rmp.request("saveProject")
        owner.metadata = try await owner.engine.request("setLanguage", ["language": "en"])
        try await owner.rmp.setLanguage()
        owner.metadata = try await owner.engine.request("setLanguage", ["language": "zh-CN"])
        try await owner.rmp.setLanguage()
        let afterLanguage = try await owner.rmp.request("saveProject")
        try requireLayout(try jsonString(beforeLanguage["project"] ?? [:]) == jsonString(afterLanguage["project"] ?? [:]), "theme and language changes preserve project")
        try await owner.rmp.export(output.appendingPathComponent("transit-workflow.svg"), format: "svg", options: ["svgVersion": 2])
        try await owner.rmp.export(output.appendingPathComponent("transit-workflow.png"), format: "png", options: ["scale": 100, "transparent": false])
        checks.append("rmp-web-details-theme-language-narrow-layout-and-svg-png-export")

        _ = try await owner.rmp.save(to: file)
        let cleanID = owner.rmp.projectID
        let decline = answerTransitAlert("取消")
        defer { decline.invalidate() }
        try await owner.rmp.generate()
        try requireLayout(!decline.isValid && owner.rmp.projectID == cleanID && !owner.rmp.dirty, "clean regeneration asks before replacing a saved project")
        _ = try await owner.rmp.request("rotateMap", ["delta": 90])
        let retainedProject = try await owner.rmp.request("saveProject")
        let discard = answerTransitAlert("放弃修改并重新生成")
        defer { discard.invalidate() }
        let regeneration = Task { @MainActor in try await owner.rmp.generate() }
        for _ in 0..<100 {
            if owner.rmp.canCancelGeneration { break }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        try requireLayout(owner.rmp.canCancelGeneration, "regeneration entered cancellable phase")
        owner.cancelTask()
        var cancelled = false
        do { try await regeneration.value } catch is CancellationError { cancelled = true }
        try requireLayout(cancelled, "native cancel completes generation without an error")
        let retainedAfterCancel = try await owner.rmp.request("saveProject")
        try requireLayout(try jsonString(retainedProject["project"] ?? [:]) == jsonString(retainedAfterCancel["project"] ?? [:])
            && owner.rmp.path == file && owner.rmp.dirty && owner.rmp.state["canUndo"] as? Bool == true,
            "cancelled regeneration preserves the graph, path, dirty flag and history")
        try await settleSettingsWrites()
        let apply = answerTransitAlert("放弃修改并重新生成")
        defer { apply.invalidate() }
        try await owner.rmp.generate()
        try requireLayout(owner.rmp.hasProject && owner.rmp.dirty && owner.metroAppliedFingerprint == owner.metroInputFingerprint(), "successful regeneration applies current generation inputs")
        let regeneratedID = owner.rmp.projectID
        try await owner.switchMapContext("map")
        try requireLayout(owner.rightItem.isCollapsed == mapCollapsed, "leaving metro restores original native inspector state")
        try await owner.openMetro()
        try requireLayout(owner.rmp.projectID == regeneratedID && owner.rmp.dirty && owner.window.isDocumentEdited,
            "returning to metro reopens the same unsaved project")
        _ = try await owner.rmp.save(to: file)
        try await inspectUnsavedTransitProject(file)
        try await owner.switchMapContext("map")
        try requireLayout(try jsonString(owner.snapshot["fields"] ?? [:]) == normal, "metro generation leaves ordinary map settings unchanged")
        checks.append("rmp-cancel-preserves-project-regenerate-and-native-inspector-restoration")
    }

    private func inspectUnsavedTransitProject(_ file: URL) async throws {
        _ = try await owner.rmp.request("rotateMap", ["delta": 90])
        let epoch = owner.epoch, projectID = owner.rmp.projectID
        let cancelCity = answerTransitAlert("取消")
        defer { cancelCity.invalidate() }
        try await owner.openCity(URL(fileURLWithPath: source))
        try requireLayout(!cancelCity.isValid && owner.epoch == epoch && owner.pendingCityEditors == nil
            && owner.rmp.projectID == projectID && owner.rmp.dirty, "canceling a city change keeps the edited project")

        let cancelQuit = answerTransitAlert("取消")
        defer { cancelQuit.invalidate() }
        NSApp.terminate(nil)
        for _ in 0..<200 where owner.terminationPending || cancelQuit.isValid {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        try requireLayout(!cancelQuit.isValid && !owner.terminationPending && !owner.terminating
            && owner.rmp.projectID == projectID && owner.rmp.dirty, "canceling quit keeps the edited project")

        let fm = FileManager.default
        let original = file.deletingLastPathComponent().appendingPathComponent(UUID().uuidString)
        try fm.moveItem(at: file, to: original)
        defer {
            try? fm.removeItem(at: file)
            try? fm.moveItem(at: original, to: file)
        }
        try fm.createDirectory(at: file, withIntermediateDirectories: false)
        let saveOnQuit = answerTransitAlert("保存")
        defer { saveOnQuit.invalidate() }
        owner.smoke = nil
        defer { owner.smoke = self }
        NSApp.terminate(nil)
        for _ in 0..<200 where owner.terminationPending || saveOnQuit.isValid {
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        try requireLayout(!saveOnQuit.isValid && !owner.terminationPending && !owner.terminating
            && owner.rmp.projectID == projectID && owner.rmp.path == file && owner.rmp.dirty
            && owner.window.attachedSheet != nil, "a save failure leaves the app open and keeps the project")
        if let sheet = owner.window.attachedSheet { owner.window.endSheet(sheet, returnCode: .alertFirstButtonReturn) }
        _ = try await owner.rmp.request("saveProject")
        checks.append("rmp-unsaved-city-and-quit-cancel-save-failure-recovery")
    }
}
