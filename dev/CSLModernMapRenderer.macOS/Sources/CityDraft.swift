import AppKit

/// Bind the engine draft to the current WebKit scene.
@MainActor final class CityDraft: NSObject, NSTableViewDataSource, NSTableViewDelegate {
    unowned let owner: InterfaceController
    let editor: ThemeEditor
    let epoch: Int
    var revision = 0
    var operation = 0
    var viewerRevision = 0
    var presented = 0
    var pending: JSON?
    var displayedTheme = ""
    var exportID = ""
    private static let categorySymbols = [
        "__basic__":"info.circle", "canvas":"rectangle", "satellite":"globe.americas", "terrain":"mountain.2",
        "water":"water.waves", "forest":"leaf", "built_area":"square.dashed", "zone":"square.on.square",
        "land_use":"grid", "district":"map", "roads":"road.lanes", "buildings":"building.2",
        "procedural_objects":"cube", "railway":"tram", "traffic":"car", "transports":"bus",
        "poi_icons":"mappin.and.ellipse", "labels":"textformat", "watermark":"signature"
    ]
    private let categories = NSTableView()
    private var styleGeneration = 0
    private var pendingCloseup: [Double]?
    private var observation: JSON?
    private var restoreObservation: JSON?
    private let originalPanels: (left: Bool, right: Bool)
    var starting = true
    var creating = false
    private(set) var refreshing = false
    var working: Bool { creating || refreshing }
    private(set) var finishing = false
    var customResolution = false
    var hasChanges: Bool {
        editor.window.dirty || owner.snapshot["transitDraftChanged"] as? Bool == true
    }

    init(owner: InterfaceController, theme: JSON) {
        self.owner = owner
        originalPanels = (owner.leftItem.isCollapsed, owner.rightItem.isCollapsed)
        epoch = owner.epoch
        editor = ThemeEditor(owner: owner, draft: theme)
        if owner.context == "transit" { editor.selectedSection = "transports" }
        super.init()
        categories.headerView = nil
        categories.style = .sourceList
        categories.rowSizeStyle = .medium
        categories.allowsEmptySelection = false
        categories.backgroundColor = .clear
        categories.columnAutoresizingStyle = .uniformColumnAutoresizingStyle
        categories.addTableColumn(NSTableColumn(identifier: NSUserInterfaceItemIdentifier("category")))
        categories.dataSource = self
        categories.delegate = self
        categories.setAccessibilityLabel(localized("草稿样式"))
    }

    func request(_ method: String, _ params: JSON = [:]) async throws {
        guard owner.cityDraft === self, owner.epoch == epoch else { return }
        operation += 1
        let current = operation
        var values = params
        values["operationId"] = current
        values["documentEpoch"] = epoch
        presented = 0; pending = nil
        if !creating && method != "beginCityArtwork" {
            refreshing = true
            owner.showStatus(localized("正在刷新草稿") + "…", kind: .progress)
        }
        let response: JSON
        do { response = try await owner.engine.request(method, values) }
        catch {
            if current == operation, owner.cityDraft === self { previewFailed() }
            throw error
        }
        guard current == operation, owner.cityDraft === self else { return }
        revision = response["artworkRevision"] as? Int ?? revision
    }

    func reserveTransitRefresh() -> Int {
        // Invalidate the pending render before updating options.
        operation += 1
        pending = nil
        refreshing = true
        owner.showStatus(localized("正在刷新草稿") + "…", kind: .progress)
        return operation
    }

    func clearUnusedTransitRefresh(_ reservation: Int) {
        guard owner.cityDraft === self, operation == reservation, refreshing else { return }
        previewFailed()
        owner.dismissStatus()
    }

    func send(_ type: String, _ values: JSON = [:]) {
        var command = values
        command["type"] = type
        command["documentEpoch"] = epoch
        command["artworkRevision"] = revision
        command["sceneId"] = owner.sceneID
        command["revision"] = owner.revision
        owner.viewer.send(command)
    }

