import AppKit

@MainActor extension NativeSmoke {
    func inspectFriendlyNames() async throws {
        NativeStrings.language = "zh-CN"
        try requireLayout(localized("style.poi.set.label") == "图标风格", "translation keys resolve in Chinese")
        try requireLayout(localized("gui.future.new_setting") == "New Setting", "missing translation has a readable fallback")
        let palette = FieldControl("线路颜色", schema: ["kind": "choice", "choices": ["game", "classic", "soft"].map { ["value": $0] }], value: "classic") { _ in }
        try requireLayout(palette.popup!.itemTitles == ["游戏原色", "经典配色", "柔和配色"] && palette.values as? [String] == ["game", "classic", "soft"], "palette titles preserve stored values")
        try requireLayout(NativePresentation.choice(["value": "PNG", "label": "PNG"]) == "PNG", "format names retain capitalization")
        let draft = try await owner.engine.request("getThemeDraft")
        let editor = ThemeEditor(owner: owner, draft: draft); editor.show()
        for section in ["roads", "labels", "transports"] {
            guard let index = editor.sections.firstIndex(of: section) else { continue }
            editor.section.selectItem(at: index); editor.rebuild()
            for field in editor.form.fields {
                let title = (field.view.toolTip ?? "").components(separatedBy: "\n")[0]
                try requireLayout(title.range(of: #"\b(?:gui|style)\.|\b[a-z]+_[a-z_]+\b|\b(?:main|road|district|types|Metro) ·"#, options: .regularExpression) == nil, "readable theme title: " + title)
            }
        }
        try requireLayout(try jsonString(editor.draft["data"]!) == jsonString(draft["data"]!), "viewing translated fields leaves theme data unchanged")
        if let index = editor.sections.firstIndex(of: "roads") {
            editor.section.selectItem(at: index); editor.rebuild()
            editor.window.contentView?.layoutSubtreeIfNeeded()
            try await Task.sleep(nanoseconds: 150_000_000); editor.window.displayIfNeeded()
            if let image = CGWindowListCreateImage(.null, .optionIncludingWindow, CGWindowID(editor.window.windowNumber), [.boundsIgnoreFraming, .bestResolution]) {
                try NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:])?.write(to: output.appendingPathComponent("theme-friendly-names.png"))
            }
        }
        NativeStrings.language = "en"
        try requireLayout(localized("style.poi.set.label") == "Icon style", "translation keys resolve in English")
        for language in NativePresentation.languages.keys.sorted() where language != "zh-CN" {
            NativeStrings.language = language
            try requireLayout(NativeStrings.nativeUICatalogs[language]?.count == NativeStrings.nativeUICatalogs["en"]?.count,
                              "complete native catalog: " + language)
            try requireLayout(NativeStrings.schemaCatalogs[language]?.count == NativeStrings.schemaCatalogs["zh-CN"]?.count,
                              "complete schema catalog: " + language)
            try requireLayout(String(format: localized("已完成：%@"), "/tmp/city.png").contains("/tmp/city.png"),
                              "localized completion retains path: " + language)
        }
        NativeStrings.language = "en"
        editor.rebuild()
        try requireLayout(editor.form.fields.allSatisfy { !($0.view.toolTip ?? "").contains("style.") }, "English fields retain readable titles")
        NativeStrings.language = "zh-CN"
        editor.window.dirty = false; editor.window.close()
        let poiModel = try await owner.engine.request("getPoiEditor")
        let poi = PoiEditor(owner: owner, model: poiModel); poi.show()
        try requireLayout(Set(poi.selector.itemTitles).count == poi.keys.count && poi.selector.itemTitles.allSatisfy { !$0.contains("::") && !$0.contains("BUILDING_TITLE[") }, "mapping selector keeps duplicate names distinct without internal keys")
        try requireLayout(NativePresentation.identifier("cn_icons") == "现代地图图标库", "icon library has a readable name")
        poi.window.close()
        owner.context = "transit"; owner.sidebarNavigation.transitPage = "lines"
        try await owner.loadTransitSettings()
        let colors = try setting(owner.transitForm!, "transit.line_color_mode")
        try requireLayout(colors.popup!.itemTitles == ["游戏原色", "经典配色", "柔和配色"], "transit palette uses readable titles")
        try await capture("transit-friendly-names")
        try requireLayout(NativePresentation.errorMessage("style.poi.set.label：请选择。") == "图标风格：请选择。", "error messages resolve embedded translation keys")
        checks.append("friendly-theme-mapping-enums-units-fallbacks-and-errors")
    }
}
