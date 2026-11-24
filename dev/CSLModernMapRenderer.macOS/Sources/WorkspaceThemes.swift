import AppKit
import WebKit

struct MapTheme: Equatable {
    let id: String
    let name: String
    let preview: String?

    init?(_ record: JSON) {
        guard let id = record["id"] as? String, !id.isEmpty,
              let name = record["name"] as? String, !name.isEmpty else { return nil }
        self.id = id; self.name = name; self.preview = record["preview"] as? String
    }

    var thumbnail: NSImage? {
        let legacy = ["modern": "apple_maps", "bright": "amap", "warm_natural": "google"]
        if let raw = preview, let url = URL(string:raw), url.isFileURL, let image = NSImage(contentsOf:url) { return image }
        let key = preview ?? id
        return resourceImage(key, folder: "Themes") ?? legacy[key].flatMap { resourceImage($0, folder: "Themes") }
    }
}

extension InterfaceController {
    var currentTheme: MapTheme? { themes.first { $0.id == textValue(snapshot["theme"]) } }

    func presentThemeUpdateIfNeeded() {
        let notice = object(snapshot["themeUpdate"])
        let identity = textValue(notice["theme"]) + ":" + textValue(notice["revision"]) + ":" + textValue(notice["sourceDigest"])
        #if CMM_INTEGRATION_CHECKS
        guard smoke == nil else { return }
        #endif
        guard engineReady, !busy, !(fieldWriting && currentTheme?.id == "satellite"), !showingSatelliteGuidance, !notice.isEmpty,
              window.attachedSheet == nil, presentedThemeUpdate != identity else { return }
        presentedThemeUpdate = identity
        let alert = NSAlert()
        alert.messageText = localized("有可用的主题更新")
        alert.informativeText = localized("请查看预设的改动，再选择是否应用到当前主题。")
        let rows = array(notice["changes"])
        let detail = NSTextField(wrappingLabelWithString: rows.compactMap { row -> String? in
            guard let label = row["label"] as? String else { return nil }
            return label + ": " + textValue(row["beforeLabel"] ?? row["before"]) + " → " + textValue(row["afterLabel"] ?? row["after"])
        }.prefix(12).joined(separator: "\n"))
        detail.preferredMaxLayoutWidth = 440
        alert.accessoryView = detail
        alert.addButton(withTitle: localized("使用新版"))
        alert.addButton(withTitle: localized("保留当前设置"))
        let generation = epoch
        alert.beginSheetModal(for: window) { [weak self] response in
            guard let self = self else { return }
            self.run { [self] in
                guard self.epoch == generation, textValue(object(self.snapshot["themeUpdate"])["theme"]) == textValue(notice["theme"]) else { return }
                _ = try await self.engine.request("acknowledgeThemeUpdate", ["documentEpoch": generation, "apply": response == .alertFirstButtonReturn, "theme": notice["theme"] ?? "", "revision": notice["revision"] ?? "", "sourceDigest": notice["sourceDigest"] ?? ""])
                try await self.refreshSnapshot()
                if self.snapshot["hasDocument"] as? Bool == true {
                    _ = try await self.engine.request("refreshPreview")
                    try await self.applySceneState()
                }
            }
        }
    }
}

extension ThemeEditor {
    func libraryMenu() -> NSPopUpButton {
        let menu = NSPopUpButton(frame:.zero,pullsDown:true)
        menu.addItem(withTitle:localized("个人主题"))
        let bases = NSMenu()
        for item in array(draft["themes"]) where item["builtin"] as? Bool == true {
            bases.addItem(ActionMenuItem(textValue(item["name"])) { [weak self] in
                self?.replaceTheme("newThemeDraft", ["baseTheme":item["id"] ?? "modern"])
            })
        }
        let create = NSMenuItem(title:localized("新建个人主题"),action:nil,keyEquivalent:"")
        create.submenu = bases; menu.menu?.addItem(create)
        menu.menu?.addItem(ActionMenuItem(localized("复制主题")) { [weak self] in
            guard let self = self, self.flushInputs() else { return }
            self.replaceTheme("copyThemeDraft", ["data":self.data,"baseTheme":self.draft["baseTheme"] ?? NSNull()])
        })
        menu.menu?.addItem(ActionMenuItem(localized("重命名")) { [weak self] in
            guard let self = self, self.editable else { return }
            if let name = self.promptThemeName(textValue(self.data["name"])) { self.change("name", name); self.rebuild() }
        })
        menu.menu?.addItem(ActionMenuItem(localized("导入主题…")) { [weak self] in
            let chooser = NSOpenPanel(); chooser.allowedFileTypes = ["yml","yaml"]
            if chooser.runModal() == .OK, let url = chooser.url { self?.replaceTheme("importThemeDraft", ["path":url.path]) }
        })
        menu.menu?.addItem(ActionMenuItem(localized("导出主题…")) { [weak self] in self?.saveAs() })
        return menu
    }

