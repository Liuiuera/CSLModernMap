import AppKit

@MainActor final class ThemeEditor: NSObject, NSSearchFieldDelegate {
    unowned let owner: InterfaceController
    let window = EditorWindow("编辑主题", width: 1060, height: 760)
    var draft: JSON
    let form = FormView()
    let preview = NSImageView()
    let placeholder = PreviewPlaceholder()
    let section = NSPopUpButton(frame: .zero, pullsDown: false)
    let search = NSSearchField()
    let automatic = NSButton(checkboxWithTitle: localized("自动预览"), target: nil, action: nil)
    let previewStatus = NSTextField(wrappingLabelWithString: "")
    let operationStatus = NSTextField(wrappingLabelWithString: "")
    let activity = NSProgressIndicator()
    let previewButton = ActionButton("预览") {}
    let retryButton = ActionButton("重试") {}
    let applyButton = ActionButton("应用") {}
    let saveButton = ActionButton("保存") {}
    let exportButton = ActionButton("另存为…") {}
    let restoreButton = ActionButton("恢复本分类默认设置") {}
    let restoreAllButton = ActionButton("恢复全部默认设置") {}
    let undoButton = ActionButton("撤销") {}
    let redoButton = ActionButton("重做") {}
    var sections: [String] = []
    var selectedSection = ""
    var selectedStyleDetail: String?
    var renderedSection = ""
    var scrollPositions: [String: CGFloat] = [:]
    var previewID = ""
    var previewTask: Task<Void, Never>?
    var imageTask: Task<Void, Never>?
    var invalidated = false
    var closed = false
    var flushing = false
    var inputDirty = false
    var inputError = false
    var rebuilding = false
    var savedData: String
    var undoStack: [JSON] = []
    var redoStack: [JSON] = []
    var extraControls: [(NSControl, String)] = []
    var rampBands: [(ThemeRampBand, String)] = []