    func show() {
        editor.sections = editor.draft["sections"] as? [String] ?? []
        if !editor.sections.contains(editor.selectedSection) {
            editor.selectedSection = editor.sections.first ?? ""
            editor.selectedStyleDetail = nil
        }
        editor.rebuild()
        owner.panelHeader(owner.leftHeader, title: "草稿样式")
        owner.panelHeader(owner.rightHeader, title: "样式参数", inspector: true)
        owner.leftContent.subviews.forEach { $0.removeFromSuperview() }
        owner.rightContent.subviews.forEach { $0.removeFromSuperview() }
        let navigation = NSScrollView()
        navigation.drawsBackground = false
        navigation.hasVerticalScroller = true
        navigation.autohidesScrollers = true
        navigation.documentView = categories
        categories.reloadData()
        synchronizeCategory()
        pin(navigation, in: owner.leftContent)
        pin(editor.form, in: owner.rightContent)
        owner.leftItem.isCollapsed = false
        owner.rightItem.isCollapsed = false
        owner.syncSidebarVisibility()
        owner.syncInspectorVisibility()
        owner.rebuildBand()
    }

    private var styleRows: [String] {
        editor.sections.flatMap { section in
            if section == "roads" {
                let roads = object(editor.data["roads"])
                return [section] + ThemeEditor.roadStyleLevels.filter { roads[$0] is JSON }.map { section + "." + $0 }
            }
            if section == "labels" { return [section, "labels.road", "labels.district"] }
            return [section]
        }
    }
    private var stylePath: String {
        editor.selectedSection + (editor.selectedStyleDetail.map { "." + $0 } ?? "")
    }
    private func styleTitle(_ path: String) -> String {
        switch path {
        case "roads": return editor.sectionTitle(path)
        case "labels.road": return localized("道路名称")
        case "labels.district": return localized("行政区名称")
        default:
            let parts = path.split(separator: ".", maxSplits: 1).map(String.init)
            return parts.count == 1 ? editor.sectionTitle(path) : NativePresentation.identifier(parts[1])
        }
    }

