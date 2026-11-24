import AppKit
import CryptoKit

@MainActor final class RmpController {
    unowned let owner: InterfaceController
    let host = WebHost(channel: "cslRmp")
    var projectID = ""
    var revision = 0
    var dirty = false
    var state: JSON = [:]
    var tools: JSON = [:]
    var metadata: JSON = [:]
    var diagnostics: JSON = [:]
    var path: URL?
    var hasProject = false
    var busy = false { didSet { syncHost(); owner.updateCommands() } }
    var initialized = false
    var generationID = ""
    var generationEpoch = 0
    var generation: CheckedContinuation<JSON, Error>?
    private var generationCancelled = false
    private var applyingGeneration = false
    private var confirmingReplacement = false
    var pending: [String: CheckedContinuation<JSON, Error>] = [:]
    var placing: Bool { state["placing"] as? Bool == true }
    var webHasFocus: Bool {
        guard let responder = owner.window?.firstResponder as? NSView else { return false }
        return responder === host.view || responder.isDescendant(of: host.view)
    }
    var placementPending: Bool {
        let mode = textValue(state["mode"])
        return !object(state["insertion"]).isEmpty || ["station-", "line-", "misc-node-"].contains(where: mode.hasPrefix)
    }
    var placementHint: String {
        switch textValue(object(state["insertion"])["kind"]) {
        case "title": return localized("点击画布放置标题，然后在右侧输入内容。按 Esc 取消。")
        case "legend": return localized("点击画布放置当前线路与符号的图例。图例只生成一次，不自动更新。按 Esc 取消。")
        case "facility": return localized("点击画布放置所选设施图标。按 Esc 取消。")
        default:
            switch textValue(state["mode"]) {
            case "misc-node-text": return localized("添加备注或说明，放置后在右侧输入内容。")
            case "misc-node-virtual": return localized("添加非车站连接点，用于连接线路或组成区域边界；点标记不参与导出。")
            case "misc-node-fill": return localized("放置填色节点，再在右侧创建形状或连接闭合边界；不是点击已有区域自动填色。")
            default: return localized("在画布上继续绘制，按 Esc 取消。")
            }
        }
    }
    var canCancelGeneration: Bool { !generationID.isEmpty && !generationCancelled && !applyingGeneration }
    private var awaitingProjectID: String?

