import AppKit

@MainActor extension NativeSmoke {
    func inspectTextIconSettings() async throws {
        let before = try await owner.engine.request("getTextIconSettings", ["target": "normal"])
        let transitBefore = try await owner.engine.request("getTextIconSettings", ["target": "transit"])
        let metroBefore = try await owner.engine.request("getTextIconSettings", ["target": "metro"])
        let editor = TextIconEditor(owner: owner, model: before, target: "normal"); editor.show()
        try requireLayout(editor.form.sections.map(\.key) == ["display", "visibility", "stations", "sources"].map { "text.normal.poi_text." + $0 }, "normal display follows task groups")
        editor.set(["mapSymbolVisibility", "poi_text"], false)
        try requireLayout(!(try setting(editor.form, "visibility.names.ordinary").view as! NSControl).isEnabled, "icon label switch gates place names")
        try requireLayout(!editor.editableFieldKeys.contains("label.roads") && !editor.editableFieldKeys.contains("label.districts"), "icon label editor excludes roads and districts")
        try requireLayout(!editor.form.fields.contains { $0.view.identifier?.rawValue == "visibility.icons.ordinary" }, "text uses name controls only")
        editor.restoreDefaults()
        editor.selectPage("style")
        _ = try setting(editor.form, "poi.name_stroke_width")
        editor.window.dirty = false
        editor.window.close()
        owner.execute("图标")
        for _ in 0..<200 { if owner.textEditor != nil { break }; try await Task.sleep(nanoseconds: 50_000_000) }
        guard let icons = owner.textEditor else { throw AppFailure(message: "Icon command did not open its editor") }
        _ = try setting(icons.form, "visibility.icons.ordinary")
        try requireLayout(!icons.form.fields.contains { $0.view.identifier?.rawValue == "visibility.names.ordinary" }, "icons use separate display controls")
        icons.selectPage("style")
        _ = try setting(icons.form, "poi.size")
        icons.selectPage("merge")
        try requireLayout(icons.form.sections.map(\.key) == ["common", "transit", "ordinary", "facility", "area", "density", "advanced"].map { "text.normal.poi.merge." + $0 }, "merge categories follow WinUI order")
        try requireLayout(try settingsSection(icons.form, "text.normal.poi.merge.advanced").body.isHidden, "advanced options start folded")
        icons.set(["profile", "merge_enabled"], false)
        try requireLayout(!(try setting(icons.form, "profile.groups.transit.merge_enabled").view as! NSControl).isEnabled && !(try setting(icons.form, "profile.groups.transit.distance_m").view as! NSControl).isEnabled, "merge master gates categories and distance")
        icons.set(["profile", "merge_enabled"], true); icons.set(["profile", "groups", "transit", "merge_enabled"], false)
        try requireLayout(!(try setting(icons.form, "profile.allow_different_station_names").view as! NSControl).isEnabled && (try setting(icons.form, "profile.children_min_zoom").view as! NSControl).isEnabled, "advanced gates follow transit merge; zoom remains independent")
        let zoom = try setting(icons.form, "profile.children_min_zoom")
        try requireLayout(zoom.high == 2, "internal building zoom range matches engine")
        (zoom.view as! NSTextField).stringValue = "3"
        var rejected = false
        do { try icons.commitInputs() } catch { rejected = true }
        try requireLayout(rejected, "invalid numeric input prevents applying draft")
        (zoom.view as! NSTextField).doubleValue = 1.25; zoom.changed()
        let density = try setting(icons.form, "profile.density_enabled")
        (density.view as! NSButton).state = .off; density.changed()
        try requireLayout(!(try setting(icons.form, "profile.density_tier").view as! NSControl).isEnabled, "density disabled retains inactive tier")
        icons.restoreDefaults()
        let unchanged = try await owner.engine.request("getTextIconSettings", ["target": "normal"])
        try requireLayout(try jsonString(unchanged) == jsonString(before), "restoring defaults stays in draft")
        try requireLayout(try jsonString(icons.model["profile"]!) == jsonString(before["profileDefaults"]!), "profile defaults restored")
        icons.set(["fields", "poi.size"], 32.0)
        icons.set(["profile", "density_tier"], "dense")
        icons.save()
        for _ in 0..<200 {
            if object(owner.snapshot["fields"])["poi.size"] as? Double == 32 { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let reopened = try await owner.engine.request("getTextIconSettings", ["target": "normal"])
        try requireLayout(object(reopened["fields"])["poi.size"] as? Double == 32, "applied size reads back")
        try requireLayout(textValue(object(reopened["profile"])["density_tier"]) == "dense" && !icons.window.dirty, "applied profile reads back and clears dirty state")
        let transitAfter = try await owner.engine.request("getTextIconSettings", ["target": "transit"])
        try requireLayout(try jsonString(transitBefore["profile"]!) == jsonString(transitAfter["profile"]!), "normal profile leaves transit profile unchanged")
        icons.window.dirty = false; icons.window.close()
        let transitEditor = TextIconEditor(owner: owner, model: transitAfter, target: "transit"); transitEditor.show(); transitEditor.selectPage("display")
        try requireLayout(transitEditor.form.sections.map(\.key) == ["visibility", "stations", "sources"].map { "text.transit.poi_text." + $0 }, "transit only shows applicable display groups")
        try requireLayout(!transitEditor.editableFieldKeys.contains("road.game_names") && !transitEditor.editableFieldKeys.contains("label.size.road"), "transit edits exclude normal-only fields")
        let placement = try setting(transitEditor.form, "options.station_name_placement")
        placement.popup!.selectItem(at: 0); placement.changed()
        try requireLayout(!(try setting(transitEditor.form, "options.station_name_size").view as! NSControl).isEnabled, "hidden station names disable size")
        transitEditor.set(["options", "station_name_size"], 14.0)
        transitEditor.save()
        for _ in 0..<200 { if !transitEditor.window.dirty { break }; try await Task.sleep(nanoseconds: 50_000_000) }
        try requireLayout(!transitEditor.window.dirty, "transit settings saved")
        let normalAfter = try await owner.engine.request("getTextIconSettings", ["target": "normal"])
        let metroAfter = try await owner.engine.request("getTextIconSettings", ["target": "metro"])
        try requireLayout(try jsonString(normalAfter["profile"]!) == jsonString(reopened["profile"]!) && jsonString(metroAfter["profile"]!) == jsonString(metroBefore["profile"]!) && jsonString(metroAfter["options"]!) == jsonString(metroBefore["options"]!), "transit save leaves normal profile and metro settings unchanged")
        transitEditor.window.dirty = false; transitEditor.window.close()
        owner.buildSidebar()
        try requireLayout(owner.layerNodes.first { $0.id == "labels" }?.children.map(\.title) == ["图标文字", "道路文字", "区划文字", "图标"], "map list shows four symbol settings")
        for category in [MapSymbolCategory.roadText, .districtText] {
            owner.execute(category.title)
            for _ in 0..<200 where owner.textEditor == nil { try await Task.sleep(nanoseconds: 50_000_000) }
            guard let editor = owner.textEditor else { throw AppFailure(message: "Missing " + category.title) }
            try requireLayout(editor.category == category, "toolbar opens the matching editor")
            _ = try setting(editor.form, category.visibilityKey)
            editor.selectPage("style")
            _ = try setting(editor.form, "label.stroke_width." + category.textStyleKinds[0])
            try requireLayout(!editor.editableFieldKeys.contains("poi.name_size"), "road and district editors isolate styles")
            editor.window.close()
        }
        owner.showLayer("道路文字")
        let road = try setting(owner.sidebarForm!, "layer.road_text.visible")
        let oldRoad = owner.defaultVisibility("道路文字")
        let oldDistrict = owner.defaultVisibility("区划文字")
        let oldIconText = owner.defaultVisibility("图标文字")
        (road.view as! NSButton).state = oldRoad ? .off : .on
        road.changed(); try await settleSettingsWrites()
        try requireLayout(owner.defaultVisibility("道路文字") != oldRoad && owner.defaultVisibility("区划文字") == oldDistrict && owner.defaultVisibility("图标文字") == oldIconText, "road switch changes only road labels")
        owner.showLayer("图标文字")
        let sidebar = owner.sidebarForm!
        try requireLayout(sidebar.sections.map(\.key) == ["visibility", "places", "stations", "sources", "actions"].map { "layer.poi_text." + $0 }, "icon label sidebar groups")
        let ordinaryName = try setting(sidebar, "label.ordinary_icon_text")
        let savedName = currentBoolean(ordinaryName)
        let previousIcon = object(owner.snapshot["labelChildren"])["ordinary_icon"] as? Bool == true
        let switchWasOn = owner.defaultVisibility("图标")
        owner.changeLayerVisibility("poi", enabled: true)
        owner.changeLayerVisibility("poi_text", enabled: true)
        owner.update("setLabelChild", ["key": "ordinary_icon", "enabled": false]); try await settleSettingsWrites()
        try requireLayout(!(ordinaryName.view as! NSControl).isEnabled && currentBoolean(ordinaryName) == savedName, "hiding an icon disables its name and preserves the name preference")
        owner.update("setLabelChild", ["key": "ordinary_icon", "enabled": true]); try await settleSettingsWrites()
        try requireLayout((ordinaryName.view as! NSControl).isEnabled && currentBoolean(ordinaryName) == savedName, "restoring an icon restores access to its name preference")
        owner.update("setLabelChild", ["key": "ordinary_icon", "enabled": previousIcon])
        owner.changeLayerVisibility("poi", enabled: switchWasOn)
        owner.changeLayerVisibility("poi_text", enabled: oldIconText)
        owner.changeLayerVisibility("road_text", enabled: oldRoad)
        try await settleSettingsWrites()
        owner.sidebarNavigation.textPages[.icons] = "style"
        owner.showLayer("图标")
        let sidebarSize = try setting(owner.sidebarForm!, "poi.size")
        (sidebarSize.view as! NSTextField).doubleValue = 40; sidebarSize.changed(); try await settleSettingsWrites()
        try requireLayout(object(owner.snapshot["fields"])["poi.size"] as? Double == 40, "sidebar icon size uses reference pixels")
        try await capture("text-icon-settings")
        owner.textEditor = nil; owner.context = "metro"; owner.showTextIconEditor()
        for _ in 0..<200 { if owner.sidebarNavigation.activeKey == "metro.generation" { break }; try await Task.sleep(nanoseconds: 50_000_000) }
        try requireLayout(owner.sidebarNavigation.activeKey == "metro.generation" && owner.textEditor == nil, "metro entry routes to its independent generation settings")
        checks.append("text-icons-separate-controls-defaults-validation-apply-and-view-scope")
        checks.append("text-icons-task-groups-conditional-controls-and-metro-entry")
    }

    func inspectTextIconExport() async throws {
        owner.context = "map"
        owner.showExport()
        for _ in 0..<600 where owner.activeExport == nil { try await Task.sleep(nanoseconds: 50_000_000) }
        guard let normal = owner.activeExport else { throw AppFailure(message: "Missing map export window") }
        normal.target = output.appendingPathComponent("text-icons.png")
        normal.autoOpen = false
        normal.changeSize(1024)
        try await normal.submit()
        for _ in 0..<2400 where normal.window.saving { try await Task.sleep(nanoseconds: 50_000_000) }
        try requireLayout(NSImage(contentsOf: normal.target!) != nil, "Map export window wrote PNG")
        normal.window.close()
        let theme = try await owner.engine.request("getThemeDraft")
        let draft = CityDraft(owner: owner, theme: theme)
        draft.displayedTheme = try jsonString(draft.editor.data)
        owner.cityDraft = draft
        try await draft.request("beginCityArtwork", ["kind":"normal", "longEdge":512, "rotation":90, "elementScale":0.5])
        draft.starting = false
        draft.export()
        for _ in 0..<600 where owner.activeExport == nil { try await Task.sleep(nanoseconds: 50_000_000) }
        guard let dialog = owner.activeExport else { throw AppFailure(message: "Missing draft export window") }
        let name = ExportNaming.filename(city: "A/B?. ", context: "normal", format: "PNG")
        try requireLayout(name.hasPrefix("A_B__" + localized("地图") + "_") && name.hasSuffix(".png"), "Windows filename components and escaping")
        try requireLayout(dialog.draft === draft && dialog.output["size"] as? Int == 512, "Export window uses draft settings")
        dialog.target = output.appendingPathComponent(name)
        dialog.autoOpen = false
        try await dialog.submit()
        for _ in 0..<2400 where dialog.window.saving { try await Task.sleep(nanoseconds: 50_000_000) }
        let bitmap = try NSBitmapImageRep(data: Data(contentsOf: dialog.target!))
        try requireLayout(max(bitmap?.pixelsWide ?? 0, bitmap?.pixelsHigh ?? 0) == 512 && draft.exportID.isEmpty, "Draft export writes the requested dimensions and releases the editor")
        dialog.window.close()
        checks.append("map-and-draft-export-windows-write-png")
    }
}
