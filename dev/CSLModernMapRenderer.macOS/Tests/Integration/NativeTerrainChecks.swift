import AppKit

@MainActor extension NativeSmoke {
    func inspectTerrainSettings() async throws {
        let modes = ["simple", "continuous", "shade"]
        let original = object(owner.snapshot["fields"])
        owner.showLayer("地形")
        let form = owner.sidebarForm!
        let modeField = try setting(form, "terrain.mode")
        try requireLayout(modeField.values.map { textValue($0) } == modes, "terrain offers all three modes")
        try requireLayout(modeField.popup!.itemTitles.allSatisfy { !modes.contains($0) && !$0.hasPrefix("style.") }, "terrain modes have readable names")
        let shadeSection = try settingsSection(form, "layer.terrain.hillshade")
        if shadeSection.body.isHidden { shadeSection.toggleTitle() }
        for (index, mode) in modes.enumerated() {
            modeField.popup!.selectItem(at: index); modeField.changed()
            try await settleSettingsWrites()
            try requireLayout(owner.sidebarForm === form, "terrain mode writes preserve the form")
            try requireLayout(textValue(object(owner.snapshot["fields"])["terrain.mode"]) == mode, "terrain mode roundtrips: " + mode)
            for key in ["terrain.shade", "terrain.sun"] {
                let field = try setting(form, key)
                try requireLayout((field.view as! NSControl).isEnabled == (mode == "shade"), "terrain lighting follows shade mode: " + key)
                try requireLayout(field.slider!.isEnabled == (mode == "shade"), "terrain lighting slider follows shade mode: " + key)
            }
            try await capture("terrain-" + mode)
        }
        for (key, value) in [("terrain.shade", 67.0), ("terrain.sun", 123.0)] {
            let field = try setting(form, key)
            (field.view as! NSTextField).doubleValue = value; field.changed()
            try await settleSettingsWrites()
        }
        let draft = try await owner.engine.request("getThemeDraft")
        let terrain = object(object(draft["data"])["terrain"])
        let lighting = object(terrain["hillshade"])
        try requireLayout(textValue(terrain["mode"]) == "shade" && textValue(terrain["elevation_mode"]) == "continuous", "theme receives continuous shaded terrain")
        try requireLayout(abs((lighting["intensity"] as? Double ?? 0) - 0.67) < 0.000001 && lighting["azimuth"] as? Double == 123, "theme receives lighting units")
        let editor = ThemeEditor(owner: owner, draft: draft); editor.show()
        guard let index = editor.sections.firstIndex(of: "terrain") else { throw AppFailure(message: "Missing terrain theme section") }
        editor.section.selectItem(at: index); editor.rebuild()
        guard let choice = editor.form.fields.first(where: { $0.values.map { textValue($0) } == modes }) else { throw AppFailure(message: "Missing three-state terrain theme field") }
        choice.popup!.selectItem(at: 1); choice.changed()
        try requireLayout(textValue(object(object(editor.draft["data"])["terrain"])["mode"]) == "continuous", "theme draft accepts continuous mode")
        try await owner.refreshSnapshot()
        try requireLayout(textValue(object(owner.snapshot["fields"])["terrain.mode"]) == "shade", "unapplied theme draft preserves live mode")
        for key in ["terrain.hillshade.intensity", "terrain.hillshade.azimuth"] {
            try requireLayout((try setting(editor.form, key).view as! NSControl).isEnabled == false, "theme lighting disabled for continuous mode")
        }
        let color = try setting(editor.form, "terrain.elevation_colors.0.color")
        (color.view as! NSColorWell).color = FieldControl.color("#123456"); color.changed()
        var stops = array(pathValue(editor.data, ["terrain", "elevation_colors"]))
        try requireLayout(textValue(stops.first?["color"]) == "#123456", "color stop updates whole ramp")
        stops[0]["color"] = "#654321"
        editor.change("terrain.elevation_colors", stops); editor.rebuild()
        let updatedColor = try setting(editor.form, "terrain.elevation_colors.0.color")
        try requireLayout((updatedColor.view as! NSColorWell).color == FieldControl.color("#654321"), "whole ramp updates color stop")
        editor.commit(save: false)
        for _ in 0..<200 {
            if textValue(object(owner.snapshot["fields"])["terrain.mode"]) == "continuous" { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        try requireLayout(textValue(object(owner.snapshot["fields"])["terrain.mode"]) == "continuous", "theme apply updates sidebar mode")
        try requireLayout(textValue(object(owner.snapshot["fields"])["terrain.color"]) == "#654321", "theme apply updates sidebar color")
        editor.window.dirty = false; editor.window.close()
        for key in ["terrain.mode", "terrain.shade", "terrain.sun", "terrain.color"] {
            owner.update("setField", ["key": key, "value": original[key]!])
        }
        try await settleSettingsWrites()
        checks.append("terrain-three-modes-lighting-theme-apply-and-ramp-sync")
    }

    func inspectTerrainExports() async throws {
        for mode in ["simple", "continuous", "shade"] {
            owner.update("setField", ["key": "terrain.mode", "value": mode])
            try await settleSettingsWrites()
            exportID = UUID().uuidString
            let result: JSON = try await withCheckedThrowingContinuation { continuation in
                exportContinuation = continuation
                owner.run { [self] in
                    do {
                        let started = try await owner.engine.request("export", ["format": "PNG", "context": "normal", "documentEpoch": owner.epoch,
                            "target": output.appendingPathComponent("terrain-" + mode + "-export.png").path,
                            "requestId": exportID, "output": ["size": 1024], "auto_open": false])
                        guard started["started"] as? Bool == true else { throw AppFailure(message: "Terrain PNG export did not start") }
                    } catch { exportContinuation?.resume(throwing: error); exportContinuation = nil }
                }
            }
            checks.append("terrain-webkit-and-png-" + mode + ":" + textValue(result["path"]))
            try await capture("terrain-" + mode + "-map")
        }
    }
}
