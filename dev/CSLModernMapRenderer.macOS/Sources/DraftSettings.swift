import AppKit

@MainActor final class DraftSettings: NSObject, NSTextFieldDelegate {
    private unowned let owner: InterfaceController
    private let epoch: Int
    private var alert: NSAlert?
    private var fields: NSGridView!
    private let range = NSPopUpButton()
    private let rangePreview = MapRangeCanvas()
    private let rangeSummary = label("")
    private var rangeProfile: MapRangeProfile?
    private let size = NSPopUpButton()
    private let width = NSTextField()
    private let height = NSTextField()
    private let scale = NSTextField()
    private let rotation = NSPopUpButton()
    private let density = NSPopUpButton()
    private let densityEnabled = NSButton(checkboxWithTitle: localized("启用图标密度控制"), target: nil, action: nil)
    private let levels = ["extremely_sparse", "very_sparse", "sparse", "standard", "dense", "very_dense", "extremely_dense"]
    private let presets = [2048, 4096, 8192]
    private var regions: [Any] = [NSNull()]
    private var longEdge: Int
    private var customEdge: Int?
    private var pendingDimension: (axis: String, pixels: Int?)?
    private var geometryTask: Task<Void, Never>?
    private var geometryValid = false
    private var geometryError = ""
    private var rangeError = ""

    init(owner: InterfaceController, config: JSON, custom: Bool) {
        self.owner = owner
        epoch = owner.epoch
        longEdge = config["longEdge"] as? Int ?? 4096
        super.init()
        range.addItem(withTitle: localized("全城"))
        if let selection = owner.selectedBounds {
            appendRegion("当前选区", ["bounds": selection, "source": "selection"])
        }
        let region = object(config["region"])
        if textValue(region["source"]) == "selection",
           let bounds = region["bounds"] as? [Double], bounds == owner.selectedBounds {
            range.selectItem(at: 1)
        } else if !region.isEmpty, textValue(region["source"]) != "city" {
            appendRegion("当前范围", region)
            range.selectItem(at: regions.count - 1)
        }

        size.addItems(withTitles: presets.map { "\($0) px" } + [localized("自定义尺寸")])
        let presetIndex = presets.firstIndex(of: longEdge)
        size.selectItem(at: custom ? presets.count : presetIndex ?? presets.count)
        if isCustom { customEdge = longEdge }
        scale.stringValue = String((config["elementScale"] as? Double ?? 1) * 100)
        rotation.addItems(withTitles: ["0°", "90°", "180°", "270°"])
        rotation.selectItem(at: (config["rotation"] as? Int ?? 0) / 90)
        density.addItems(withTitles: ["极稀疏", "很稀疏", "稀疏", "标准", "密集", "很密集", "极密集"].map(localized))
        let poi = object(config["poiDensity"])
        density.selectItem(at: levels.firstIndex(of: textValue(poi["density_tier"])) ?? 3)
        densityEnabled.state = poi["density_enabled"] as? Bool == false ? .off : .on
        densityEnabled.target = self; densityEnabled.action = #selector(toggleDensity)
        toggleDensity()

        for control in [range, size, rotation] {
            control.target = self
            control.action = #selector(changeGeometry(_:))
        }
        for field in [width, height, scale] {
            field.delegate = self
            field.widthAnchor.constraint(greaterThanOrEqualToConstant: 110).isActive = true
        }
        size.identifier = NSUserInterfaceItemIdentifier("draft.size")
        rotation.identifier = NSUserInterfaceItemIdentifier("draft.rotation")
        width.setAccessibilityLabel(localized("宽度（px）"))
        height.setAccessibilityLabel(localized("高度（px）"))
        scale.setAccessibilityLabel(localized("元素尺寸倍率（%）"))
        rangePreview.heightAnchor.constraint(equalToConstant: 124).isActive = true
        rangePreview.widthAnchor.constraint(equalToConstant: 220).isActive = true
        rangePreview.isHidden = true
        let rangeDetails = column([range, rangePreview, rangeSummary], gap: 5)
        fields = settingsGrid([("范围", rangeDetails), ("图像尺寸", size), ("宽度（px）", width), ("高度（px）", height),
            ("元素尺寸倍率（%）", scale), ("旋转", rotation), ("图标密度", row([densityEnabled, density], gap: 6))])
        fields.setFrameSize(fields.fittingSize)
    }

    private var isCustom: Bool { size.indexOfSelectedItem == presets.count }

    private func appendRegion(_ title: String, _ value: JSON) {
        regions.append(value)
        range.addItem(withTitle: localized(title))
    }

    @objc private func toggleDensity() { density.isEnabled = densityEnabled.state == .on }

    private func text(_ field: NSTextField) -> String {
        (field.currentEditor()?.string ?? field.stringValue).trimmingCharacters(in: .whitespaces)
    }

    private var factor: Double? {
        guard let value = Double(text(scale)), value.isFinite, value > 0 else { return nil }
        return value / 100
    }

    private func updateValidation() {
        let message = geometryError.isEmpty && factor == nil ? localized("请输入有效的尺寸倍率。") : geometryError
        alert?.informativeText = [message, rangeError].filter { !$0.isEmpty }.joined(separator: "\n")
        alert?.buttons[0].isEnabled = geometryValid && factor != nil
    }

