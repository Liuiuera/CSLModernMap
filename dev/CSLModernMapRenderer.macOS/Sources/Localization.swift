import AppKit

enum NativeStrings {
    static var language = "zh-CN"
    static let catalogs: [String: [String: String]] = {
        guard let url = Bundle.main.url(forResource: "NativeStrings", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let catalogs = try? JSONSerialization.jsonObject(with: data) as? [String: [String: String]] else { return [:] }
        return catalogs
    }()
    static let schemaCatalogs: [String: [String: String]] = {
        guard let url = Bundle.main.url(forResource: "NativeSchemaStrings", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let catalogs = try? JSONSerialization.jsonObject(with: data) as? [String: [String: String]] else { return [:] }
        return catalogs
    }()
    static let winUICatalogs: [String: [String: String]] = {
        guard let url = Bundle.main.url(forResource: "WinUIStrings", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let catalogs = try? JSONSerialization.jsonObject(with: data) as? [String: [String: String]] else { return [:] }
        return catalogs
    }()
    static let nativeUICatalogs: [String: [String: String]] = {
        guard let url = Bundle.main.url(forResource: "NativeUILocalization", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let catalogs = try? JSONSerialization.jsonObject(with: data) as? [String: [String: String]] else { return [:] }
        return catalogs
    }()
    static func translate(_ original: String) -> String {
        if schemaCatalogs["zh-CN"]?[original] == nil, original.hasPrefix("gui.") || original.hasPrefix("style.") {
            let parts = original.components(separatedBy: ".")
            let name = ["label", "title", "desc"].contains(parts.last ?? "") ? parts.dropLast().last! : parts.last!
            return NativePresentation.identifier(name)
        }
        let source = schemaCatalogs["zh-CN"]?[original] ?? original
        if language == "zh-CN" { return source }
        if let value = winUICatalogs[language]?[source] { return value }
        if let value = schemaCatalogs[language]?[original] { return value }
        if let value = nativeUICatalogs[language]?[source] ?? catalogs[language]?[source] { return value }
        let key = source.trimmingCharacters(in: .whitespacesAndNewlines).trimmingCharacters(in: CharacterSet(charactersIn: "…."))
        let current = nativeUICatalogs[language]?[key] ?? catalogs[language]?[key]
        let nativeEnglish = nativeUICatalogs["en"]?[source] ?? nativeUICatalogs["en"]?[key]
        let legacyEnglish = catalogs["en"]?[source] ?? catalogs["en"]?[key]
        if let value = current ?? nativeEnglish ?? legacyEnglish {
            return value + (original.hasSuffix("…") && !value.hasSuffix("…") ? "…" : "")
        }
        return source
    }
}

func localized(_ title: String) -> String { NativeStrings.translate(title) }

extension InterfaceController {
    func applyLanguage() {
        rememberSidebarPosition()
        let selection = selectedObject
        NativeStrings.language = textValue(metadata["language"])
        refreshLocalizedStatusControls()
        showMapState(mapPlaceholder.state)
        for (index, title) in ["开始", "样式", "创建与工具"].enumerated() { tabs.setLabel(localized(title), forSegment: index) }
        buildMenus(); rebuildBand(); buildInspector()
        collapse.toolTip = localized(bandHeight.constant == 0 ? "展开功能区" : "收起功能区")
        syncSidebarVisibility(); syncInspectorVisibility()
        if context == "transit" || context == "metro" { showTransitSettings() }
        else { restoreMapSidebar() }
        if context == "metro" { rmp.show() }
        else if let selection = selection { run { try await self.showObject(selection) } }
    }
}
