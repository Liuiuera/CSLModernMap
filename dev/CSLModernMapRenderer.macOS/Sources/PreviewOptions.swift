import AppKit
import WebKit

@MainActor private final class QualityChange: NSObject {
    var changed: (() -> Void)?
    @objc func invoke(_ sender: Any) { changed?() }
}

extension InterfaceController {
    func previewSettings() {
        let alert = NSAlert()
        alert.messageText = localized("预览质量")
        let pixels = NSPopUpButton(), lines = NSPopUpButton(), polygons = NSPopUpButton()
        pixels.addItems(withTitles:["75%","100%","125%"])
        for field in [lines,polygons] { field.addItems(withTitles:["标准","精细"].map(localized)) }
        let ratios = [0.75,1,1.25]
        let sample = viewerURL == nil ? nil : WebHost(channel:"cslQuality")
        defer { sample?.dispose() }
        let change = QualityChange()
        let scaleTitles = ["字号 (%)", "线宽 (%)", "图标 (%)"]
        let scaleSliders = scaleTitles.map { title -> NSSlider in
            let slider = NSSlider(value:100, minValue:50, maxValue:200, target:change, action:#selector(QualityChange.invoke(_:)))
            slider.isContinuous = true
            slider.setAccessibilityLabel(localized(title))
            return slider
        }
        let scaleValues = scaleTitles.map { _ in NSTextField(labelWithString:"100%") }
        func selected() -> PreviewQuality {
            PreviewQuality(pixelScale:ratios[pixels.indexOfSelectedItem],
                lineDetail:lines.indexOfSelectedItem == 1 ? "fine":"standard",
                polygonDetail:polygons.indexOfSelectedItem == 1 ? "fine":"standard",
                textPercent:Int(scaleSliders[0].doubleValue.rounded()),
                linePercent:Int(scaleSliders[1].doubleValue.rounded()),
                iconPercent:Int(scaleSliders[2].doubleValue.rounded()))
        }
        let levels = [PreviewQuality(pixelScale: 0.75), PreviewQuality(), PreviewQuality(pixelScale: 1.25, lineDetail: "fine", polygonDetail: "fine")]
        let presets = ChoiceSegments(["流畅", "标准", "精细"], selected: -1) { index in select(levels[index]) }
        presets.setAccessibilityLabel(localized("预览质量"))
        change.changed = { [weak self, weak sample, weak presets] in
            let quality = selected()
            presets?.selectedSegment = levels.firstIndex { quality.matchesPreset($0) } ?? -1
            for (index, slider) in scaleSliders.enumerated() {
                let value = Int(slider.doubleValue.rounded())
                scaleValues[index].stringValue = "\(value)%"
            }
            guard let self = self, let sample = sample else { return }
            sample.send(["type":"setQualityDemo", "quality":quality.message, "baselineLabel":localized("标准"),
                "draftLabel":localized("当前设置"), "language":self.metadata["language"] ?? "zh-CN",
                "appearance":self.window.effectiveAppearance.bestMatch(from:[.darkAqua,.aqua]) == .darkAqua ? "dark":"light"])
        }
        for field in [pixels,lines,polygons] { field.target = change; field.action = #selector(QualityChange.invoke(_:)) }
        func select(_ value: PreviewQuality, includeScales: Bool = false) {
            pixels.selectItem(at:ratios.firstIndex(of:value.pixelScale) ?? 1)
            lines.selectItem(at:value.lineDetail == "fine" ? 1 : 0)
            polygons.selectItem(at:value.polygonDetail == "fine" ? 1 : 0)
            if includeScales {
                for (slider, value) in zip(scaleSliders, [value.textPercent,value.linePercent,value.iconPercent]) {
                    slider.integerValue = value
                }
            }
            change.changed?()
        }
        if let url = viewerURL {
            sample?.load(url.deletingLastPathComponent().appendingPathComponent("quality-demo.html"))
        }
        select(PreviewQuality.saved, includeScales:true)
        let fields = [("像素倍率", pixels), ("线条细节", lines), ("面细节", polygons)].map { title, field in
            let group = column([label(title, 12, .regular, .secondaryLabelColor), field], gap: 4)
            field.widthAnchor.constraint(equalTo: group.widthAnchor).isActive = true
            return group
        }
        let grid = row(fields, gap: 12)
        grid.distribution = .fillEqually
        let distanceTitle = label("视距缩放", 13, .semibold)
        let guide = NSTextField(wrappingLabelWithString:localized("调整中档，远、近两档自动计算。100% 使用主题或视图设定的大小。"))
        let caps = NSTextField(wrappingLabelWithString:localized("站点随线宽缩放。远档上限为字号 10 px、站点直径 6 px、图标 12 px。"))
        for field in [guide,caps] { field.font = .systemFont(ofSize:11); field.textColor = .secondaryLabelColor }
        let scaleRows = scaleTitles.indices.map { index -> NSStackView in
            let name = label(scaleTitles[index], 12)
            name.widthAnchor.constraint(equalToConstant:130).isActive = true
            let value = scaleValues[index]
            value.alignment = .right
            value.font = .monospacedDigitSystemFont(ofSize:12, weight:.regular)
            value.widthAnchor.constraint(equalToConstant:48).isActive = true
            scaleSliders[index].setContentHuggingPriority(.defaultLow, for:.horizontal)
            return row([name,scaleSliders[index],value], gap:10)
        }
        let distance = column([distanceTitle,guide] as [NSView] + scaleRows + [caps], gap:7)
        let form = column([presets, grid, distance], gap: 14)
        let width: CGFloat = 530
        form.widthAnchor.constraint(equalToConstant: width).isActive = true
        for view in [presets, grid, distance] as [NSView] { view.widthAnchor.constraint(equalTo: form.widthAnchor).isActive = true }
        for view in [guide,caps] as [NSView] + scaleRows { view.widthAnchor.constraint(equalTo: distance.widthAnchor).isActive = true }
        if let sample = sample {
            let box = NSView()
            let placeholder = PreviewPlaceholder()
            pin(sample.view, in: box)
            sample.view.wantsLayer = true
            sample.view.layer?.opacity = 0
            pin(placeholder, in: box)
            placeholder.show(.loading("正在载入对照预览…"))
            sample.event = { [weak placeholder, weak sample] frame in
                switch frame["type"] as? String {
                case "qualityDemoReady": sample?.view.layer?.opacity = 1; placeholder?.show(.ready)
                case "qualityDemoError": sample?.view.layer?.opacity = 0; placeholder?.show(.unavailable("对照预览暂不可用"))
                default: break
                }
            }
            sample.failed = { [weak placeholder, weak sample] _ in
                sample?.view.layer?.opacity = 0
                placeholder?.show(.unavailable("对照预览暂不可用"))
            }
            form.addArrangedSubview(box)
            box.widthAnchor.constraint(equalTo: form.widthAnchor).isActive = true
            box.heightAnchor.constraint(equalToConstant: 220).isActive = true
        }
        form.setFrameSize(form.fittingSize)
        alert.accessoryView = form
        alert.addButton(withTitle:localized("应用")); alert.addButton(withTitle:localized("取消"))
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        let quality = selected()
        if !viewer.ready { run { try quality.save() }; return }
        let id = UUID().uuidString
        pendingQuality = (id,quality)
        viewer.send(["type":"setPreviewQuality","requestId":id,"quality":quality.message])
        DispatchQueue.main.asyncAfter(deadline:.now()+30) { [weak self] in
            guard let self = self, self.pendingQuality?.0 == id else { return }
            self.pendingQuality = nil
            self.showError(AppFailure(message:localized("地图未确认预览质量设置。")))
        }
    }

    func startInteractivePreview() async throws {
        let result = try await engine.request("startPreview")
        guard let url = URL(string:textValue(result["viewerUrl"])), url.host == "127.0.0.1", url.scheme == "http",
              var parts = URLComponents(url:url,resolvingAgainstBaseURL:false) else { throw AppFailure(message:"引擎返回无效预览地址") }
        viewerURL = url
        parts.queryItems = [URLQueryItem(name:"desktopUi",value:"1")]
        staticMode = false; staticRefreshPending = false; staticPreviewID = nil
        staticPreview.removeFromSuperview()
        showMapState(snapshot["hasDocument"] as? Bool == true ? .loading("正在绘制地图…") : .empty("尚未打开城市"))
        viewer.view.isHidden = false
        presentedScene = nil
        #if CMM_INTEGRATION_CHECKS
        if smoke == nil || !CommandLine.arguments.contains("--settings-probe") { viewer.load(parts.url!) }
        #else
        viewer.load(parts.url!)
        #endif
        _ = try await engine.request("setPreviewEnabled", ["enabled":true])
        if let draft = cityDraft {
            _ = try await engine.request("restoreCityArtworkPreview", ["documentEpoch": draft.epoch])
        } else { try await refreshMapPreview(force: true) }
    }

    @discardableResult func refreshMapPreview(force: Bool = false, waitForPaint: Bool = false) async throws -> Bool {
        guard context != "metro", snapshot["hasDocument"] as? Bool == true else { return false }
        if let draft = cityDraft {
            try await draft.request("refreshCityArtwork")
            return true
        }
        if staticMode {
            try await renderStaticPreview()
            return true
        }
        let identity = previewIdentity, target = context
        let result = try await engine.request("refreshPreview", ["map": target == "map", "force": force])
        guard previewIdentity == identity, context == target, !staticMode, cityDraft == nil else { return true }
        let started = result["started"] as? Bool == true
        if let theme = result["theme"] as? JSON { try await applySceneState(theme: theme, waitForPaint: waitForPaint && !started) }
        return started
    }

    func openBrowserPreview() {
        let id = UUID().uuidString
        browserPreviewID = id
        run { [self] in
            let result = try await engine.request("openBrowserPreview", ["context":context == "transit" ? "transit":"normal",
                "documentEpoch":epoch,"requestId":id])
            guard result["started"] as? Bool == true else { throw AppFailure(message:"当前无法启动浏览器预览") }
        }
    }

    func renderStaticPreview() async throws {
        guard context != "metro", cityDraft == nil, snapshot["hasDocument"] as? Bool == true else { return }
        if staticPreviewID != nil { staticRefreshPending = true; return }
        if staticPreview.superview == nil { showMapState(.loading("正在绘制地图…")) }
        staticMode = true; staticRefreshPending = false
        let id = UUID().uuidString
        staticPreviewID = id
        defer { updateCommands() }
        do {
        _ = try await engine.request("setPreviewEnabled", ["enabled":false,"documentEpoch":epoch])
        guard staticPreviewID == id else { return }
        let result = try await engine.request("renderCompatibilityPreview", ["documentEpoch":epoch, "requestId":id,
            "context":context == "transit" ? "transit":"normal"])
        if result["started"] as? Bool != true { throw AppFailure(message:"请等待当前任务完成后重试兼容预览。") }
        } catch {
            if staticPreviewID == id {
                staticPreviewID = nil
                if case .loading = mapPlaceholder.state { showMapState(.unavailable("地图预览不可用")) }
            }
            throw error
        }
    }

    func finishStaticPreview(_ id: String) {
        guard staticPreviewID == id else { return }
        staticPreviewID = nil
        if staticMode, staticRefreshPending { run { try await self.renderStaticPreview() } }
    }

    func previewOutput(_ name: String, _ frame: JSON) {
        guard let id = frame["requestId"] as? String else { return }
        if name == "compatibilityPreviewReady" {
            if context != "metro", staticMode, !staticRefreshPending, staticPreviewID == id, frame["documentEpoch"] as? Int == epoch,
               let url = URL(string:textValue(frame["url"])), url.isFileURL, let image = NSImage(contentsOf:url) {
                staticPreview.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
                staticPreview.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
                staticPreview.image = image; staticPreview.imageScaling = .scaleProportionallyUpOrDown
                if staticPreview.superview == nil {
                    pin(staticPreview, in: center)
                    center.addSubview(staticPreview, positioned: .below, relativeTo: mapPlaceholder)
                }
                viewer.view.isHidden = true
                showMapState(.ready)
                showStatus(localized("就绪"), kind: .ready)
            } else if context != "metro", staticMode, !staticRefreshPending, staticPreviewID == id {
                showMapState(.unavailable("地图预览不可用"))
                dismissStatus()
            }
            finishStaticPreview(id)
            run { [self] in _ = try await engine.request("releaseCompatibilityPreview", ["requestId":id]) }
        } else if id == staticPreviewID, ["compatibilityPreviewFailed","compatibilityPreviewCancelled"].contains(name) {
            finishStaticPreview(id)
            if !staticRefreshPending, case .loading = mapPlaceholder.state {
                showMapState(name == "compatibilityPreviewCancelled" ? .empty("预览已取消") : .unavailable("地图预览不可用"))
            }
            if name == "compatibilityPreviewFailed" { showError(AppFailure(payload: frame)) }
            else if !staticRefreshPending { showStatus(localized("预览已取消"), kind: .ready) }
        } else if id == browserPreviewID, ["exportDone","error","exportCancelled"].contains(name) {
            browserPreviewID = nil
            if name == "exportDone", let path = frame["path"] as? String { NSWorkspace.shared.open(URL(fileURLWithPath:path)) }
            else if name == "error" { showError(AppFailure(payload: frame)) }
        }
    }
}