    @objc private func changeGeometry(_ sender: NSPopUpButton) {
        if sender === range { rangeError = "" }
        refreshRange()
        if sender === size { pendingDimension = nil }
        if let pendingDimension {
            calculate(axis: pendingDimension.axis, pixels: pendingDimension.pixels)
        } else {
            let edge = isCustom ? customEdge ?? longEdge : presets[size.indexOfSelectedItem]
            calculate(axis: "longEdge", pixels: edge)
        }
    }

    private var effectiveBounds: MapRangeBounds? {
        MapRangeBounds(object(regions[range.indexOfSelectedItem])["bounds"]) ?? rangeProfile?.city
    }

    private func refreshRange() {
        guard let profile = rangeProfile, let bounds = effectiveBounds else { return }
        rangePreview.isHidden = false
        rangePreview.show(profile, area: profile.world, selection: bounds)
        rangeSummary.stringValue = bounds.summary
    }

    func controlTextDidChange(_ notification: Notification) {
        guard let field = notification.object as? NSTextField else { return }
        if field === scale { updateValidation(); return }
        let change = (axis: field === width ? "width" : "height", pixels: Int(text(field)))
        pendingDimension = change
        calculate(axis: change.axis, pixels: change.pixels)
    }

    private func calculate(axis: String, pixels: Int?) {
        geometryTask?.cancel()
        geometryValid = false
        width.isEnabled = isCustom; height.isEnabled = isCustom
        geometryError = ""
        guard let pixels, pixels > 0 else {
            geometryError = localized("请输入有效的长边和尺寸倍率。")
            updateValidation()
            return
        }
        let oldFactor = factor
        let request: JSON = ["documentEpoch": epoch, "axis": axis, "pixels": pixels, "previousEdge": longEdge,
            "elementScale": oldFactor ?? 1, "rotation": rotation.indexOfSelectedItem * 90,
            "region": regions[range.indexOfSelectedItem]]
        updateValidation()
        geometryTask = Task { [self] in
            do {
                let result = try await owner.engine.request("getCityArtworkGeometry", request)
                try Task.checkCancellation()
                guard let edge = result["longEdge"] as? Int, let dimensions = result["size"] as? [Int], dimensions.count == 2,
                      let adjusted = result["elementScale"] as? Double else { return }
                longEdge = edge
                if isCustom { customEdge = edge }
                pendingDimension = nil
                if width.currentEditor() == nil || axis != "width" { width.stringValue = String(dimensions[0]) }
                if height.currentEditor() == nil || axis != "height" { height.stringValue = String(dimensions[1]) }
                if oldFactor != nil, factor == oldFactor {
                    let value = String(adjusted * 100)
                    scale.stringValue = value
                    scale.currentEditor()?.string = value
                }
                for (index, preset) in array(result["presets"]).enumerated() where presets.indices.contains(index) {
                    if let pair = preset["size"] as? [Int], pair.count == 2 {
                        size.item(at: index)?.title = "\(pair[0]) × \(pair[1]) px"
                    }
                }
                geometryValid = true
            } catch is CancellationError { return }
            catch {
                guard !Task.isCancelled else { return }
                geometryError = localized("请输入有效的长边和尺寸倍率。")
            }
            updateValidation()
        }
    }

    func options() async -> JSON? {
        defer { geometryTask?.cancel() }
        do {
            rangeProfile = MapRangeProfile(try await owner.engine.request("getRangeProfile"))
            refreshRange()
            fields.setFrameSize(fields.fittingSize)
        } catch { rangeError = error.localizedDescription }
        calculate(axis: "longEdge", pixels: longEdge)
        while owner.epoch == epoch {
            let sheet = NSAlert()
            sheet.messageText = localized("地图草稿设置")
            sheet.addButton(withTitle: localized("确定"))
            sheet.addButton(withTitle: localized("取消"))
            sheet.addButton(withTitle: localized("选择范围…"))
            sheet.accessoryView = fields
            alert = sheet
            updateValidation()
            let response = await sheet.beginSheetModal(for: owner.window)
            sheet.accessoryView = nil
            alert = nil
            if response == .alertThirdButtonReturn {
                guard owner.epoch == epoch else { return nil }
                if rangeProfile == nil {
                    do {
                        rangeProfile = MapRangeProfile(try await owner.engine.request("getRangeProfile"))
                        refreshRange(); fields.setFrameSize(fields.fittingSize)
                    }
                    catch { rangeError = error.localizedDescription; continue }
                }
                guard let rangeProfile else {
                    rangeError = localized("无法加载导出范围预览："); continue
                }
                guard owner.epoch == epoch else { return nil }
                rangeError = ""
                let chooser = MapRangeChooser(engine: owner.engine, profile: rangeProfile, initial: effectiveBounds)
                if let bounds = await chooser.choose(parent: owner.window), owner.epoch == epoch {
                    let region: JSON = ["bounds": bounds.values, "source": "custom"]
                    let title = localized("自定义范围")
                    let index = range.indexOfItem(withTitle: title)
                    if index < 0 {
                        appendRegion("自定义范围", region)
                        range.selectItem(at: regions.count - 1)
                    } else {
                        regions[index] = region
                        range.selectItem(at: index)
                    }
                    changeGeometry(range)
                }
                continue
            }
            guard response == .alertFirstButtonReturn, geometryValid, let factor else { return nil }
            return ["longEdge": longEdge, "elementScale": factor, "rotation": rotation.indexOfSelectedItem * 90,
                "region": regions[range.indexOfSelectedItem], "customResolution": isCustom,
                "poiDensity": ["density_enabled": densityEnabled.state == .on,
                               "density_tier": levels[density.indexOfSelectedItem]]]
        }
        return nil
    }
}