    init(owner: InterfaceController, draft: JSON) {
        self.owner = owner; self.draft = draft
        savedData = (try? jsonString(draft["savedData"] ?? draft["data"] ?? [:])) ?? ""
        super.init()
    }
    var data: JSON { object(draft["data"]) }
    var documentCurrent: Bool {
        !closed && !invalidated && owner.epoch == (draft["documentEpoch"] as? Int ?? -1) &&
            (owner.snapshot["theme"] == nil || textValue(owner.snapshot["theme"]) == textValue(draft["theme"]))
    }
    var editable: Bool {
        documentCurrent && owner.engineReady && !owner.busy && !owner.fieldWriting &&
            owner.fieldQueue.isEmpty && !window.saving
    }
    func show() {
        window.minSize = NSSize(width: 940, height: 580)
        sections = draft["sections"] as? [String] ?? []
        for key in sections {
            section.menu?.addItem(NSMenuItem(title: sectionTitle(key), action: nil, keyEquivalent: ""))
        }
        selectedSection = sections.first ?? ""
        section.target = self; section.action = #selector(selectSection)
        search.placeholderString = localized("搜索当前分类参数"); search.delegate = self
        automatic.state = .on; automatic.target = self; automatic.action = #selector(toggleAutomatic)
        previewButton.perform = { [weak self] in self?.render() }
        retryButton.perform = { [weak self] in self?.render() }
        applyButton.perform = { [weak self] in self?.commit(save: false) }
        saveButton.perform = { [weak self] in self?.commit(save: true) }
        exportButton.perform = { [weak self] in self?.saveAs() }
        restoreButton.perform = { [weak self] in self?.restore(all: false) }
        restoreAllButton.perform = { [weak self] in self?.restore(all: true) }
        undoButton.perform = { [weak self] in self?.restoreHistory(undo: true) }
        redoButton.perform = { [weak self] in self?.restoreHistory(undo: false) }
        applyButton.toolTip = localized("仅应用到当前地图，不保存主题设置。")
        saveButton.toolTip = localized("保存主题设置并应用到当前地图。")
        exportButton.toolTip = localized("导出 YAML 文件，不改变当前地图或已保存设置。")
        let navigation = column([section, search, row([undoButton, redoButton, restoreButton], gap: 6)], gap: 8)
        for control in [section, search] as [NSView] { control.widthAnchor.constraint(equalTo: navigation.widthAnchor).isActive = true }
        let left = column([navigation, form], gap: 10)
        for view in [navigation, form] { view.widthAnchor.constraint(equalTo: left.widthAnchor).isActive = true }
        form.setContentHuggingPriority(.defaultLow, for: .vertical)
        let previewActions = row([label("主题预览", 14, .semibold), automatic, previewButton, retryButton], gap: 8)
        preview.imageScaling = .scaleProportionallyUpOrDown
        preview.setContentHuggingPriority(.defaultLow, for: .vertical)
        preview.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        activity.style = .spinning; activity.controlSize = .small; activity.isDisplayedWhenStopped = false
        previewStatus.font = .systemFont(ofSize: 12); previewStatus.textColor = .secondaryLabelColor
        let progress = row([activity, previewStatus], gap: 8)
        let hint = NSTextField(wrappingLabelWithString: localized("预览只显示草稿效果，不会应用或保存。失败时保留上次可用预览。"))
        hint.font = .systemFont(ofSize: 11); hint.textColor = .secondaryLabelColor
        let canvas = NSView()
        pin(preview, in: canvas)
        pin(placeholder, in: canvas)
        let right = column([previewActions, canvas, progress, hint], gap: 10)
        for view in [previewActions, canvas, progress, hint] { view.widthAnchor.constraint(equalTo: right.widthAnchor).isActive = true }
        previewStatus.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        let body = row([left, right], gap: 18)
        NSLayoutConstraint.activate([left.widthAnchor.constraint(equalToConstant: 410),
            left.heightAnchor.constraint(equalTo: body.heightAnchor), right.heightAnchor.constraint(equalTo: body.heightAnchor),
            preview.heightAnchor.constraint(greaterThanOrEqualToConstant: 180)])
        let close = ActionButton("关闭") { [weak self] in self?.window.performClose(nil) }
        close.keyEquivalent = "\u{1b}"
        let actions = row([libraryMenu(), restoreAllButton, NSView(), close, exportButton, applyButton, saveButton], gap: 10)
        saveButton.bezelColor = .controlAccentColor
        operationStatus.font = .systemFont(ofSize: 12)
        let divider = rule()
        let layout = column([body, operationStatus, divider, actions], gap: 12)
        for view in [body, operationStatus, divider, actions] { view.widthAnchor.constraint(equalTo: layout.widthAnchor).isActive = true }
        let host = window.surface; pin(layout, in: host, inset: 18)
        window.closeDecision = { [weak self] in self?.prepareClose() ?? true }
        window.onClose = { [weak self] in
            guard let self = self else { return }
            self.closed = true; self.cancelPreview()
            self.owner.panels.removeAll { $0 === self.window }
            if self.owner.themeEditor === self { self.owner.themeEditor = nil }
        }
        rebuild(); owner.panels.append(window); window.center(); window.makeKeyAndOrderFront(nil)
        schedulePreview(immediate: true)
    }
    func sectionTitle(_ key: String) -> String {
        localized(key == "__basic__" ? "基本信息" : textValue(object(draft["sectionNames"])[key] ?? NativePresentation.identifier(key)))
    }
    @objc func selectSection() {
        guard sections.indices.contains(section.indexOfSelectedItem) else { return }
        guard flushInputs() else { section.selectItem(at: sections.firstIndex(of: selectedSection) ?? 0); return }
        selectedSection = sections[section.indexOfSelectedItem]
        rebuild(); schedulePreview()
    }
    func controlTextDidChange(_ obj: Notification) {
        guard flushInputs() else { return }
        rebuild()
    }
    @objc func toggleAutomatic() {
        if automatic.state == .on { schedulePreview() }
        else { cancelPreview(); setPreviewStatus("自动预览已暂停，可点击预览。") }
    }
    func setPreviewStatus(_ text: String, working: Bool = false, failed: Bool = false) {
        if preview.image == nil {
            placeholder.show(working ? .loading(text) : (failed ? .unavailable(text) : .empty(text)))
            previewStatus.stringValue = ""
            activity.stopAnimation(nil)
        } else {
            placeholder.show(.ready)
            previewStatus.stringValue = localized(text)
            if working { activity.startAnimation(nil) } else { activity.stopAnimation(nil) }
        }
        retryButton.isHidden = !failed
    }
    func cancelPreview() {
        previewTask?.cancel(); previewTask = nil
        imageTask?.cancel(); imageTask = nil
        activity.stopAnimation(nil)
        let requestID = previewID; previewID = ""
        guard !requestID.isEmpty else { return }
        Task { [owner] in
            do { _ = try await owner.engine.request("cancelThemePreview", ["requestId": requestID]) }
            catch { FileHandle.standardError.write(Data(("Theme preview cleanup: " + error.localizedDescription + "\n").utf8)) }
        }
    }
    func render() { schedulePreview(immediate: true, manual: true) }
    func schedulePreview(immediate: Bool = false, manual: Bool = false) {
        guard !flushing, !rebuilding, documentCurrent, !window.saving else { return }
        if let session = owner.cityDraft, session.editor === self {
            previewTask?.cancel()
            previewTask = Task { [weak self] in
                do {
                    if !immediate { try await Task.sleep(nanoseconds: 150_000_000) }
                    guard let self = self else { return }
                    try await self.owner.flushFieldWrites()
                    try Task.checkCancellation()
                    guard self.owner.cityDraft === session, !session.finishing, self.flushInputs() else { return }
                    try await session.updateTheme()
                } catch is CancellationError {} catch { self?.owner.showError(error) }
            }
            return
        }
        cancelPreview()
        guard manual || automatic.state == .on else { setPreviewStatus("草稿已改变，点击预览以更新。"); return }
        setPreviewStatus("等待预览…", working: true)
        previewTask = Task { [weak self] in
            guard let self = self else { return }
            do {
                if !immediate { try await Task.sleep(nanoseconds: 350_000_000) }
                try Task.checkCancellation()
                guard self.documentCurrent else { return }
                guard self.editable else { self.setPreviewStatus("请等待当前操作完成后再预览。"); return }
                guard self.flushInputs() else { self.setPreviewStatus("请修正参数后重试预览。", failed: true); return }
                let requestID = UUID().uuidString; self.previewID = requestID
                self.setPreviewStatus("正在渲染预览…", working: true)
                let result = try await self.owner.engine.request("renderThemePreview", ["data": self.data,
                    "documentEpoch": self.draft["documentEpoch"] ?? -1, "requestId": requestID,
                    "scene": object(self.draft["scenes"])[self.selectedSection] ?? self.selectedSection, "size": 640])
                guard !Task.isCancelled, self.documentCurrent, self.previewID == requestID else { return }
                if result["started"] as? Bool != true {
                    self.setPreviewStatus("操作未完成", failed: true)
                    self.owner.showError(AppFailure(payload: result))
                }
            } catch is CancellationError {} catch {
                guard !Task.isCancelled, self.documentCurrent else { return }
                self.setPreviewStatus("操作未完成", failed: true)
                self.owner.showError(error)
            }
        }
    }
    func event(_ name: String, _ payload: JSON) {
        guard documentCurrent, !previewID.isEmpty, textValue(payload["requestId"]) == previewID else { return }
        if name == "themePreviewFailed" {
            imageTask?.cancel()
            setPreviewStatus("操作未完成", failed: true)
            owner.showError(AppFailure(payload: payload))
        } else if name == "themePreviewReady" {
            let requestID = previewID
            imageTask?.cancel(); setPreviewStatus("正在载入预览…", working: true)
            imageTask = Task { [weak self] in
                guard let self = self else { return }
                do {
                    guard let url = URL(string: textValue(payload["url"])), url.isFileURL else { throw AppFailure(message: localized("预览图片地址无效。")) }
                    let bytes = try await Task.detached(priority: .utility) { try Data(contentsOf: url) }.value
                    try Task.checkCancellation()
                    guard self.documentCurrent, self.previewID == requestID else { return }
                    guard let bitmap = NSBitmapImageRep(data: bytes), let image = bitmap.cgImage else {
                        throw AppFailure(message: localized("预览图片无法读取，请重试。"))
                    }
                    self.preview.image = NSImage(cgImage: image, size: NSSize(width: bitmap.pixelsWide, height: bitmap.pixelsHigh))
                    self.setPreviewStatus("预览已完成")
                } catch is CancellationError {} catch {
                    guard !Task.isCancelled, self.documentCurrent, self.previewID == requestID else { return }
                    self.setPreviewStatus("操作未完成", failed: true)
                    self.owner.showError(error)
                }
            }
        }
    }
    func refreshAvailability() {
        guard !closed else { return }
        if !documentCurrent && !invalidated && !window.saving {
            invalidated = true; cancelPreview()
            setPreviewStatus("城市或主题已切换，请重新打开主题编辑器。")
        }
        if documentCurrent && !owner.engineReady {
            cancelPreview(); setPreviewStatus("引擎未就绪，请稍后重试。")
        }
        let enabled = editable
        for control in [section, search, automatic, previewButton, retryButton, applyButton, exportButton, restoreButton, restoreAllButton] as [NSControl] { control.isEnabled = enabled }
        saveButton.isEnabled = enabled && window.dirty
        undoButton.isEnabled = enabled && !undoStack.isEmpty; redoButton.isEnabled = enabled && !redoStack.isEmpty
        for field in form.fields { field.refresh(enabled: enabled, synchronize: false) }
        for (control, key) in extraControls { control.isEnabled = enabled && conditionAllows(key) }
    }
    func updateDirty() {
        window.dirty = inputDirty || (try? jsonString(data)) != savedData
        refreshAvailability()
        if owner.cityDraft?.editor === self { owner.refreshBandActions() }
    }
    func prepareClose() -> Bool {
        let valid = flushInputs()
        if !window.dirty { return true }
        let alert = NSAlert(); alert.messageText = localized("保存主题修改？")
        alert.informativeText = localized("预览不会保存。已应用但未保存的修改只影响当前会话。")
        alert.addButton(withTitle: localized("保存")); alert.addButton(withTitle: localized("放弃修改")); alert.addButton(withTitle: localized("继续编辑"))
        alert.buttons[0].isEnabled = valid && editable
        switch alert.runModal() {
        case .alertFirstButtonReturn: commit(save: true, closeAfter: true); return false
        case .alertSecondButtonReturn: return true
        default: return false
        }
    }
    @discardableResult func commit(save: Bool, closeAfter: Bool = false, refreshArtwork: Bool = true) -> Task<Bool, Never>? {
        guard editable, flushInputs() else { return nil }
        cancelPreview(); setPreviewStatus("主题操作期间暂停预览。")
        window.saving = true; refreshAvailability()
        let payload = data
        var params: JSON = ["data": payload, "theme": draft["theme"] ?? "", "documentEpoch": draft["documentEpoch"] ?? -1,
            "settingsRevision": draft["settingsRevision"] ?? "", "persist": save]
        let embedded = owner.cityDraft?.editor === self
        for key in ["baseTheme","importToken"] { params[key] = draft[key] }
        params["new"] = draft["isNew"] ?? false
        return Task { [self] in
            defer { window.saving = false; refreshAvailability() }
            do {
                let result = try await owner.engine.request(embedded ? "commitCityArtwork" : "commitThemeDraft",
                    embedded ? ["documentEpoch": params["documentEpoch"] ?? -1, "themeDraft": params] : params)
                guard !closed, !invalidated, owner.epoch == params["documentEpoch"] as? Int else { return false }
                guard result["applied"] as? Bool == true else { throw AppFailure(message: localized("主题未能应用，请重试。")) }
                let updatedDraft = object(result["draft"])
                if !updatedDraft.isEmpty {
                    draft = updatedDraft
                    owner.snapshot["theme"] = result["theme"]
                } else { draft["settingsRevision"] = result["settingsRevision"] }
                if save {
                    guard result["saved"] as? Bool == true else { throw AppFailure(message: localized("主题未能保存，请重试。")) }
                    savedData = try jsonString(draft["savedData"] ?? payload)
                }
                inputDirty = false; updateDirty()
                operationStatus.stringValue = localized(save ? "主题已保存并应用。" : "已应用到当前地图，尚未保存。")
                do {
                    try await owner.refreshSnapshot()
                    guard documentCurrent else { return false }
                    if let session = owner.cityDraft, session.editor === self {
                        session.displayedTheme = ""
                        if refreshArtwork { try await session.updateTheme() }
                        return true
                    }
                    _ = try await owner.engine.request("refreshPreview")
                    guard documentCurrent else { return false }
                    try await owner.applySceneState()
                } catch {
                    owner.showError(error)
                    return true
                }
                if closeAfter { window.close() }
                else { window.saving = false; schedulePreview(immediate: true) }
                return true
            } catch {
                guard documentCurrent else { return false }
                operationStatus.stringValue = localized("操作未完成")
                owner.showError(error)
                setPreviewStatus("草稿已保留，可修改后重试。")
                return false
            }
        }
    }
    func saveAs() {
        guard editable, flushInputs() else { return }
        let panel = NSSavePanel(); panel.allowedFileTypes = ["yaml", "yml"]
        panel.nameFieldStringValue = textValue(draft["theme"]) + ".yaml"
        panel.beginSheetModal(for: owner.cityDraft?.editor === self ? owner.window : window) { [weak self] result in
            guard result == .OK, let self = self, let url = panel.url, self.editable, self.flushInputs() else { return }
            self.window.saving = true; self.refreshAvailability()
            let payload = self.data
            Task { [self] in
                defer { self.window.saving = false; self.refreshAvailability() }
                do {
                    let result = try await self.owner.engine.request("exportThemeDraft", ["data": payload, "path": url.path, "documentEpoch": self.draft["documentEpoch"] ?? -1])
                    guard self.documentCurrent else { return }
                    guard result["exported"] as? Bool == true else { throw AppFailure(message: localized("主题未能导出，请重试。")) }
                    self.operationStatus.stringValue = localized("主题文件已导出；当前地图与保存设置未改变。")
                } catch {
                    guard self.documentCurrent else { return }
                    self.operationStatus.stringValue = localized("操作未完成")
                    self.owner.showError(error)
                }
            }
        }
    }
}