    init(owner: InterfaceController) {
        self.owner = owner
        host.event = { [weak self] frame in self?.receive(frame) }
        host.failed = { [weak self] error in self?.fail(error) }
    }
    func syncHost() {
        guard initialized else { return }
        host.send(["type": "hostState", "protocolVersion": 1, "projectId": projectID,
                   "enabled": hasProject && !busy && owner.pendingCityEditors == nil
                       && owner.context == "metro" && !host.view.isHidden])
    }
    func fail(_ error: Error) {
        let requests = pending; pending.removeAll()
        for continuation in requests.values { continuation.resume(throwing: error) }
        let task = generation; generation = nil; generationID = ""
        task?.resume(throwing: error)
        busy = false
        owner.showError(error)
    }
    func initialize() async throws {
        if initialized { return }
        guard let url = owner.viewerURL else { throw AppFailure(message: "地图服务未就绪") }
        if host.view.superview == nil {
            pin(host.view, in: owner.center)
            owner.center.addSubview(host.view, positioned: .below, relativeTo: owner.restoreSidebar)
        }
        projectID = ""
        host.load(url.deletingLastPathComponent().appendingPathComponent("rmp/embed.html"))
        initialized = true
        do {
            let result = try await request("getTools"); tools = object(result["tools"])
            try await setLanguage(); try await setAppearance()
            syncHost()
        } catch { initialized = false; throw error }
    }
    func request(_ type: String, _ params: JSON = [:]) async throws -> JSON {
        let id = UUID().uuidString
        var message = params
        message["type"] = type; message["protocolVersion"] = 1
        message["projectId"] = awaitingProjectID ?? projectID; message["requestId"] = id
        return try await withCheckedThrowingContinuation { continuation in
            register(id, continuation); host.send(message)
            DispatchQueue.main.asyncAfter(deadline: .now() + 120) { [weak self] in
                self?.pending.removeValue(forKey: id)?.resume(throwing: AppFailure(message: localized("地铁编辑器响应超时，请重试。")))
            }
        }
    }
    private func register(_ id: String, _ continuation: CheckedContinuation<JSON, Error>) { pending[id] = continuation }
    func setLanguage() async throws {
        guard initialized else { return }
        let language = textValue(owner.metadata["language"])
        let result = try await request("setLanguage", ["language": language == "zh-CN" ? "zh-Hans" : language == "zh-TW" ? "zh-Hant" : language])
        tools = object(result["tools"])
    }
    func setAppearance() async throws {
        guard initialized else { return }
        let dark = owner.root.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        _ = try await request("setTheme", ["theme": dark ? "dark" : "light", "highContrast": NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast])
    }
    func receive(_ frame: JSON) {
        guard frame["protocolVersion"] as? Int == 1 else { return }
        let type = textValue(frame["type"])
        if type == "ready" { return }
        let incoming = textValue(frame["projectId"])
        guard incoming == projectID || incoming == awaitingProjectID else { return }
        if incoming == projectID {
            if type == "changed" {
                revision = frame["revision"] as? Int ?? revision; dirty = true
                owner.window.isDocumentEdited = true
            }
            if type == "editorState" { state = object(frame["state"]); owner.updateCommands() }
            if type == "commandRequested" { command(textValue(frame["command"])) }
        }
        if let id = frame["requestId"] as? String, let continuation = pending.removeValue(forKey: id) {
            if type == "error" { continuation.resume(throwing: AppFailure(payload: frame)) }
            else { continuation.resume(returning: frame) }
        }
    }
    func show() {
        host.view.isHidden = false; owner.viewer.view.isHidden = true
        owner.configureMetroWorkspace(true); syncHost(); owner.rebuildBand()
    }
    func hide() {
        owner.rmpFacilitiesPopover?.close()
        host.view.isHidden = true; syncHost(); owner.configureMetroWorkspace(false)
        owner.window.isDocumentEdited = false
    }
    func focusEditor() async throws {
        guard initialized, hasProject, !busy, !host.view.isHidden else { return }
        _ = try await request("focusEditor")
    }
    func generate() async throws {
        guard owner.context == "metro", !busy, !placing else { return }
        guard owner.canGenerateMetro() else { throw AppFailure(message: localized("请选择至少一种交通类型和一条可用线路。")) }
        guard try await permitReplacement(regenerating: true) else { return }
        guard owner.context == "metro", owner.canGenerateMetro() else { return }
        let settingsFingerprint = owner.metroInputFingerprint(), documentEpoch = owner.epoch
        busy = true
        defer {
            generationID = ""; generation = nil
            generationCancelled = false; applyingGeneration = false
            busy = false
        }
        try await initialize(); show()
        let taskID = UUID().uuidString
        generationID = taskID; generationEpoch = documentEpoch
        owner.refreshMetroSettingsStatus()
        owner.showStatus(localized("正在生成地铁项目…"), kind: .progress)
        do {
            let draft: JSON = try await withCheckedThrowingContinuation { continuation in
                generation = continuation
                DispatchQueue.main.asyncAfter(deadline: .now() + 180) { [weak self] in
                    guard let self, self.generationID == taskID, let pending = self.generation else { return }
                    self.generation = nil
                    pending.resume(throwing: AppFailure(message: "地铁项目生成超时"))
                    self.owner.run { _ = try await self.owner.engine.request("cancel", ["rmpRequestId": taskID]) }
                }
                owner.run { [self] in
                    do {
                        let result = try await owner.engine.request("prepareRmpProject", ["requestId": taskID, "documentEpoch": documentEpoch])
                        if result["started"] as? Bool != true { throw AppFailure(message: "未能开始生成地铁项目") }
                        if generationID == taskID, generationCancelled {
                            _ = try await owner.engine.request("cancel", ["rmpRequestId": taskID])
                        }
                    } catch {
                        guard generationID == taskID else { return }
                        let pending = generation; generation = nil; pending?.resume(throwing: error)
                    }
                }
            }
            guard generationID == taskID, !generationCancelled, owner.epoch == documentEpoch,
                  owner.context == "metro" else { throw CancellationError() }
            applyingGeneration = true
            owner.refreshStatusVisibility()
            var provenance = object(draft["metadata"])
            provenance["nativeSettingsFingerprint"] = settingsFingerprint
            provenance["diagnostics"] = object(draft["diagnostics"])
            try await load(object(draft["project"]), path: nil, provenance: provenance, settingsFingerprint: settingsFingerprint)
            owner.showStatus(localized("地铁项目已生成，请保存项目"), kind: .ready)
        } catch is CancellationError {
            owner.showStatus(localized(hasProject ? "生成已取消，原工程已保留" : "任务已取消"), kind: .ready)
            throw CancellationError()
        }
    }
    func cancelGeneration() -> Bool {
        guard !generationID.isEmpty else { return false }
        guard canCancelGeneration else { return true }
        generationCancelled = true
        let taskID = generationID
        owner.showStatus(localized("正在取消…"), kind: .progress)
        owner.run { [self] in
            do { _ = try await owner.engine.request("cancel", ["rmpRequestId": taskID]) }
            catch {
                guard generationID == taskID, let pending = generation else { return }
                generation = nil; pending.resume(throwing: error)
            }
        }
        return true
    }
    func engineEvent(_ name: String, _ data: JSON) {
        guard !generationID.isEmpty, textValue(data["requestId"]) == generationID else { return }
        guard (data["documentEpoch"] as? Int ?? generationEpoch) == generationEpoch else { return }
        guard ["rmpProjectReady", "rmpProjectError", "error"].contains(name), let pending = generation else { return }
        generation = nil
        if generationCancelled || data["cancelled"] as? Bool == true || ["cancelled", "stale"].contains(textValue(data["category"])) {
            pending.resume(throwing: CancellationError())
        } else if name == "rmpProjectReady" { pending.resume(returning: object(data["draft"])) }
        else { pending.resume(throwing: AppFailure(payload: data)) }
    }
    func load(_ project: JSON, path: URL?, provenance: JSON = [:], settingsFingerprint: String? = nil) async throws {
        let incoming = UUID().uuidString
        awaitingProjectID = incoming
        defer { awaitingProjectID = nil }
        let loaded = try await request("loadProject", ["project": project, "fit": path == nil])
        projectID = incoming; self.path = path; revision = loaded["revision"] as? Int ?? 0
        hasProject = true; dirty = path == nil; state = object(loaded["state"])
        metadata = provenance; diagnostics = object(provenance["diagnostics"])
        owner.metroAppliedFingerprint = settingsFingerprint
        show()
    }
    func command(_ type: String, _ params: JSON = [:]) {
        owner.run { [self] in
            guard owner.context == "metro", !busy, !placing, owner.pendingCityEditors == nil else { return }
            switch type {
            case "open": try await open()
            case "save": _ = try await save(asNew: false)
            case "saveAs": _ = try await save(asNew: true)
            case "export": owner.showExport()
            case "pickFacility": owner.runRmpTool(.facilities)
            case "beginInsert", "setMode":
                guard hasProject else { return }
                owner.window.makeFirstResponder(host.view)
                try await focusEditor()
                _ = try await request(type, params)
            case "undo", "redo", "focusedHistory", "cancelInsert", "fit", "rotateMap", "setViewOptions":
                guard hasProject else { return }; _ = try await request(type, params)
            default: break
            }
        }
    }
    func permitReplacement(regenerating: Bool = false) async throws -> Bool {
        guard !busy, !placing, !confirmingReplacement else { return false }
        guard hasProject else { return true }
        confirmingReplacement = true
        defer { confirmingReplacement = false }
        try await focusEditor()
        if !dirty && !regenerating { return true }
        let alert = NSAlert()
        alert.messageText = localized(regenerating ? "重新生成将替换当前地铁工程" : "保存地铁项目的修改？")
        if regenerating {
            alert.informativeText = localized(dirty
                ? "当前工程有未保存的编辑。取消或生成失败时保留原工程。"
                : "重新生成会在编辑器中打开新草稿。已保存的工程文件仍可重新打开。")
        }
        alert.addButton(withTitle: localized(regenerating ? (dirty ? "保存并重新生成" : "重新生成示意图") : "保存"))
        alert.addButton(withTitle: localized("取消"))
        alert.buttons[1].keyEquivalent = "\u{1b}"
        if dirty { alert.addButton(withTitle: localized(regenerating ? "放弃修改并重新生成" : "不保存")) }
        switch alert.runModal() {
        case .alertFirstButtonReturn: return !dirty ? true : try await save(asNew: false)
        case .alertThirdButtonReturn: return true
        default: return false
        }
    }
    func open() async throws {
        guard try await permitReplacement() else { return }
        let chooser = NSOpenPanel(); chooser.allowedFileTypes = ["json"]
        guard chooser.runModal() == .OK, let url = chooser.url else { return }
        let bytes = try Data(contentsOf: url)
        let project = object(try JSONSerialization.jsonObject(with: bytes))
        var provenance: JSON = [:]
        let metadataURL = URL(fileURLWithPath: url.path + ".cmm-meta.json")
        // Open the project even if its sidecar is damaged.
        if let data = try? Data(contentsOf: metadataURL), let value = try? JSONSerialization.jsonObject(with: data) {
            let candidate = object(value)
            if textValue(candidate["projectHash"]) == Self.hash(bytes) { provenance = candidate }
        }
        busy = true; defer { busy = false }
        try await initialize()
        try await load(project, path: url, provenance: provenance,
                       settingsFingerprint: provenance["nativeSettingsFingerprint"] as? String ?? owner.metroInputFingerprint())
        owner.showStatus(localized("已打开：") + url.lastPathComponent, kind: .ready)
    }
    static func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02X", $0) }.joined() }
    func save(asNew: Bool) async throws -> Bool {
        guard hasProject, !busy, !placing else { return false }
        try await focusEditor()
        var destination = path
        if asNew || destination == nil {
            let chooser = NSSavePanel(); chooser.allowedFileTypes = ["json"]
            chooser.nameFieldStringValue = ExportNaming.filename(city: exportCityName, context: "metro", format: "RMP")
            guard chooser.runModal() == .OK else { return false }; destination = chooser.url
        }
        guard let destination = destination else { return false }
        return try await save(to: destination)
    }
    func save(to destination: URL) async throws -> Bool {
        guard hasProject, !busy, !placing else { return false }
        try await focusEditor()
        busy = true; defer { busy = false }
        let result = try await request("saveProject")
        let bytes = try JSONSerialization.data(withJSONObject: object(result["project"]), options: [.sortedKeys, .withoutEscapingSlashes])
        let savedRevision = result["revision"] as? Int ?? revision
        var provenance = metadata; provenance["projectHash"] = Self.hash(bytes); provenance["projectRevision"] = savedRevision
        let metadataBytes = try JSONSerialization.data(withJSONObject: provenance, options: [.sortedKeys])
        let metadataURL = URL(fileURLWithPath: destination.path + ".cmm-meta.json"), fm = FileManager.default
        let oldProject = fm.fileExists(atPath: destination.path) ? try Data(contentsOf: destination) : nil
        let oldMetadata = fm.fileExists(atPath: metadataURL.path) ? try Data(contentsOf: metadataURL) : nil
        if let previous = oldProject { try previous.write(to: URL(fileURLWithPath: destination.path + ".previous"), options: .atomic) }
        if let previous = oldMetadata { try previous.write(to: URL(fileURLWithPath: metadataURL.path + ".previous"), options: .atomic) }
        do {
            try bytes.write(to: destination, options: .atomic)
            try metadataBytes.write(to: metadataURL, options: .atomic)
        } catch {
            if let previous = oldProject { try previous.write(to: destination, options: .atomic) }
            else if fm.fileExists(atPath: destination.path) { try fm.removeItem(at: destination) }
            if let previous = oldMetadata { try previous.write(to: metadataURL, options: .atomic) }
            else if fm.fileExists(atPath: metadataURL.path) { try fm.removeItem(at: metadataURL) }
            throw error
        }
        path = destination; metadata = provenance; dirty = revision != savedRevision
        owner.window.isDocumentEdited = dirty
        owner.showStatus(localized("已保存：") + destination.lastPathComponent, kind: .ready)
        return !dirty
    }
    var exportCityName: String {
        if let city = metadata["cityName"] as? String, !city.isEmpty { return city }
        if let path { return path.lastPathComponent.replacingOccurrences(of: #"(?i)\.rmp\.json$|\.[^.]+$"#, with: "", options: .regularExpression) }
        return textValue(object(owner.snapshot["cityInfo"])["name"])
    }
    func export(_ url: URL, format: String, options: JSON) async throws {
        guard !busy, !placing, hasProject else { throw AppFailure(message: "地铁项目尚未就绪") }
        try await focusEditor()
        busy = true; defer { busy = false }
        var params = options; params["format"] = format
        let result = try await request("export", params)
        let bytes: Data
        if format == "png" {
            guard let decoded = Data(base64Encoded: textValue(result["data"])) else { throw AppFailure(message: "地铁 PNG 返回数据无效") }
            bytes = decoded
        } else { bytes = Data(textValue(result["data"]).utf8) }
        try bytes.write(to: url, options: .atomic)
    }
}

extension InterfaceController {
    func rebuildRmpBand() {
        let actions = [
            ("返回地图", "arrow.left"), ("保存项目", "square.and.arrow.down"), ("导出…", "square.and.arrow.up")
        ]
        let buttons = actions.map { title, icon in
            let button = ActionButton(title) { [weak self] in self?.execute(title) }
            button.image = symbol(icon, size: 14); button.imagePosition = .imageLeading
            button.font = .systemFont(ofSize: 13); button.toolTip = button.title
            return button
        }
        bandContent(buttons)
    }
}
