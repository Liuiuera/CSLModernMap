import AppKit

let layerKeys = ["卫星影像":"satellite", "地形":"terrain", "水域":"water", "道路":"road", "建筑":"building", "铁路":"railway", "建成区":"built_area", "区域上色":"poi_area", "路况":"traffic", "公共交通":"metro", "行政区划":"district", "功能区划":"zone", "规划网格":"land_use", "PO对象":"procedural_objects", "水印":"watermark"]
    .merging(Dictionary(uniqueKeysWithValues: MapSymbolCategory.allCases.map { ($0.title, $0.rawValue) })) { current, _ in current }

extension InterfaceController {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenus(); buildWindow(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        rmpKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self else { return event }
            return self.routeMetroKey(event)
        }
        engine.event = { [weak self] name, payload in self?.engineEvent(name, payload) }
        engine.failed = { [weak self] error in
            guard let self = self else { return }
            self.engineReady = false; self.engineBusy = false; self.pendingCityEditors = nil
            self.fieldQueue.removeAll(); self.toolTasks.removeAll()
            self.previewLayerWrites.removeAll()
            self.dismissStatus()
            self.showMapState(.unavailable("地图预览不可用"))
            self.rmp.fail(error)
            self.activeExport?.failed(error)
            self.updateCommands()
        }
        viewer.event = { [weak self] frame in self?.viewerEvent(frame) }
        viewer.failed = { [weak self] error in
            guard let self = self else { return }
            self.cityDraft?.previewFailed()
            if self.staticMode { self.showError(error) }
            else { self.showPreviewError(error, forceUnavailable: true) }
        }
        let args = CommandLine.arguments
        #if CMM_INTEGRATION_CHECKS
        smoke = NativeSmoke(owner: self, arguments: args)
        #endif
        run { [self] in
            try engine.start()
            let hello = try await engine.request("hello", ["protocolVersion": 1])
            guard hello["protocolVersion"] as? Int == 1 else { throw AppFailure(message: "引擎协议不兼容") }
            metadata = try await engine.request("getUiMetadata")
            engineReady = true; try await refreshSnapshot(); applyLanguage()
            diagnosticsPanel?.refreshPerformance()
            do { try await startInteractivePreview(); showStatus(localized("请打开城市")) }
            catch { showPreviewError(error, forceUnavailable: true) }
            #if CMM_INTEGRATION_CHECKS
            if let smoke { smoke.start(); return }
            #endif
            if let i = args.firstIndex(of: "--open"), args.indices.contains(i + 1) {
                try await openCity(URL(fileURLWithPath: args[i + 1]))
            } else if args.dropFirst().first == "gui", args.count > 2 {
                try await openCity(URL(fileURLWithPath: args[2]))
            }
        }
    }
    func run(_ action: @escaping @MainActor () async throws -> Void) {
        Task { @MainActor in do { try await action() } catch is CancellationError {} catch { showError(error) } }
    }
    func showPreviewError(_ error: Error, forceUnavailable: Bool = false) {
        let command = "重新启动交互预览"
        let wasLoading: Bool
        if case .loading = mapPlaceholder.state { wasLoading = true } else { wasLoading = false }
        let canvasUnavailable = !staticMode && (forceUnavailable || wasLoading)
        if canvasUnavailable { pendingQuality = nil }
        let canRetry = commandEnabled(command)
        if canvasUnavailable {
            let message = "地图预览不可用"
            showMapState(engineReady && context != "metro" && cityDraft == nil
                         ? .previewFailed(message) : .unavailable(message))
            mapPlaceholder.enableRetry(canRetry)
        }
        let retry: (() -> Void)? = canRetry ? { [weak self] in self?.execute(command) } : nil
        showError(error, retry: retry)
    }
    func showError(_ error: Error, retry: (() -> Void)? = nil) {
        let details = (error as? AppFailure)?.details ?? error.localizedDescription
        FileHandle.standardError.write(Data((details + "\n").utf8))
        lastFailureDetails = details
        recordDiagnosticError(details)
        showStatus(localized("操作未完成"))
        #if CMM_INTEGRATION_CHECKS
        if let smoke { smoke.fail(error); return }
        #endif
        let parent = NSApp.keyWindow ?? window!
        guard parent.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = localized("操作未完成")
        alert.alertStyle = .warning
        alert.addButton(withTitle: localized(retry == nil ? "关闭" : "重试"))
        alert.addButton(withTitle: localized("查看报错"))
        if retry != nil { alert.addButton(withTitle: localized("关闭")).keyEquivalent = "\u{1b}" }
        alert.beginSheetModal(for: parent) { [weak self] response in
            if response == .alertFirstButtonReturn { retry?() }
            else if response == .alertSecondButtonReturn { self?.showTextWindow("报错详情", details) }
        }
    }
    func refreshSnapshot() async throws {
        let state = try await engine.request("getSnapshot")
        applySnapshot(state)
    }
    func applySnapshot(_ state: JSON) {
        guard (state["documentEpoch"] as? Int ?? 0) >= epoch else { return }
        let previousTheme = currentTheme
        let incomingThemes = array(state["themes"]).compactMap(MapTheme.init)
        let themesChanged = themes != incomingThemes
        let overviewChanged = snapshot["documentEpoch"] as? Int != state["documentEpoch"] as? Int
            || snapshot["hasDocument"] as? Bool != state["hasDocument"] as? Bool
            || textValue(object(snapshot["cityInfo"])["name"]) != textValue(object(state["cityInfo"])["name"])
            || !(object(snapshot["stats"]) as NSDictionary).isEqual(to: object(state["stats"]))
        snapshot = state; themes = incomingThemes
        if let generation = state["documentEpoch"] as? Int { epoch = generation }
        let info = object(state["cityInfo"]); window.title = textValue(info["name"]).isEmpty ? localized("城市地图") : textValue(info["name"])
        engineBusy = state["busy"] as? Bool ?? false
        if overviewChanged, selectedObject == nil, cityDraft == nil, context != "metro" { buildInspector() }
        reloadLayerList()
        if themesChanged || previousTheme != currentTheme { gallery?.close(); rebuildBand() }
        updateCommands()
        presentThemeUpdateIfNeeded()
    }
    func engineEvent(_ name: String, _ payload: JSON) {
        let taskID = textValue(payload["requestId"])
        if let task = toolTasks[taskID] {
            guard task.epoch == epoch else { toolTasks.removeValue(forKey: taskID); return }
            switch name {
            case "exportDone", "externalExportDone":
                toolTasks.removeValue(forKey: taskID)
                showStatus(String(format: localized("已完成：%@"), task.target))
            case "exportCancelled":
                toolTasks.removeValue(forKey: taskID)
                showStatus(localized("导出已取消"))
            case "error":
                toolTasks.removeValue(forKey: taskID)
                showError(AppFailure(payload: payload))
            default: break
            }
        }
        activeExport?.event(name, payload)
        rmp.engineEvent(name, payload)
        if let value = payload["documentEpoch"] as? Int, name != "documentOpening", value != epoch { return }
        previewOutput(name, payload)
        cityDraft?.engineEvent(name, payload)
        themeEditor?.event(name, payload)
        switch name {
        case "documentOpening":
            closeApprovedEditors(pendingCityEditors ?? [])
            pendingCityEditors = []
            if !staticMode, let url = viewer.view.url { viewer.load(url) }
            rmp.hasProject = false; rmp.dirty = false; rmp.path = nil; rmp.state = [:]; rmp.projectID = UUID().uuidString
            metroAppliedFingerprint = nil; sidebarNavigation.requestID = UUID()
            sidebarNavigation.positions.removeAll()
            context = "map"; rmp.hide(); staticPreview.isHidden = false; viewer.view.isHidden = false; buildSidebar(); rebuildBand()
            mapPlaceholder.clearProgress()
            showMapState(.loading("正在绘制地图…"))
            staticPreview.image = nil
            buildingList?.window.close(); staticRefreshPending = false; staticPreviewID = nil; staticPreview.removeFromSuperview(); cityDraft = nil; presentedScene = nil; epoch = payload["documentEpoch"] as? Int ?? epoch; sceneID = ""; revision = 0; previewLayerWrites.removeAll(); selectedBounds = nil; selectedObject = nil; buildInspector(); updateCommands()
        case "documentOpenFailed":
            finishCityRead()
            showMapState(.empty("尚未打开城市"))
            showError(AppFailure(payload: payload))
        case "busy": engineBusy = payload["value"] as? Bool ?? false; updateCommands(); drainFieldQueue()
        case "progress":
            guard cityDraft?.working != true else { break }
            let stage = textValue(payload["message"])
            if let percent = payload["percent"] as? Double, percent.isFinite, (0...100).contains(percent) {
                if taskID.isEmpty { mapPlaceholder.showProgress(percent, stage: stage) }
                showStatus("\(Int(percent))%  \(stage)", kind: .progress)
            } else if !stage.isEmpty { showStatus(stage, kind: .progress) }
        case "exportDone", "externalExportDone": run { try await self.refreshSnapshot(); try await self.applySceneState() }
        case "mapStatistics":
            run { try await self.refreshSnapshot() }
        case "meta", "themeThumbnailReady":
            if name == "meta", textValue(payload["source"]) == "open" {
                finishCityRead()
                run { _ = try await self.engine.request("getMapStatistics") }
            }
            if name == "meta", textValue(payload["source"]) == "open",
               let warnings = object(payload["info"])["fileWarnings"] as? [String], !warnings.isEmpty {
                showTextWindow("文件读取提示", warnings.joined(separator: "\n\n"))
            }
            run {
                try await self.refreshSnapshot()
                if name == "meta", self.staticMode, self.staticPreview.superview == nil, self.staticPreviewID == nil, !self.busy { try await self.renderStaticPreview() }
            }
        case "sceneReady":
            let incoming = payload["revision"] as? Int ?? 0
            if textValue(payload["sceneId"]) == sceneID && incoming < revision { return }
            if !staticMode, case .loading = mapPlaceholder.state { showMapState(.loading("正在绘制地图…")) }
            presentedScene = nil
            sceneID = textValue(payload["sceneId"]); revision = incoming
            previewLayerWrites = previewLayerWrites.filter { $0.value == previewIdentity }
            viewer.send(["type":"loadScene", "manifestUrl":payload["manifestUrl"] ?? "", "sceneId":sceneID,
                         "revision":revision, "preserveArtwork":cityDraft != nil])
            #if CMM_INTEGRATION_CHECKS
            if smoke != nil, CommandLine.arguments.contains("--settings-probe") { smoke?.settingsSceneReady(payload) }
            #endif
        case "sceneError" where textValue(payload["requestId"]).isEmpty && !staticMode:
            showPreviewError(AppFailure(payload: payload))
        case "error", "sceneError", "protocolError":
            if textValue(payload["requestId"]).isEmpty {
                if case .loading = mapPlaceholder.state { showMapState(.unavailable("地图预览不可用")) }
                showError(AppFailure(payload: payload))
            }
        default: break
        }
        #if CMM_INTEGRATION_CHECKS
        smoke?.engineEvent(name, payload)
        #endif
    }
    func viewerEvent(_ frame: JSON) {
        let type = textValue(frame["type"])
        if ["sceneLoaded","selectionChanged","selectionCleared","objectSelected","objectSelectionCleared"].contains(type) {
            guard textValue(frame["sceneId"]) == sceneID, frame["revision"] as? Int == revision else { return }
        }
        if previewIdentity.isPresented(by: frame) {
            if !staticMode, cityDraft == nil, case .loading = mapPlaceholder.state { showMapState(.ready) }
            if presentedScene != previewIdentity && pendingCityEditors == nil
                && !staticMode && context != "metro" && mapPlaceholder.state == .ready && cityDraft?.working != true {
                if frame["completion"] as? String == "base" {
                    showStatus(localized("地图已显示，正在补充细节…"), kind: .progress)
                } else {
                    showStatus(localized("就绪"), kind: .ready)
                }
            }
            presentedScene = previewIdentity
            cityDraft?.deliver()
        }
        switch type {
        case "ready": viewer.send(["type":"setLanguage","language":metadata["language"] ?? "zh-CN"]);updateViewerAppearance();sendPreviewUiState()
        case "previewQualityApplied":
            guard let (id, quality) = pendingQuality, id == frame["requestId"] as? String else { return }
            pendingQuality = nil
            if frame["outcome"] as? String == "applied" { run { try quality.save() } }
            else { showError(AppFailure(message:textValue(frame["message"]))) }
        case "requestLeaveArtwork": if frame["documentEpoch"] as? Int == epoch { cityDraft?.leave() }
        case "artworkSnapshotNeeded": cityDraft?.supplySnapshot(frame)
        case "artworkPresented": cityDraft?.acknowledge(frame)
        case "artworkViewChanged": cityDraft?.observationChanged(frame)
        case "artworkFailed":
            if let draft = cityDraft, frame["artworkRevision"] as? Int == draft.revision {
                if case .loading = mapPlaceholder.state { showMapState(.unavailable("地图预览不可用")) }
                draft.previewFailed(); showError(AppFailure(message: textValue(frame["message"])))
            }
        case "sceneLoaded":
            let generation = epoch, loadedScene = sceneID, loadedRevision = revision
            run { [self] in
                guard generation == epoch, loadedScene == sceneID, loadedRevision == revision else { return }
                _ = try await engine.request("previewPresented", ["bundleId":frame["bundleId"] ?? "", "revision":loadedRevision,"completion":frame["completion"] ?? "complete", "performance":frame["performance"] ?? [:]])
                guard generation == epoch, loadedScene == sceneID, loadedRevision == revision else { return }
                try await refreshSnapshot(); try await applySceneState()
                #if CMM_INTEGRATION_CHECKS
                guard generation == epoch, loadedScene == sceneID, loadedRevision == revision else { return }
                smoke?.sceneLoaded(frame)
                #endif
            }
        case "selectionChanged":
            selectedBounds = frame["bounds"] as? [Double]
        case "selectionCleared":
            selectedBounds = nil
        case "objectSelected": if let draft = cityDraft { draft.pickStyle(frame); return }; selectedObject = frame; buildingList?.synchronize(frame); run { try await self.showObject(frame) }
        case "objectSelectionCleared":
            if let draft = cityDraft { draft.clearPickedStyle(); return }
            selectedObject = nil; buildInspector()
        case "requestLayerVisibility": requestPreviewLayer(frame)
        case "requestMapRotation": requestMapRotation(frame)
        case "renderError":
            if frame["sceneId"] != nil && (textValue(frame["sceneId"]) != sceneID || frame["revision"] as? Int != revision) { return }
            if frame["fatal"] as? Bool == false { showStatus(textValue(frame["message"])); return }
            let error = AppFailure(payload: frame)
            if staticMode { showError(error) }
            else { showPreviewError(error) }
        default: break
        }
    }
    func applySceneState(theme suppliedTheme: JSON? = nil, waitForPaint: Bool = false) async throws {
        guard cityDraft == nil else { return }
        let generation = epoch, targetScene = sceneID, targetRevision = revision, targetContext = context
        let theme: JSON
        if let suppliedTheme { theme = suppliedTheme }
        else { theme = try await engine.request("getPreviewTheme", ["map":targetContext == "map"]) }
        guard generation == epoch, targetScene == sceneID, targetRevision == revision,
              targetContext == context, cityDraft == nil else { return }
        var commands: [JSON] = [["type":"applyTheme","theme":theme,"sceneId":sceneID,"revision":revision]]
        for (key,value) in viewerLayers() {
            commands.append(["type":"setLayerVisibility","layer":key,"visible":value,"sceneId":sceneID,"revision":revision])
        }
        if context != "metro" {
            commands.append(["type": "setContextMode", "mode": context,
                         "options": object(snapshot[context == "transit" ? "transitOptions" : "mapTransitOptions"])])
        }
        commands.append(["type":"setOutputRotation","rotation":object(snapshot["output"])["rotation"] ?? 0])
        if waitForPaint {
            try await viewer.applyAndPresent(commands)
            guard generation == epoch, targetScene == sceneID, targetRevision == revision, targetContext == context else { return }
        } else {
            for command in commands { viewer.send(command) }
        }
        sendPreviewUiState()
    }
    func viewerLayers() -> JSON {
        var layers = object(snapshot["layers"])
        if layers["label"] as? Bool == false || (context == "map" && !defaultVisibility(MapSymbolCategory.icons.title)) {
            layers["poi"] = false
        }
        return layers
    }
    func updateViewerAppearance() {
        if rmp.initialized { run { try await self.rmp.setAppearance() } }
        guard viewer.ready else { return }
        let dark=root.effectiveAppearance.bestMatch(from:[.darkAqua,.aqua]) == .darkAqua
        viewer.send(["type":"setUiAppearance","appearance":dark ? "dark":"light"])
    }
    func update(_ method: String, _ params: JSON, refresh: Bool = true, previewScene: PreviewSceneIdentity? = nil) {
        guard engineReady else { return }
        fieldQueue.append(FieldUpdate(method: method, params: params, refresh: refresh, epoch: epoch, previewScene: previewScene, context: context))
        drainFieldQueue()
    }
    func drainFieldQueue() {
        guard !fieldWriting, !busy, !fieldQueue.isEmpty else { return }
        fieldWriting = true; updateCommands()
        let task = Task { @MainActor [self] in
            var completedPreviewWrites: [(String, PreviewSceneIdentity)] = []
            var transitRefresh: (draft: CityDraft, reservation: Int)?
            defer {
                if let transitRefresh { transitRefresh.draft.clearUnusedTransitRefresh(transitRefresh.reservation) }
                for (layer, identity) in completedPreviewWrites where previewLayerWrites[layer] == identity { previewLayerWrites.removeValue(forKey: layer) }
                fieldWriteTask = nil
                fieldWriting = false; updateCommands(); drainFieldQueue()
            }
            let generation = epoch
            var needsPreview = false
            var updatedLayer: String?
            var satelliteGuidance = false
            var viewerLayerCommands: [JSON] = []
            while !fieldQueue.isEmpty {
                let write = fieldQueue.removeFirst()
                if ["setLayerVisibility", "setLabelChild"].contains(write.method), let identity = write.previewScene {
                    completedPreviewWrites.append((textValue(write.params["key"]), identity))
                }
                guard write.epoch == epoch else { continue }
                if let identity = write.previewScene, identity != previewIdentity || write.context != context { continue }
                var request = write.params; request["documentEpoch"] = write.epoch
                do {
                    let rotationDelta = request.removeValue(forKey: "rotationDelta") as? Int
                    if let rotationDelta {
                        let state = try await engine.request("getSnapshot")
                        guard write.previewScene == previewIdentity, write.context == context, previewRotationEnabled else { continue }
                        let angle = object(state["output"])["rotation"] as? Int ?? 0
                        request["value"] = (angle + rotationDelta + 360) % 360
                    }
                    let key = textValue(request["key"])
                    let visibility = write.method == "setLayerVisibility"
                    let enablingSatellite = visibility && key == "satellite" && request["enabled"] as? Bool == true
                    if transitRefresh == nil, context == "transit", write.refresh, request["draft"] as? Bool == true,
                       let draft = cityDraft {
                        transitRefresh = (draft, draft.reserveTransitRefresh())
                    }
                    if transitRefresh == nil && (visibility || ["setTransitOption", "setTransitType", "setLabelChild"].contains(write.method)) {
                        let title = layerKeys.first { $0.value == key }?.key ?? textValue(schema(key)["label"])
                        let option = title.isEmpty ? textValue(object(transitSchema[key])["label"]) : title
                        let operation = visibility ? (request["enabled"] as? Bool == true ? "正在显示" : "正在隐藏") : "正在更新"
                        let name = option.isEmpty ? NativePresentation.identifier(key) : localized(option)
                        updatedLayer = name
                        showStatus(localized(operation) + " · " + name, kind: .progress)
                    }
                    if enablingSatellite || write.method == "applySatelliteRecommendations" {
                        request["maxTextureSize"] = try await satelliteTextureLimit()
                    }
                    let wasSatellite = snapshot["theme"] as? String == "satellite"
                    let layerWasOn = object(snapshot["layers"])["satellite"] as? Bool == true
                    let result = try await engine.request(write.method, request)
                    guard write.epoch == epoch else { continue }
                    if rotationDelta != nil, let angle = result["rotation"] as? Int {
                        viewer.send(["type": "setOutputRotation", "rotation": angle])
                    }
                    if visibility {
                        let layers = object(result["mapLayers"])
                        for (layer, value) in layers where key == "satellite" || layer == key || (key == "label" && layer == "poi") {
                            viewerLayerCommands.append(["type":"setLayerVisibility", "layer":layer, "visible":value, "sceneId":sceneID, "revision":revision])
                        }
                        needsPreview = needsPreview || cityDraft != nil || staticMode || ["satellite", "label", "poi"].contains(key)
                        if key == "satellite" { satelliteGuidance = enablingSatellite && !layerWasOn }
                    } else {
                        needsPreview = needsPreview || write.refresh
                        if write.method == "setTheme" {
                            satelliteGuidance = !wasSatellite && object(result["snapshot"])["theme"] as? String == "satellite"
                        }
                    }
                } catch {
                    fieldQueue.removeAll()
                    previewLayerWrites.removeAll()
                    try await refreshSnapshot()
                    throw error
                }
            }
            try await refreshSnapshot()
            guard generation == epoch else { return }
            if !staticMode { try await viewer.applyAndPresent(viewerLayerCommands) }
            guard generation == epoch else { return }
            let waitingForScene = needsPreview ? try await refreshMapPreview(waitForPaint: updatedLayer != nil) : false
            guard generation == epoch else { return }
            if let updatedLayer, !waitingForScene { showStatus(localized("已更新") + " · " + updatedLayer, kind: .ready) }
            if satelliteGuidance { recommendSatellite() }
        }
        fieldWriteTask = task
        run { try await task.value }
    }
    func flushFieldWrites() async throws {
        drainFieldQueue()
        while let task = fieldWriteTask { try await task.value }
        guard engineReady, fieldQueue.isEmpty else {
            throw AppFailure(message: localized("请等待当前操作完成。"))
        }
    }
    func openCity(_ url: URL) async throws {
        if fieldWriting || !fieldQueue.isEmpty { try await flushFieldWrites() }
        guard let editors = editorsPermittingReplacement() else { return }
        pendingCityEditors = editors
        var started = false
        defer { if !started { finishCityRead() } }
        guard try await rmp.permitReplacement() else { return }
        rmp.syncHost()
        updateCommands()
        showStatus(localized("正在读取城市…") + " " + url.lastPathComponent, kind: .progress)
        let result = try await engine.request("openDocument", ["path":url.path])
        guard result["started"] as? Bool == true else { throw AppFailure(message: "未能开始加载城市") }
        started = true
    }
    func finishCityRead() {
        pendingCityEditors = nil
        rmp.syncHost()
        updateCommands()
        drainFieldQueue()
    }
    func closeEditors() -> Bool {
        guard let editors = editorsPermittingReplacement() else { return false }
        closeApprovedEditors(editors)
        return true
    }
    private func editorsPermittingReplacement() -> [EditorWindow]? {
        guard cityDraft == nil else { return nil }
        let editors = panels.compactMap { $0 as? EditorWindow }.filter { $0.isVisible }
        return editors.allSatisfy { $0.permitClose() } ? editors : nil
    }
    private func closeApprovedEditors(_ editors: [EditorWindow]) {
        for editor in editors where editor.isVisible { editor.dirty = false; editor.close() }
    }
    func cancelTask() {
        if rmp.cancelGeneration() { return }
        run { [self] in
            if pendingCityEditors != nil {
                let result = try await engine.request("cancelDocumentOpen")
                if result["cancelled"] as? Bool == true {
                    finishCityRead()
                    showStatus(localized("任务已取消"), kind: .ready)
                }
                return
            }
            let result = try await engine.request("cancel")
            showStatus(result["cancelled"] as? Bool == true ? localized("任务已取消") : textValue(result["message"]))
            if result["cancelled"] as? Bool == true {
                if case .loading = mapPlaceholder.state { showMapState(.empty("预览已取消")) }
                toolTasks.removeAll()
                activeExport?.cancelled()
            }
            try await refreshSnapshot()
        }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if terminating { return .terminateNow }
        if terminationPending { return .terminateCancel }
        terminationPending = true
        run { [self] in
            defer { terminationPending = false }
            guard !draftSettingsOpen, !draftWorkflowOpen else { return }
            if let draft = cityDraft, try await !draft.finish(leaving: true) { return }
            guard pendingCityEditors == nil, !rmp.busy, !rmp.placing,
                  let editors = editorsPermittingReplacement(), try await rmp.permitReplacement() else { return }
            closeApprovedEditors(editors)
            terminating = true
            viewer.dispose(); rmp.host.dispose()
            await engine.shutdown()
            sender.terminate(nil)
        }
        // Finish asynchronous saves outside the termination modal loop.
        return .terminateCancel
    }
    func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }
    func defaultVisibility(_ name: String) -> Bool {
        guard let layer = layerKeys[name] else { return false }
        if let category = MapSymbolCategory(rawValue: layer) {
            let source = category.usesSymbolVisibility ? "mapSymbolVisibility" : "fields"
            return object(snapshot[source])[category.visibilityKey] as? Bool ?? false
        }
        return object(snapshot["layers"])[layer] as? Bool ?? false
    }
    func changeLayerVisibility(_ layer: String, enabled: Any, previewScene: PreviewSceneIdentity? = nil) {
        update(MapSymbolCategory(rawValue: layer) == nil ? "setLayerVisibility" : "setLabelChild",
               ["key":layer, "enabled":enabled], previewScene: previewScene)
    }
    @objc func changeCheck(_ sender: NSButton) {
        guard let key = sender.identifier?.rawValue, key.hasPrefix("layer:"), let layer = layerKeys[String(key.dropFirst(6))] else { return }
        changeLayerVisibility(layer, enabled: sender.state == .on)
    }
}
