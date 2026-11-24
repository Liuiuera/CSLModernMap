import AppKit

@MainActor final class RandomSpotController {
    unowned let owner: InterfaceController
    let window = EditorWindow("随机特写", width: 470, height: 365)
    let form = FormView()
    private let epoch: Int
    private let context: String
    private var scene = "overall"
    private var bounds: [Double]?
    private var picking = false
    private let status = NSTextField(wrappingLabelWithString: "")
    private lazy var seedField = form.field("随机种子（可选）", ["kind": "text"], "") { _ in }
    private lazy var pickButton = ActionButton("抽取") { [weak self] in
        guard let self else { return }
        self.owner.run { try await self.pick() }
    }
    private lazy var exportButton = ActionButton("导出选区") { [weak self] in self?.export() }

    init(owner: InterfaceController) {
        self.owner = owner
        epoch = owner.epoch
        context = owner.context
    }

    func show() {
        let choices = [
            ("overall", "综合"), ("buildings", "建筑"), ("transports", "交通"),
            ("water", "水域"), ("poi", "地点"), ("roads", "道路")
        ].map { ["value": $0.0, "label": $0.1] }
        form.field("特写场景", ["kind": "choice", "choices": choices], scene) { [weak self] value in
            self?.scene = textValue(value)
            self?.clearResult()
        }
        seedField.onTextEdit = { [weak self] in self?.clearResult() }
        status.textColor = .secondaryLabelColor
        form.add(status)
        form.add(row([pickButton, NSView(), exportButton], gap: 8))
        pin(form, in: window.surface)
        window.onClose = { [weak self] in
            guard let self else { return }
            self.owner.panels.removeAll { $0 === self.window }
            if self.owner.randomSpot === self { self.owner.randomSpot = nil }
        }
        owner.panels.append(window)
        clearResult()
        let map = owner.window.frame
        let screen = owner.window.screen?.visibleFrame ?? map
        window.setFrameTopLeftPoint(NSPoint(
            x: max(screen.minX, min(map.maxX, screen.maxX) - window.frame.width - 16),
            y: min(map.maxY, screen.maxY) - 48
        ))
        window.makeKeyAndOrderFront(nil)
    }

    private var isCurrent: Bool {
        owner.randomSpot === self && owner.epoch == epoch && owner.context == context
            && owner.pendingCityEditors == nil
    }

    private func updateControls() {
        for field in form.fields { field.refresh(enabled: !picking) }
        pickButton.isEnabled = !picking
        exportButton.isEnabled = !picking && bounds != nil
    }

    private func clearResult() {
        bounds = nil
        status.stringValue = localized("请先抽取范围")
        updateControls()
    }

    private func pick() async throws {
        guard isCurrent, !picking, owner.commandEnabled("随机特写") else { return }
        let seed = textValue(try seedField.pendingInputValue())
        clearResult()
        picking = true
        status.stringValue = localized("正在抽取范围…")
        updateControls()
        defer {
            picking = false
            updateControls()
        }
        let result: JSON
        do {
            result = try await owner.engine.request("pickRandomRegion", [
                "scene": scene,
                "seed": seed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? NSNull() : seed
            ])
        } catch {
            guard isCurrent else { return }
            status.stringValue = localized("操作未完成")
            throw error
        }
        guard isCurrent else { return }
        guard let selected = result["bounds"] as? [Double], selected.count == 4 else {
            status.stringValue = localized("当前城市没有可用的随机特写范围")
            return
        }
        bounds = selected
        owner.selectedBounds = selected
        if !owner.staticMode, !owner.sceneID.isEmpty {
            owner.viewer.send(["type": "setSelection", "worldBounds": selected,
                               "sceneId": owner.sceneID, "revision": owner.revision])
            let metersPerDegree = 111195.0
            let geoBounds = [
                [-selected[3] / metersPerDegree, selected[0] / metersPerDegree],
                [-selected[1] / metersPerDegree, selected[2] / metersPerDegree]
            ]
            owner.viewer.send(["type": "focusGeoBounds", "bounds": geoBounds,
                               "sceneId": owner.sceneID, "revision": owner.revision])
        }
        status.stringValue = localized("已选择地图范围")
    }

    private func export() {
        guard isCurrent, !picking, let bounds, owner.commandEnabled("随机特写") else { return }
        guard owner.activeExport == nil else {
            status.stringValue = localized("请先关闭当前导出窗口")
            return
        }
        owner.presentExport(format: "PNG", exportContext: "random_spot", entryBounds: bounds)
        window.close()
    }
}

extension InterfaceController {
    func showRandomSpot() {
        if let panel = randomSpot {
            panel.window.makeKeyAndOrderFront(nil)
            return
        }
        let panel = RandomSpotController(owner: self)
        randomSpot = panel
        panel.show()
    }
}
