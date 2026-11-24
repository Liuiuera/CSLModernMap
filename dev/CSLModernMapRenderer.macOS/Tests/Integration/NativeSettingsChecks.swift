import AppKit

@MainActor extension NativeSmoke {
    // These checks cover AppKit settings with the real engine, independently of
    // the MapLibre/WebGL canvas. Compiled scene bundles still receive normal acks.
    func settingsSceneReady(_ payload: JSON) {
        owner.run { [self] in
            guard let url = URL(string: textValue(payload["manifestUrl"])) else { throw AppFailure(message: "Missing settings probe scene manifest") }
            _ = try await owner.engine.request("previewPresented", ["bundleId": url.deletingLastPathComponent().lastPathComponent,
                "revision": payload["revision"] ?? 0, "completion": payload["completion"] ?? "complete", "performance": [:]])
            try await owner.refreshSnapshot()
            guard payload["completion"] as? String == "complete", !testing else { return }
            testing = true
            checks.append("settings-real-engine-open-without-webgl")
            if CommandLine.arguments.contains("--fgh-probe") { try await inspectTransitWorkflow() }
            else if CommandLine.arguments.contains("--terrain-settings-probe") { try await inspectTerrainSettings() }
            else if CommandLine.arguments.contains("--text-icons-settings-probe") { try await inspectTextIconSettings() }
            else if CommandLine.arguments.contains("--labels-settings-probe") { try await inspectFriendlyNames() }
            else if CommandLine.arguments.contains("--regions-settings-probe") { try await inspectRegions() }
            else { try await inspectSettings() }
            finish(true, nil)
        }
    }
    func setting(_ form: FormView, _ key: String) throws -> FieldControl {
        for field in form.fields {
            if field.view.identifier?.rawValue == key { return field }
        }
        throw AppFailure(message: "Missing setting: " + key)
    }
    func settingsSection(_ form: FormView, _ key: String) throws -> FormSection {
        for section in form.sections { if section.key == key { return section } }
        throw AppFailure(message: "Missing section: " + key)
    }
    func settleSettingsWrites() async throws {
        for _ in 0..<1200 {
            if !owner.fieldWriting && owner.fieldQueue.isEmpty && !owner.busy { try await settleLayout(); return }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        throw AppFailure(message: "Settings write did not finish")
    }
    func checkTypeColumns(_ view: NSView, horizontal: Bool) throws {
        if let grid = view as? TransitTypeChoices {
            for case let row as NSStackView in grid.arrangedSubviews {
                try requireLayout(row.orientation == (horizontal ? .horizontal : .vertical), "transport types reflow with sidebar width")
                if horizontal, row.arrangedSubviews.count == 2 {
                    try requireLayout(abs(row.arrangedSubviews[0].bounds.width - row.arrangedSubviews[1].bounds.width) < 2, "transport type columns have equal widths")
                }
            }
        }
        for child in view.subviews { try checkTypeColumns(child, horizontal: horizontal) }
    }
    func inspectSettings() async throws {
        FileHandle.standardError.write(Data("settings: sizing window\n".utf8))
        owner.window.setContentSize(NSSize(width: 1340, height: 790))
        owner.split.splitView.setPosition(310, ofDividerAt: 0)
        try await inspectTerrainSettings()
        FileHandle.standardError.write(Data("settings: building road form\n".utf8))
        owner.showLayer("道路")
        FileHandle.standardError.write(Data("settings: checking road sections\n".utf8))
        let road = owner.sidebarForm!
        try requireLayout(road.sections.map(\.key) == ["visibility", "overall", "highway", "main", "branch", "special", "quay", "beautification", "actions"].map { "layer.road." + $0 }, "road sections follow product order")
        for key in ["highway", "main", "branch", "special", "quay", "beautification"] {
            try requireLayout(try settingsSection(road, "layer.road." + key).body.isHidden, "road detail initially folded: " + key)
        }
        FileHandle.standardError.write(Data("settings: locating advanced section\n".utf8))
        let highway = try settingsSection(road, "layer.road.highway")
        FileHandle.standardError.write(Data("settings: expanding advanced section\n".utf8))
        highway.toggleTitle()
        FileHandle.standardError.write(Data("settings: settling road layout\n".utf8))
        try await settleLayout()
        FileHandle.standardError.write(Data("settings: editing road width\n".utf8))
        road.restoreScroll(110)
        let offset = road.contentView.bounds.minY
        let oldWidth = object(owner.snapshot["fields"])["road.level.highway.footprint_scale"] as? Double ?? 1
        let width = try setting(road, "road.level.highway.footprint_scale")
        (width.view as! NSTextField).doubleValue = oldWidth == 1.10 ? 1.15 : 1.10
        width.changed(); try await settleSettingsWrites()
        FileHandle.standardError.write(Data("settings: checking road write\n".utf8))
        try requireLayout(owner.sidebarForm === road, "writing a map setting retains its form and focus")
        try requireLayout(abs(road.contentView.bounds.minY - offset) < 2, "writing retains scroll position")
        try requireLayout(!highway.body.isHidden, "writing retains advanced fold state")
        try await capture("settings-map-road")
        owner.update("setField", ["key": "road.level.highway.footprint_scale", "value": oldWidth]); try await settleSettingsWrites()
        checks.append("settings-road-order-default-folds-write-preserves-form-scroll")

        for (name, key) in layerKeys where MapSymbolCategory(rawValue: key) == nil {
            owner.showLayer(name)
            let expected = object(owner.snapshot["layerFields"])[key] as? [String] ?? []
            let visible = Set(owner.sidebarForm!.fields.compactMap { $0.view.identifier?.rawValue })
            try requireLayout(expected.allSatisfy { field in visible.contains(field) || visible.contains(field + ".fixed") || visible.contains(field + ".scaled") }, "layer fields remain accessible: " + key)
        }
        owner.sidebarNavigation.textPages[.iconText] = "style"; owner.showLayer("图标文字")
        try requireLayout(try settingsSection(owner.sidebarForm!, "layer.poi_text.scaling").body.isHidden, "label scaling initially folded")
        let model = try await owner.engine.request("getTextIconSettings", ["target": "normal"])
        let editor = TextIconEditor(owner: owner, model: model, target: "normal", category: .icons); editor.show()
        let draft = try jsonString(editor.model)
        for page in ["style", "merge", "display"] { editor.selectPage(page) }
        try requireLayout(!editor.window.dirty && (try jsonString(editor.model)) == draft, "editor navigation does not change draft")
        editor.set(["profile", "density_tier"], "dense"); editor.selectPage("style"); editor.selectPage("merge")
        try requireLayout(textValue(object(editor.model["profile"])["density_tier"]) == "dense" && editor.window.dirty, "editor page switches preserve unsaved draft")
        editor.window.dirty = false; editor.window.close()
        checks.append("settings-map-field-coverage-and-text-editor-draft-navigation")

        owner.showLayer("道路")
        owner.context = "transit"; owner.rebuildBand(); try await owner.loadTransitSettings()
        owner.sidebarNavigation.transitPage = "stations"; owner.renderTransitSettings()
        let stations = owner.transitForm!
        try requireLayout(stations.sections.map(\.key) == ["transit.stations.symbols", "transit.stations.names", "transit.stations.merge"], "transit stations product order")
        try requireLayout(stations.sections.last!.body.isHidden, "nearby station merge initially folded")
        let icon = try setting(stations, "transit.facilities_enabled")
        let oldIcons = currentBoolean(icon)
        (icon.view as! NSButton).state = .off; icon.changed(); try await settleSettingsWrites()
        try requireLayout(!(try setting(stations, "transit.facility_icon_size").view as! NSControl).isEnabled, "hidden icons disable size")
        try requireLayout(owner.transitForm === stations, "transit writes retain the page instance")
        owner.update("setTransitOption", ["metro": false, "key": "facilities_enabled", "value": oldIcons]); try await settleSettingsWrites()
        try requireLayout(owner.currentTransitOptions(false)["facilities_enabled"] as? Bool == oldIcons, "transit icon switch roundtrips")
        let transitBefore = try jsonString(owner.snapshot["transitOptions"] ?? [:])
        for page in ["base", "lines", "stations"] { owner.sidebarNavigation.transitPage = page; owner.renderTransitSettings() }
        try requireLayout((try jsonString(owner.snapshot["transitOptions"] ?? [:])) == transitBefore, "transit navigation never writes settings")
        try await capture("settings-transit-stations")
        owner.context = "map"; owner.restoreMapSidebar()
        try requireLayout(owner.selectedLayer == "道路", "mode switch restores map detail")
        try requireLayout(try !settingsSection(owner.sidebarForm!, "layer.road.highway").body.isHidden, "mode switch preserves road folds")
        owner.context = "transit"; try await owner.loadTransitSettings()
        try requireLayout(owner.sidebarNavigation.transitPage == "stations" && owner.transitForm!.sections.first!.key == "transit.stations.symbols", "returning to transit restores station page")
        checks.append("settings-transit-pages-conditional-controls-and-mode-navigation")

        owner.context = "metro"; owner.rebuildBand(); try await owner.loadTransitSettings()
        try await owner.rmp.initialize(); owner.rmp.show()
        let metro = owner.transitForm!
        let generationOnly = Set(metro.fields.compactMap { $0.view.identifier?.rawValue })
        try requireLayout(!generationOnly.contains("metro.station_font") && !generationOnly.contains("metro.line_width") && !generationOnly.contains("metro.title_enabled"), "metro shows generation inputs only")
        let oldSchematic = object(owner.currentTransitOptions(true)["schematic_options"])
        owner.metroAppliedFingerprint = owner.metroInputFingerprint()
        let adaptive = try setting(metro, "metro.schematic_options.adaptive_scale")
        (adaptive.view as! NSButton).state = .off; adaptive.changed(); try await settleSettingsWrites()
        try requireLayout(!(try setting(metro, "metro.schematic_options.adaptive_strength").view as! NSControl).isEnabled, "adaptive strength depends on adaptive layout")
        owner.update("setTransitOption", ["metro": true, "key": "schematic_options", "value": ["adaptive_strength": 0.42]], refresh: false)
        try await settleSettingsWrites()
        let edited = object(owner.currentTransitOptions(true)["schematic_options"])
        try requireLayout(edited["adaptive_scale"] as? Bool == false && edited["adaptive_strength"] as? Double == 0.42, "nested edits preserve sibling settings")
        try requireLayout(owner.metroSettingsStatus!.stringValue == localized("设置已修改，当前工程尚未重新生成。"), "metro pending generation is visible")
        try requireLayout(!owner.rmp.hasProject, "editing generation inputs does not generate")
        try await capture("settings-metro-pending")
        let other = try jsonString(owner.snapshot["transitOptions"] ?? [:])
        owner.update("resetMetroGenerationOptions", [:], refresh: false); try await settleSettingsWrites()
        try requireLayout((try jsonString(owner.snapshot["transitOptions"] ?? [:])) == other, "reset only affects metro")
        let metroTypes = owner.currentTransitOptions(true)["types"] as? [String] ?? []
        for key in metroTypes {
            _ = try await owner.engine.request("setTransitType", ["metro": true, "key": key, "enabled": false, "documentEpoch": owner.epoch])
        }
        try await owner.refreshSnapshot()
        try requireLayout(!owner.metroGenerateButton!.isEnabled && !owner.canGenerateMetro(), "empty metro selection disables generation")
        try requireLayout(owner.metroSettingsStatus!.stringValue == localized("请选择至少一种交通类型和一条可用线路。"), "empty selection has an explanation")
        for key in metroTypes {
            _ = try await owner.engine.request("setTransitType", ["metro": true, "key": key, "enabled": true, "documentEpoch": owner.epoch])
        }
        owner.update("setTransitOption", ["metro": true, "key": "schematic_options", "value": oldSchematic], refresh: false); try await settleSettingsWrites()
        checks.append("settings-metro-patch-reset-pending-and-empty-selection")

        for mode in ["map", "transit", "metro"] {
            owner.context = mode; owner.rebuildBand()
            owner.rmp.host.view.isHidden = mode != "metro"; owner.viewer.view.isHidden = mode == "metro"
            if mode == "map" { owner.restoreMapSidebar() } else { try await owner.loadTransitSettings() }
            for width in [1340, 1100] {
                owner.window.setContentSize(NSSize(width: width, height: 790))
                owner.split.splitView.setPosition(width == 1100 ? 240 : 310, ofDividerAt: 0)
                for appearance in [NSAppearance.Name.aqua, .darkAqua] {
                    owner.window.appearance = NSAppearance(named: appearance)
                    try await settleLayout(); try checkGeometry()
                    let form = owner.sidebarForm ?? owner.transitForm!
                    try requireLayout(form.stack.bounds.width <= form.contentView.bounds.width + 1, "settings fit narrow sidebar")
                    if mode == "metro" { try checkTypeColumns(form, horizontal: width == 1340) }
                    try await capture("settings-\(mode)-\(width)-\(appearance.rawValue)")
                }
            }
        }
        checks.append("settings-three-modes-narrow-light-dark-layout")
        owner.metadata = try await owner.engine.request("setLanguage", ["language": "en"])
        try await owner.refreshSnapshot(); owner.applyLanguage(); try await owner.loadTransitSettings()
        try await settleLayout(); try checkGeometry()
        try requireLayout(NativeStrings.translate("地铁生成设置") == "Metro Generation Settings", "generation labels have readable English")
        try await capture("settings-metro-english")
        checks.append("settings-english-mode-preserves-generation-navigation")
    }
    func currentBoolean(_ field: FieldControl) -> Bool { (field.view as? NSButton)?.state == .on }
}
