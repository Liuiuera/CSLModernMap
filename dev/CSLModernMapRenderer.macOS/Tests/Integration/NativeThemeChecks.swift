import AppKit

@MainActor extension NativeSmoke {
    func inspectThemes() async throws {
        let expected = ["modern", "urban_white", "bright", "warm_natural", "satellite", "dark", "vintage"]
        let builtins = array(owner.snapshot["themes"]).filter { $0["builtin"] as? Bool == true }
        try requireLayout(builtins.map { textValue($0["id"]) } == expected, "seven canonical themes")
        try requireLayout(owner.themes.filter { expected.contains($0.id) }.allSatisfy { $0.thumbnail != nil }, "seven theme thumbnails")
        owner.window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        try await settleLayout()
        try openThemeGallery()
        guard let popover = owner.gallery, let gallery = popover.contentViewController?.view else {
            throw AppFailure(message: "Theme gallery did not open")
        }
        try await settleLayout()
        try requireLayout(popover.isShown && gallery.window != nil, "theme gallery appears on screen")
        gallery.layoutSubtreeIfNeeded()
        try requireLayout(popover.contentSize.height > 200 && popover.contentSize.height <= 406, "theme gallery scroll area")
        if let number = gallery.window?.windowNumber,
           let image = CGWindowListCreateImage(.null, .optionIncludingWindow, CGWindowID(number), [.boundsIgnoreFraming, .bestResolution]) {
            try NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: output.appendingPathComponent("themes-gallery.png"))
        }
        owner.gallery?.close()
        checks.append("seven-native-themes-and-gallery")

        let original = textValue(owner.snapshot["theme"])
        for identity in expected {
            _ = try await owner.engine.request("setTheme", ["theme": identity, "documentEpoch": owner.epoch])
            try await owner.refreshSnapshot(); try await settleSettingsWrites()
            try requireLayout(owner.currentTheme?.id == identity, "theme selection " + identity)
        }
        checks.append("seven-theme-selection-roundtrip")
        _ = try await owner.engine.request("setTheme", ["theme": "modern", "documentEpoch": owner.epoch])
        try await owner.refreshSnapshot(); try await settleSettingsWrites()
        let draft = try await owner.engine.request("getThemeDraft")
        let editor = ThemeEditor(owner: owner, draft: draft)
        owner.themeEditor = editor; editor.show()
        try requireLayout(editor.entries().contains("name") && editor.entries().contains("description"), "theme name and description are editable")
        var data = object(editor.draft["data"])
        data["name"] = "原生个人主题验收"
        var water = object(data["water"]); water["color"] = "#13579B"; data["water"] = water
        editor.draft["data"] = data; editor.rebuild(); editor.commit(save: false)
        try await waitForThemeCommit(editor)
        try requireLayout(editor.documentCurrent && !editor.invalidated, "theme editor remains usable after apply")
        try requireLayout(textValue(owner.snapshot["theme"]) == "modern", "apply retains builtin identity")
        var applied = try await owner.engine.request("getThemeDraft")
        try requireLayout(textValue(object(object(applied["data"])["water"])["color"]) == "#13579B", "applied color")
        try requireLayout(textValue(object(object(applied["savedData"])["water"])["color"]) != "#13579B", "apply is session only")
        editor.commit(save: true)
        try await waitForThemeCommit(editor)
        let personal = textValue(owner.snapshot["theme"])
        try requireLayout(personal.hasPrefix("user_") && editor.documentCurrent && !editor.invalidated, "save creates editable personal identity")
        try requireLayout(owner.currentTheme?.name == "原生个人主题验收", "personal theme name roundtrip")
        try requireLayout(!editor.window.dirty, "save clears dirty status")
        applied = try await owner.engine.request("getThemeDraft")
        try requireLayout(textValue(object(object(applied["savedData"])["water"])["color"]) == "#13579B", "personal color persisted")
        try requireLayout(owner.currentTheme?.thumbnail != nil, "personal theme retains base thumbnail")
        try await capture("theme-personal-saved")
        if let image = CGWindowListCreateImage(.null, .optionIncludingWindow, CGWindowID(editor.window.windowNumber), [.boundsIgnoreFraming, .bestResolution]) {
            try NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: output.appendingPathComponent("theme-editor-saved.png"))
        }
        editor.window.close()
        checks.append("native-theme-apply-save-personal-and-editor-continuity")
        _ = try await owner.engine.request("setTheme", ["theme": original, "documentEpoch": owner.epoch])
        try await owner.refreshSnapshot(); try await settleSettingsWrites()
    }

    func waitForThemeCommit(_ editor: ThemeEditor) async throws {
        for _ in 0..<1200 {
            if !editor.window.saving {
                try requireLayout(!editor.operationStatus.stringValue.contains(localized("主题操作失败：")), editor.operationStatus.stringValue)
                try await settleSettingsWrites()
                return
            }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        throw AppFailure(message: "Theme commit timed out")
    }
}