    func promptThemeName(_ initial: String) -> String? {
        let alert = NSAlert(); alert.messageText = localized("主题名称")
        let field = NSTextField(string: initial); field.frame.size.width = 260
        alert.accessoryView = field
        alert.addButton(withTitle: localized("确定")); alert.addButton(withTitle: localized("取消"))
        guard alert.runModal() == .alertFirstButtonReturn else { return nil }
        let name = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        return name.isEmpty ? nil : name
    }

    func replaceTheme(_ method: String, _ params: JSON) {
        guard editable, flushInputs() else { return }
        if window.dirty {
            let alert = NSAlert()
            alert.messageText = localized("替换未保存的主题修改？")
            alert.addButton(withTitle:localized("取消")); alert.addButton(withTitle:localized("替换"))
            guard alert.runModal() == .alertSecondButtonReturn else { return }
        }
        window.saving = true
        owner.run { [self] in
            defer { window.saving = false; refreshAvailability() }
            let next = try await owner.engine.request(method, params)
            guard documentCurrent, next["documentEpoch"] as? Int == owner.epoch else { return }
            cancelPreview(); draft = next; undoStack.removeAll(); redoStack.removeAll()
            savedData = ""; inputDirty = false
            sections = next["sections"] as? [String] ?? []
            section.removeAllItems(); section.addItems(withTitles:sections.map(sectionTitle))
            selectedSection = sections.first ?? ""; selectedStyleDetail = nil; window.saving = false
            if let session = owner.cityDraft, session.editor === self { session.show() } else { rebuild() }
            schedulePreview(immediate:true)
        }
    }
}

extension InterfaceController {
    func selectFacilities() {
        let generation = epoch
        run { [self] in
            let response = try await engine.request("getFacilityCatalog")
            guard epoch == generation, context != "metro" else { return }
            let catalog = array(response["facilities"])
            var current = Set<Int>()
            let rows: [JSON] = catalog.enumerated().map { index, item in
                if item["enabled"] as? Bool == true { current.insert(index) }
                return ["id":index,"name":item["name"] ?? "", "type":item["kindLabel"] ?? "", "available":true]
            }
            let selector = RegionSelector(title:"设施筛选",items:rows,selected:current)
            selector.apply = { [weak self] selector in
                guard let self = self, generation == self.epoch else { return }
                self.run {
                    defer { selector.window.saving = false }
                    _ = try await self.engine.request("setFacilitySelection", ["documentEpoch":generation,
                        "ids":selector.selected.sorted().map { textValue(catalog[$0]["id"]) }])
                    selector.window.dirty = false; selector.window.saving = false; selector.window.close()
                    _ = try await self.engine.request("refreshPreview")
                }
            }
            selector.window.retained.append(selector); panels.append(selector.window)
            selector.window.onClose = { [weak self, weak selector] in
                guard let panel = selector?.window else { return }
                panel.retained.removeAll(); self?.panels.removeAll { $0 === panel }
            }
            selector.show()
        }
    }

    func satelliteTextureLimit() async throws -> NSNumber? {
        guard viewer.ready, !staticMode else { return nil }
        let script = "(() => {const gl = document.querySelector('.maplibregl-canvas')?.getContext('webgl2'); return gl ? gl.getParameter(gl.MAX_TEXTURE_SIZE) : null})()"
        return try await viewer.view.evaluateJavaScript(script) as? NSNumber
    }

    func recommendSatellite() {
        let generation = epoch
        guard context == "map", !showingSatelliteGuidance else { return }
        showingSatelliteGuidance = true
        defer { showingSatelliteGuidance = false }
        let available = snapshot["satelliteAvailable"] as? Bool == true
        let alert = NSAlert()
        alert.messageText = localized("卫星地图显示建议")
        var lines = ["推荐搭配卫星暖色主题，突出道路、图标和文字。", "建议开启：道路、图标、文字。",
                     "按需开启：铁路、公共交通、路况，保留当前选择。", "建议关闭：建筑、地形、水域、建成区、用地类型、POI 足迹和 PO 对象。"]
        if !available { lines.append("当前地图没有卫星影像") }
        alert.informativeText = lines.map(localized).joined(separator: "\n\n")
        alert.addButton(withTitle: localized("应用推荐设置")).isEnabled = available
        alert.addButton(withTitle: localized("保持当前设置"))
        guard alert.runModal() == .alertFirstButtonReturn, available, epoch == generation, context == "map" else { return }
        if let editor = themeEditor {
            guard editor.window.permitClose() else { return }
            editor.window.close()
        }
        update("applySatelliteRecommendations", [:])
    }
}