    func numberOfRows(in tableView: NSTableView) -> Int { styleRows.count }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let path = styleRows[row]
        let key = String(path.prefix(while: { $0 != "." }))
        let title = styleTitle(path)
        let cell = NSTableCellView()
        let name = NSTextField(labelWithString: title)
        name.font = .systemFont(ofSize: NSFont.systemFontSize)
        name.lineBreakMode = .byTruncatingTail
        name.translatesAutoresizingMaskIntoConstraints = false
        cell.textField = name
        cell.addSubview(name)
        let icon = NSImageView()
        icon.image = symbol(Self.categorySymbols[key] ?? "slider.horizontal.3", size: 15)
        icon.contentTintColor = .controlAccentColor
        icon.translatesAutoresizingMaskIntoConstraints = false
        icon.setAccessibilityElement(false)
        cell.imageView = icon
        cell.addSubview(icon)
        NSLayoutConstraint.activate([icon.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: key == path ? 5 : 22),
            icon.centerYAnchor.constraint(equalTo: cell.centerYAnchor), icon.widthAnchor.constraint(equalToConstant: 18),
            name.leadingAnchor.constraint(equalTo: icon.trailingAnchor, constant: 8),
            name.trailingAnchor.constraint(equalTo: cell.trailingAnchor), name.centerYAnchor.constraint(equalTo: cell.centerYAnchor)])
        return cell
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        let rows = styleRows
        guard rows.indices.contains(categories.selectedRow), rows[categories.selectedRow] != stylePath else { return }
        selectStyle(rows[categories.selectedRow])
        locateStyle()
    }

    private func synchronizeCategory() {
        guard let index = styleRows.firstIndex(of: stylePath) else { return }
        if categories.selectedRow != index {
            categories.selectRowIndexes(IndexSet(integer: index), byExtendingSelection: false)
        }
        categories.scrollRowToVisible(index)
        owner.panelHeader(owner.rightHeader, title: styleTitle(stylePath), inspector: true)
    }

    func selectStyle(_ path: String, facts: JSON? = nil) {
        let parts = path.split(separator: ".").map(String.init)
        guard let key = parts.first else { return }
        guard let index = editor.sections.firstIndex(of: key) else { return }
        guard editor.flushInputs() else { synchronizeCategory(); return }
        styleGeneration += 1; pendingCloseup = nil
        send("cancelArtworkLocate")
        editor.selectedSection = key
        let candidate = parts.prefix(2).joined(separator: ".")
        editor.selectedStyleDetail = parts.count > 1 && styleRows.contains(candidate) ? parts[1] : nil
        if editor.section.numberOfItems > index { editor.section.selectItem(at: index) }
        editor.rebuild()
        if let facts {
            editor.form.section(textValue(facts["title"]), key: "draft.object")
            for fact in array(facts["summary"]) + array(facts["details"]) {
                editor.form.add(owner.settingsHelp(textValue(fact["label"]) + " · " + textValue(fact["value"])))
            }
            editor.form.add(ActionButton("定位") { [weak self] in self?.send("locateObject") })
        }
        synchronizeCategory()
    }

    func pickStyle(_ frame: JSON) {
        guard frame["documentEpoch"] as? Int == epoch, frame["artworkRevision"] as? Int == revision else { return }
        styleGeneration += 1
        let generation = styleGeneration
        owner.run { [self] in
            let model = try await owner.engine.request("getObjectInspector",
                ["object": frame, "context": owner.context, "documentEpoch": epoch])
            guard owner.cityDraft === self, generation == styleGeneration, frame["artworkRevision"] as? Int == revision else { return }
            selectStyle(textValue(model["stylePath"] ?? model["themeSection"]), facts: model)
        }
    }

    func clearPickedStyle() {
        styleGeneration += 1
        editor.rebuild()
    }

    func updateTheme() async throws {
        let encoded = try jsonString(editor.data)
        guard encoded != displayedTheme else { return }
        displayedTheme = encoded
        do { try await request("previewCityArtworkTheme", ["data": editor.data, "theme": editor.draft["theme"] ?? "", "debounce": true]) }
        catch { if displayedTheme == encoded { displayedTheme = "" }; throw error }
    }

    func deliver() {
        guard let frame = pending, owner.presentedScene == owner.previewIdentity,
              frame["sceneId"] as? String == owner.sceneID, frame["revision"] as? Int == owner.revision else { return }
        pending = nil
        let artwork = object(frame["artwork"])
        guard frame["operationId"] as? Int == operation, let next = artwork["revision"] as? Int else { return }
        revision = next; viewerRevision = next
        send("beginArtwork")
        send("loadArtwork", ["artwork": artwork])
    }

    func engineEvent(_ name: String, _ frame: JSON) {
        guard frame["operationId"] as? Int == operation else { return }
        if name == "artworkProgress", working {
            let stage: String
            switch frame["stage"] as? String {
            case "waitingBase", "base": stage = "正在准备底图"
            case "labels": stage = "正在排版文字"
            case "icons": stage = "正在布置图标"
            case "transport": stage = "正在绘制公共交通"
            case "delivering": stage = "正在显示草稿"
            default: stage = "正在排版草稿"
            }
            let percent = frame["percent"] as? Double
            let detail: String
            if stage != "正在显示草稿", let percent, percent.isFinite, (0...100).contains(percent) {
                detail = localized(stage) + " " + percent.formatted(.number.precision(.fractionLength(0))) + "%"
            } else {
                detail = localized(stage)
            }
            let action = localized(creating ? "正在创建草稿" : "正在刷新草稿")
            owner.showStatus(action + " · " + detail, kind: .progress)
        }
        if name == "artworkReady" {
            if working { owner.showStatus(localized("正在显示草稿") + "…", kind: .progress) }
            pending = frame; deliver()
        }
        if name == "artworkFailed" {
            if case .loading = owner.mapPlaceholder.state { owner.showMapState(.unavailable("地图预览不可用")) }
            previewFailed(); owner.showError(AppFailure(payload: frame))
        }
    }

    func supplySnapshot(_ frame: JSON) {
        guard frame["documentEpoch"] as? Int == epoch, frame["artworkRevision"] as? Int == revision else { return }
        let target = revision, current = operation, scene = owner.previewIdentity
        owner.run { [self] in
            let artwork = try await owner.engine.request("getCityArtworkSnapshot", ["documentEpoch":epoch, "artworkRevision":target])
            guard owner.cityDraft === self, current == operation, target == revision,
                  owner.previewIdentity == scene, artwork["revision"] as? Int == target else { return }
            send("loadArtwork", ["artwork":artwork])
        }
    }

    func locateStyle() {
        let section = editor.selectedSection
        let detail = editor.selectedStyleDetail
        guard !["__basic__", "canvas", "watermark"].contains(section) else { return }
        styleGeneration += 1
        let generation = styleGeneration
        pendingCloseup = nil
        owner.run { [self] in
            var params: JSON = ["documentEpoch": epoch, "section": section]
            if section == "roads" { params["roadLevel"] = detail }
            if section == "labels" { params["labelType"] = detail ?? "road" }
            let result = try await owner.engine.request("getCityArtworkCloseup", params)
            guard owner.cityDraft === self, generation == styleGeneration else { return }
            if let bounds = result["bounds"] as? [Double] { pendingCloseup = bounds; presentCloseup() }
            else { owner.showStatus(localized("当前城市没有该分类的特写")) }
        }
    }

    private func presentCloseup() {
        guard presented > 0, presented == revision, let pendingCloseup else { return }
        self.pendingCloseup = nil
        send("locateArtwork", ["worldBounds": pendingCloseup])
    }

    func observationChanged(_ frame: JSON) {
        guard frame["documentEpoch"] as? Int == epoch, frame["artworkRevision"] as? Int == viewerRevision,
              frame["scale"] is Double else { return }
        observation = frame.filter { ["scale", "x", "y", "viewportWidth", "viewportHeight"].contains($0.key) }
    }

    func refreshCanvas() {
        guard !starting, !working else { return }
        restoreObservation = observation
        pending = nil; presented = 0
        styleGeneration += 1; pendingCloseup = nil
        starting = true; owner.updateCommands()
        owner.run { [self] in
            do {
                try await owner.flushFieldWrites()
                guard editor.flushInputs() else { previewFailed(); return }
                editor.previewTask?.cancel()
                try await updateTheme()
                try await request("refreshCityArtwork")
            }
            catch { previewFailed(); throw error }
        }
    }

    func previewFailed() {
        starting = false
        creating = false
        refreshing = false
        owner.updateCommands()
    }

    func acknowledge(_ frame: JSON) {
        guard frame["documentEpoch"] as? Int == epoch, frame["artworkRevision"] as? Int == revision else { return }
        let target = revision, current = operation
        owner.run { [self] in
            let result = try await owner.engine.request("artworkPresented", ["documentEpoch": epoch, "artworkRevision": target,
                "operationId": current, "performance": frame["performance"] ?? [:]])
            if result["accepted"] as? Bool == true, owner.cityDraft === self, target == revision, current == operation {
                presented = target
                if let restoreObservation {
                    self.restoreObservation = nil
                    send("setArtworkView", ["observation": restoreObservation])
                }
                let wasCreating = creating
                creating = false
                refreshing = false
                if case .loading = owner.mapPlaceholder.state { owner.showMapState(.ready) }
                starting = false; owner.updateCommands()
                presentCloseup()
                owner.showStatus(localized(wasCreating ? "草稿已创建" : "草稿已更新"), kind: .ready)
            }
        }
    }

    func configure() {
        guard !owner.draftSettingsOpen else { return }
        owner.draftSettingsOpen = true; owner.updateCommands()
        owner.run { [self] in
            defer { owner.draftSettingsOpen = false; owner.updateCommands() }
            let state = try await owner.engine.request("getCityArtworkState", ["documentEpoch":epoch])
            guard owner.cityDraft === self else { return }
            let settings = DraftSettings(owner: owner, config: object(state["config"]), custom: customResolution)
            guard var values = await settings.options(), owner.cityDraft === self else { return }
            guard editor.flushInputs() else { return }
            try await owner.flushFieldWrites()
            editor.previewTask?.cancel()
            try await updateTheme()
            styleGeneration += 1; pendingCloseup = nil
            customResolution = values.removeValue(forKey: "customResolution") as? Bool ?? false
            let latest = try await owner.engine.request("getCityArtworkState", ["documentEpoch": epoch])
            values["expectedRevision"] = latest["revision"]
            values["debounce"] = true
            try await request("updateCityArtwork", values)
        }
    }

    func export() {
        owner.showExport()
    }

    func save() { owner.run { _ = try await self.finish(leaving: false) } }
    func leave() { owner.run { _ = try await self.finish(leaving: true) } }

    func finish(leaving: Bool) async throws -> Bool {
        guard owner.cityDraft === self, exportID.isEmpty, !starting, !finishing,
              !owner.draftSettingsOpen, !editor.window.saving else { return false }
        owner.window.makeFirstResponder(nil)
        finishing = true; owner.updateCommands()
        defer { finishing = false; owner.updateCommands() }
        try await owner.flushFieldWrites()
        guard owner.cityDraft === self else { return false }
        let valid = editor.flushInputs()
        var shouldSave = !leaving
        if leaving && hasChanges {
            let alert = NSAlert()
            alert.messageText = localized("保存草稿修改？")
            alert.addButton(withTitle: localized("保存"))
            alert.addButton(withTitle: localized("放弃修改"))
            alert.addButton(withTitle: localized("继续编辑"))
            alert.buttons[0].isEnabled = valid
            switch await alert.beginSheetModal(for: owner.window) {
            case .alertFirstButtonReturn: shouldSave = true
            case .alertSecondButtonReturn: break
            default: return false
            }
        }
        if shouldSave && hasChanges {
            guard valid else { throw AppFailure(message: localized("请修正参数后重试预览。")) }
            if editor.window.dirty {
                if editor.draft["isBuiltin"] as? Bool == true {
                    let current = textValue(editor.data["name"])
                    let saved = textValue(object(editor.draft["savedData"])["name"])
                    let initial = current == saved ? textValue(editor.draft["suggestedName"]) : current
                    guard let name = editor.promptThemeName(initial) else { return false }
                    editor.change("name", name)
                }
                guard let commit = editor.commit(save: true, refreshArtwork: !leaving), await commit.value else { return false }
            } else {
                _ = try await owner.engine.request("commitCityArtwork", ["documentEpoch": epoch])
                try await owner.refreshSnapshot()
            }
            if !working || leaving { owner.showStatus(localized("已保存"), kind: .ready) }
        }
        guard leaving else { return true }
        editor.previewTask?.cancel()
        _ = try await owner.engine.request("leaveCityArtwork", ["documentEpoch": epoch])
        revision = viewerRevision
        send("leaveArtwork")
        editor.closed = true
        if owner.activeExport?.draft === self { owner.activeExport?.window.close() }
        owner.cityDraft = nil
        if working { owner.dismissStatus() }
        owner.rebuildBand()
        if owner.context == "transit" { try await owner.loadTransitSettings() }
        else { owner.restoreMapSidebar() }
        owner.buildInspector()
        owner.leftItem.isCollapsed = originalPanels.left
        owner.rightItem.isCollapsed = originalPanels.right
        owner.syncSidebarVisibility(); owner.syncInspectorVisibility()
        try await owner.refreshMapPreview()
        return true
    }

    func toolbar() -> [NSView] {
        func action(_ title: String, _ icon: String, showsTitle: Bool = true, _ perform: @escaping () -> Void) -> ActionButton {
            let button = ActionButton(title, perform)
            button.toolTip = localized(title)
            button.setAccessibilityLabel(localized(title))
            if let image = symbol(icon, size: 15) {
                button.image = image
                button.imagePosition = showsTitle ? .imageLeading : .imageOnly
            }
            return button
        }
        var tools: [NSView] = [
            action("退出草稿", "chevron.backward", leave),
            action("草稿设置", "slider.horizontal.3", configure),
            editor.libraryMenu()
        ]
        if owner.context == "transit" {
            tools.append(ActionButton("公交设置") { self.owner.showTransitSettings() })
            tools.append(ActionButton("草稿样式") { self.show() })
        }
        return tools + [
            action("分类特写", "viewfinder", showsTitle: false, locateStyle),
            action("撤销", "arrow.uturn.backward", showsTitle: false) { self.editor.restoreHistory(undo: true) },
            action("重做", "arrow.uturn.forward", showsTitle: false) { self.editor.restoreHistory(undo: false) },
            action("刷新草稿", "arrow.clockwise", refreshCanvas),
            action("保存主题", "square.and.arrow.down", save),
            action("导出", "square.and.arrow.up", export)
        ]
    }
}

