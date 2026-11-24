import AppKit

// Runs only through the explicit smoke entry point, against a separate test data directory.
@MainActor extension NativeSmoke {
    func requireLayout(_ condition: Bool, _ message: String) throws {
        if CommandLine.arguments.contains("--settings-probe") { FileHandle.standardError.write(Data(("settings assertion: \(message) = \(condition)\n").utf8)) }
        if !condition { throw AppFailure(message: "Layout: " + message) }
    }
    func settleLayout() async throws {
        owner.root.layoutSubtreeIfNeeded()
        try await Task.sleep(nanoseconds: 250_000_000)
        owner.root.layoutSubtreeIfNeeded()
    }
    func checkGeometry() throws {
        let root = owner.root
        let left = root.convert(owner.left.bounds, from: owner.left)
        let right = root.convert(owner.right.bounds, from: owner.right)
        let band = root.convert(owner.band.bounds, from: owner.band)
        let tabs = owner.tabs.convert(owner.tabs.bounds, to: nil)
        let rootFrame = root.convert(root.bounds, to: nil)
        if owner.leftItem.isCollapsed {
            try requireLayout(abs(band.minX - root.bounds.minX) < 3, "band expands to the window edge when the sidebar is hidden")
        } else {
            try requireLayout(abs(left.maxY - root.bounds.maxY) < 2, "left sidebar must reach the window top")
            try requireLayout(abs(left.minX - root.bounds.minX) < 2, "left sidebar must reach the window edge")
            try requireLayout(abs(band.minX - left.maxX) < 3, "band starts after the left sidebar")
        }
        try requireLayout(abs(tabs.midX - rootFrame.midX) < 3, "tabs must be centered in the whole window (delta \(tabs.midX-rootFrame.midX))")
        try requireLayout(!owner.tabs.isHiddenOrHasHiddenAncestor && tabs.width >= owner.tabs.fittingSize.width - 1, "all three tabs remain visible without compression")
        let collapse = owner.collapse.convert(owner.collapse.bounds, to: nil)
        try requireLayout(!tabs.intersects(collapse), "tabs must not overlap the band toggle")
        try requireLayout(tabs.minX >= rootFrame.minX && tabs.maxX <= rootFrame.maxX, "tabs stay inside the window")
        if !owner.rightItem.isCollapsed {
            try requireLayout(abs(right.maxY - band.minY) < 2, "inspector starts below the band")
        }
        try requireLayout(owner.leftItem.canCollapse && owner.rightItem.canCollapse, "both sidebars support hiding")
        try requireLayout(owner.restoreSidebar.isHidden == !owner.leftItem.isCollapsed, "left restore follows sidebar visibility")
        try requireLayout(owner.restoreInspector.isHidden == (owner.context == "metro" || !owner.rightItem.isCollapsed), "right restore follows mode and inspector visibility")
        for button in [owner.restoreSidebar, owner.restoreInspector] where !button.isHidden {
            try requireLayout(owner.center.bounds.contains(button.frame), "restore button stays inside the canvas")
            let canvas = owner.context == "metro" ? owner.rmp.host.view : owner.viewer.view
            try requireLayout(owner.center.subviews.firstIndex(of: button)! > owner.center.subviews.firstIndex(of: canvas)!, "restore button stays above the active web canvas")
        }
        try requireLayout(owner.viewer.view.bounds.width >= 499, "usable canvas width")
    }
    func checkSidebarCombinations() async throws {
        let selectedLayer = owner.selectedLayer
        let sidebarForm = owner.sidebarForm, transitForm = owner.transitForm, inspectorForm = owner.inspectorForm
        func settingsSnapshot() throws -> String {
            try jsonString(["fields": object(owner.snapshot["fields"]), "layers": object(owner.snapshot["layers"]), "transitOptions": object(owner.snapshot["transitOptions"]), "metroOptions": object(owner.snapshot["metroOptions"])])
        }
        let settings = try settingsSnapshot()
        let collapsedSections = owner.sectionState.collapsed
        func hideButton(_ header: NSStackView, action: Selector) throws -> NSButton {
            guard let button = header.arrangedSubviews.compactMap({ $0 as? NSButton }).first(where: { $0.action == action }) else {
                throw AppFailure(message: "Sidebar header is missing its hide button")
            }
            return button
        }
        let hideLeft = try hideButton(owner.leftHeader, action: #selector(InterfaceController.toggleSidebar))
        let hideRight = try hideButton(owner.rightHeader, action: #selector(InterfaceController.toggleInspector))
        var measurements: [JSON] = []
        for width in [1340, 1100] {
            owner.window.setContentSize(NSSize(width: width, height: width == 1100 ? 700 : 790))
            try await settleLayout()
            for (leftHidden, rightHidden) in [(false, false), (true, false), (true, true), (false, true), (false, false)] {
                if owner.leftItem.isCollapsed != leftHidden { (leftHidden ? hideLeft : owner.restoreSidebar).performClick(nil) }
                if owner.rightItem.isCollapsed != rightHidden { (rightHidden ? hideRight : owner.restoreInspector).performClick(nil) }
                try await settleLayout(); try checkGeometry()
                try requireLayout(owner.leftItem.isCollapsed == leftHidden && owner.rightItem.isCollapsed == rightHidden, "each button changes only its own sidebar")
                try requireLayout(owner.selectedLayer == selectedLayer && owner.sidebarForm === sidebarForm && owner.transitForm === transitForm && owner.inspectorForm === inspectorForm, "sidebar toggles retain the selected page and forms")
                try requireLayout(owner.sectionState.collapsed == collapsedSections, "sidebar toggles retain section state")
                let currentSettings = try settingsSnapshot()
                try requireLayout(currentSettings == settings, "sidebar toggles retain settings")
                let tabs = owner.tabs.convert(owner.tabs.bounds, to: nil)
                let root = owner.root.convert(owner.root.bounds, to: nil)
                measurements.append(["windowWidth": root.width, "leftHidden": leftHidden, "rightHidden": rightHidden, "tabCenterDelta": tabs.midX - root.midX, "canvasWidth": owner.center.bounds.width])
                if leftHidden && rightHidden { try await capture("sidebars-\(owner.context)-\(width)-both-hidden") }
            }
            owner.split.splitView.setPosition(width == 1100 ? 280 : 240, ofDividerAt: 0)
            try await settleLayout()
            owner.contentSplit.splitView.setPosition(owner.contentSplit.view.bounds.width - (width == 1100 ? 240 : 300), ofDividerAt: 0)
            try await settleLayout(); try checkGeometry()
            try await capture("sidebars-\(owner.context)-\(width)-restored")
        }
        try JSONSerialization.data(withJSONObject: measurements, options: [.prettyPrinted, .sortedKeys]).write(to: output.appendingPathComponent("sidebar-geometry-\(owner.context).json"))
        checks.append("\(owner.context)-both-sidebars-hide-restore-state-and-window-centering")
    }
    func inspectLayout(mapOnly: Bool = false) async throws {
        var choice: Any?
        let field = FieldControl("Mode", schema:["kind":"choice", "choices":[["value":"first", "label":"First"], ["value":"second", "label":"Second"]]], value:"first") { choice = $0 }
        try requireLayout(field.popup?.numberOfItems == 2, "popup options must be populated")
        field.popup?.selectItem(at:1); field.changed()
        try requireLayout(choice as? String == "second", "popup sends its selected value, not a boolean")
        let disabled = FieldControl("Disabled",schema:["kind":"toggle", "enabled":false],value:false) { _ in }
        try requireLayout(!(disabled.view as! NSControl).isEnabled, "schema disabled state")
        let empty = FieldControl("Empty",schema:["kind":"choice", "choices":[]],value:"") { _ in choice = "unexpected" }
        try requireLayout(!(empty.view as! NSControl).isEnabled, "empty popup is unavailable")
        empty.changed()
        try requireLayout(choice as? String == "second", "empty popup never sends a boolean")
        checks.append("native-choice-field-values-and-disabled-state")
        owner.buildSidebar(); owner.buildInspector()
        try await settleLayout(); try await capture("01-map-light"); try checkGeometry()
        let layersBefore = try jsonString(object(owner.snapshot["layers"]))
        let group = owner.layerNodes[1]
        owner.table.collapseItem(group)
        try requireLayout(owner.sectionState.collapsed.contains("layers.city"), "outline collapse state")
        try await owner.refreshSnapshot()
        try requireLayout(!owner.table.isItemExpanded(group), "refresh preserves outline collapse")
        let layersAfter = try jsonString(object(owner.snapshot["layers"]))
        try requireLayout(layersAfter == layersBefore, "collapse must not alter visibility")
        owner.table.expandItem(group)
        try await settleLayout()
        checks.append("outline-collapse-preserves-visibility-and-refresh-state")

        let road = owner.layerNodes[1].children[0]
        let row = owner.table.row(forItem: road)
        let cell = owner.table.view(atColumn: 0, row: row, makeIfNecessary: true)!
        cell.layoutSubtreeIfNeeded()
        func checkboxes(_ view: NSView) -> [NSButton] {
            (view as? NSButton).map { [$0] } ?? view.subviews.flatMap { checkboxes($0) }
        }
        let visibility = checkboxes(cell).first { $0.identifier?.rawValue == "layer:道路" }!
        let details = checkboxes(cell).first { $0.identifier?.rawValue == "layer.open.road" }!
        try requireLayout(details.image != nil, "layer details show a disclosure arrow")
        let arrowRight = owner.table.convert(details.bounds, from: details).maxX
        let contentRight = owner.table.enclosingScrollView?.contentView.bounds.width ?? 0
        let cellRight = owner.table.convert(cell.bounds, from: cell).maxX
        try requireLayout(abs(cellRight - arrowRight - 3) < 1 && abs(contentRight - cellRight) < 24,
                          "layer arrow aligns with the trailing edge (arrow \(arrowRight), cell \(cellRight), viewport \(contentRight))")
        let oldValue = owner.defaultVisibility("道路")
        visibility.performClick(nil)
        for _ in 0..<400 {
            if !owner.fieldWriting && !owner.busy { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        try requireLayout(owner.selectedLayer == nil, "checkbox must not enter details")
        try requireLayout(owner.defaultVisibility("道路") != oldValue, "inline checkbox updates engine visibility")
        details.performClick(nil)
        try requireLayout(owner.selectedLayer == "道路", "disclosure opens layer details")
        try requireLayout((owner.sidebarForm?.fields.first?.view as? NSButton)?.state == (oldValue ? .off : .on), "detail visibility stays synchronized")
        _ = try await owner.engine.request("setLayerVisibility", ["key":"road", "enabled":oldValue])
        try await owner.refreshSnapshot(); owner.showLayer("道路")
        let section = try settingsSection(owner.sidebarForm!, "layer.road.overall")
        section.toggleTitle()
        owner.showLayer("道路")
        try requireLayout(try settingsSection(owner.sidebarForm!, "layer.road.overall").body.isHidden, "detail rebuild preserves folded section")
        checks.append("inline-visibility-and-detail-section-state")
        owner.metadata = try await owner.engine.request("setLanguage", ["language":"en"])
        owner.applyLanguage()
        try await settleLayout(); try checkGeometry()
        try requireLayout(owner.selectedLayer == "道路", "language preserves detail page")
        try requireLayout(try settingsSection(owner.sidebarForm!, "layer.road.overall").body.isHidden, "language preserves folded section")
        try await capture("02-detail-english")
        try await checkSidebarCombinations()
        try settingsSection(owner.sidebarForm!, "layer.road.overall").toggleTitle()
        owner.metadata = try await owner.engine.request("setLanguage", ["language":"zh-CN"])
        owner.applyLanguage(); owner.buildSidebar()

        owner.window.setContentSize(NSSize(width:1100,height:700))
        try await settleLayout()
        owner.split.splitView.setPosition(280,ofDividerAt:0)
        try await settleLayout(); try checkGeometry()
        try requireLayout(abs(owner.left.bounds.width - 280) < 2, "left divider must actually resize the sidebar")
        owner.toggleInspector()
        try await settleLayout(); try checkGeometry()
        try requireLayout(!owner.restoreInspector.isHidden, "restore button remains reachable")
        try await capture("03-narrow-inspector-hidden")
        owner.restoreInspector.performClick(nil)
        try await settleLayout(); try checkGeometry()
        owner.toggleBand(); try await settleLayout(); try checkGeometry()
        try await capture("04-band-collapsed")
        owner.toggleBand()
        for page in 0..<3 {
            owner.tabs.selectedSegment = page; owner.changePage(owner.tabs)
            try await settleLayout(); try checkGeometry()
            guard let scroll = owner.band.subviews.first as? NSScrollView, let document = scroll.documentView else { throw AppFailure(message:"Band is not scrollable") }
            try requireLayout(document.bounds.width > 0, "band has commands")
            scroll.contentView.scroll(to: NSPoint(x:max(0, document.bounds.width-scroll.contentView.bounds.width),y:0))
            scroll.reflectScrolledClipView(scroll.contentView)
            try await capture("05-narrow-tab-\(page)")
        }
        checks.append("window-centering-split-resize-inspector-and-band-toggles")
        owner.tabs.selectedSegment = 0; owner.changePage(owner.tabs)
        owner.window.appearance = NSAppearance(named:.darkAqua)
        try await settleLayout(); try await capture("06-map-dark")
        owner.window.appearance = NSAppearance(named:.aqua)
        if mapOnly { return }
        owner.window.setContentSize(NSSize(width:1340,height:790))
        owner.split.splitView.setPosition(240,ofDividerAt:0)
        owner.context = "transit"; try await owner.applySceneState(); owner.rebuildBand()
        try await owner.loadTransitSettings()
        try await settleLayout(); try checkGeometry(); try await capture("07-transit")
        let transit = owner.transitForm!
        try requireLayout(transit.sections.count > 1 && !transit.fields.isEmpty, "transit grouped fields")
        try requireLayout(transit.fields.compactMap(\.popup).allSatisfy { $0.numberOfItems > 0 }, "transit popup choices remain reachable")
        transit.sections[0].toggleTitle()
        try await owner.loadTransitSettings()
        try requireLayout(owner.transitForm!.sections[0].body.isHidden, "transit refresh preserves section")
        try await checkSidebarCombinations()
        checks.append("transit-grouped-settings")
        try await owner.openMetro(); try await owner.loadTransitSettings()
        try await settleLayout(); try checkGeometry(); try await capture("08-metro")
        try requireLayout(owner.rightItem.isCollapsed && owner.restoreInspector.isHidden, "metro uses webpage tools without a native right panel")
        _ = try await owner.rmp.request("setViewOptions", ["options": ["detailsVisible": true]])
        owner.toggleInspector(); try await settleLayout()
        try requireLayout(owner.rmp.state["detailsVisible"] as? Bool == false, "details shortcut hides webpage details")
        try await capture("09-metro-web-details-hidden")
        owner.toggleInspector(); try await settleLayout()
        try requireLayout(owner.rmp.state["detailsVisible"] as? Bool == true && owner.rightItem.isCollapsed, "details shortcut restores webpage panel")
        owner.toggleSidebar(); try await settleLayout(); try checkGeometry()
        owner.toggleSidebar(); try await settleLayout(); try checkGeometry()
        checks.append("metro-web-tools-details-and-left-sidebar-restore")
    }
}