extension InterfaceController {
    func chooseDraftWorkflow(_ workflow: String, direct: @escaping @MainActor () -> Void) {
        guard !draftSettingsOpen, !draftWorkflowOpen, window.attachedSheet == nil else { return }
        let generation = epoch, mode = context
        let canUseDraft = commandEnabled("创建地图草稿")
        draftWorkflowOpen = true
        run { [self] in
            defer { draftWorkflowOpen = false; updateCommands() }
            let request: JSON = ["documentEpoch": generation, "workflow": workflow, "canUseDraft": canUseDraft]
            let preference = try await engine.request("getMapWorkflowChoice", request)
            guard epoch == generation, context == mode, cityDraft == nil else { return }
            let useDraft: Bool
            if let saved = preference["useDraft"] as? Bool {
                useDraft = saved
            } else {
                let exporting = workflow == "export"
                let alert = NSAlert()
                alert.messageText = localized(exporting ? "选择导出方式" : "选择主题编辑器")
                alert.addButton(withTitle: localized(exporting ? "直接导出" : "交互式主题编辑器"))
                alert.addButton(withTitle: localized(exporting ? "编辑后导出" : "传统主题编辑器"))
                alert.addButton(withTitle: localized("取消"))
                alert.buttons[exporting ? 1 : 0].isEnabled = canUseDraft
                if !canUseDraft { alert.informativeText = localized("请先打开城市地图或公共交通地图，等待交互预览就绪。") }
                let remember = NSButton(checkboxWithTitle: localized("记住我的选择"), target: nil, action: nil)
                remember.sizeToFit()
                alert.accessoryView = remember
                let choice = await alert.beginSheetModal(for: window)
                guard choice == .alertFirstButtonReturn || choice == .alertSecondButtonReturn,
                      epoch == generation, context == mode else { return }
                useDraft = exporting ? choice == .alertSecondButtonReturn : choice == .alertFirstButtonReturn
                guard !useDraft || commandEnabled("创建地图草稿") else { return }
                if remember.state == .on {
                    var values = request; values["useDraft"] = useDraft
                    _ = try await engine.request("rememberMapWorkflowChoice", values)
                }
            }
            guard epoch == generation, context == mode else { return }
            if useDraft { beginDraft() } else { direct() }
        }
    }

    func beginDraft() {
        guard commandEnabled("创建地图草稿"), closeEditors() else { return }
        let generation = epoch, mode = context
        draftSettingsOpen = true; updateCommands()
        run { [self] in
            defer { draftSettingsOpen = false; updateCommands() }
            try await flushFieldWrites()
            let theme = try await engine.request("getThemeDraft")
            let profile = try await engine.request("getExportProfile", ["context":mode == "transit" ? "transit":"normal"])
            guard epoch == generation, context == mode, cityDraft == nil else { return }
            let output = object(profile["output"])
            var config: JSON = ["longEdge":4096, "elementScale":1,
                "rotation":output["rotation"] ?? 0, "poiDensity":profile["poiProfile"] ?? [:]]
            if let bounds = selectedBounds { config["region"] = ["bounds":bounds,"source":"selection"] }
            let settings = DraftSettings(owner: self, config: config, custom: false)
            guard let options = await settings.options(), epoch == generation, context == mode else { return }
            try await startCityDraft(theme: theme, options: options)
        }
    }

    func startCityDraft(theme: JSON, options: JSON) async throws {
        var options = options
        options["kind"] = context == "transit" ? "transit" : "normal"
        options["incremental"] = true
        let session = CityDraft(owner: self, theme: theme)
        session.customResolution = options.removeValue(forKey: "customResolution") as? Bool ?? false
        session.displayedTheme = try jsonString(session.editor.data)
        cityDraft = session
        session.creating = true
        showStatus(localized("正在创建草稿") + "…", kind: .progress)
        do { try await session.request("beginCityArtwork", options) }
        catch { cityDraft = nil; throw error }
        session.starting = false; session.show()
        if context == "transit" { try await loadTransitSettings() }
    }
}
