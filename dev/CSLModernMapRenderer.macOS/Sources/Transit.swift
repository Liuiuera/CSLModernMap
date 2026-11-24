import AppKit

extension InterfaceController {
    func enterTransit() { run { try await self.switchMapContext("transit") } }
    func returnToMap() { run { try await self.switchMapContext("map") } }
    func switchMapContext(_ target: String) async throws {
        guard context != target, !busy, !fieldWriting, !rmp.busy, !rmp.placing, closeEditors() else { return }
        if context == "metro" {
            try await rmp.focusEditor()
            guard context == "metro", !busy, !rmp.busy, !rmp.placing else { return }
        }
        rememberSidebarPosition()
        sidebarNavigation.requestID = UUID()
        context = target; selectedLayer = nil
        rmp.hide()
        staticPreview.isHidden = false
        viewer.view.isHidden = staticMode && staticPreview.superview != nil
        rebuildBand()
        if target == "transit" { try await loadTransitSettings() }
        else {
            try await refreshSnapshot()
            guard context == target else { return }
            restoreMapSidebar(); buildInspector()
        }
        guard context == target else { return }
        try await refreshMapPreview()
    }
    func enterMetro(){run{try await self.openMetro()}}
    func openMetro() async throws {
        guard !busy, !fieldWriting, !rmp.busy, !rmp.placing, closeEditors() else { return }
        staticPreview.isHidden = true
        rememberSidebarPosition();sidebarNavigation.requestID=UUID();context="metro";selectedLayer=nil;rebuildBand();try await loadTransitSettings()
        try await rmp.initialize()
        if !rmp.hasProject && canGenerateMetro() { try await rmp.generate() }
        else { rmp.show() }
    }
    func bandContent(_ views: [NSView]) {
        let scroll = NSScrollView(); scroll.drawsBackground = false; scroll.hasHorizontalScroller = true
        scroll.scrollerStyle = .overlay; scroll.autohidesScrollers = true
        let contents = row(views, gap: 14)
        contents.edgeInsets = NSEdgeInsets(top: 8, left: 16, bottom: 8, right: 16)
        scroll.documentView = contents; pin(scroll, in: band)
        NSLayoutConstraint.activate([
            contents.heightAnchor.constraint(equalToConstant: 60),
            contents.leadingAnchor.constraint(equalTo: scroll.contentView.leadingAnchor),
            contents.topAnchor.constraint(equalTo: scroll.contentView.topAnchor)
        ])
        for view in views { view.setContentCompressionResistancePriority(.required, for: .horizontal) }
        updateCommands()
    }
    func rebuildTransitBand() {
        let back = ActionButton("返回地图") { [weak self] in self?.returnToMap() }
        let settings = ActionButton("交通设置") { [weak self] in self?.showTransitSettings() }
        let icons = ActionButton("图标") { [weak self] in self?.showTextIconEditor(category: .icons) }
        let text = ActionButton("文字") { [weak self] in self?.showTextIconEditor() }
        let export = ActionButton("导出…") { [weak self] in self?.execute("导出…") }
        for (button, icon) in [(back, "arrow.backward"), (settings, "slider.horizontal.3"), (text, "textformat"), (icons, "mappin"), (export, "square.and.arrow.up")] {
            button.image = symbol(icon, size: 14); button.imagePosition = .imageLeading
            button.font = .systemFont(ofSize: 13); button.toolTip = button.title
        }
        bandContent([back, settings, text, icons, ActionButton("创建地图草稿") { [weak self] in self?.beginDraft() }, export])
    }
    func showTransitSettings() {
        leftItem.isCollapsed = false; syncSidebarVisibility()
        run { try await self.loadTransitSettings() }
    }
    func showLines(metro: Bool) {
        let target = metro ? "metro" : "transit", generation = epoch
        let identifier = NSUserInterfaceItemIdentifier(target + ".lines.selector")
        func existing() -> NSWindow? { panels.first { $0.identifier == identifier && $0.isVisible } }
        if let panel = existing() { panel.makeKeyAndOrderFront(nil); return }
        guard context == target, engineReady, !busy, !fieldWriting, fieldQueue.isEmpty,
              !rmp.busy, snapshot["hasDocument"] as? Bool == true else { return }
        run { [self] in
            let draft = cityDraft
            let response = try await engine.request("getTransitLines", ["metro": metro, "draft": draft != nil])
            guard epoch == generation, context == target, engineReady, !busy, !rmp.busy,
                  !fieldWriting, fieldQueue.isEmpty,
                  let documentEpoch = response["documentEpoch"] as? Int, documentEpoch == generation else { return }
            // Recheck after the shared catalog request.
            if let panel = existing() { panel.makeKeyAndOrderFront(nil); return }
            let selector = TransitLineSelector(metro: metro, items: array(response["lines"]),
                selected: (response["lineIds"] as? [Int]).map(Set.init),
                types: array(snapshot["transitTypes"]), enabledTypes: Set(currentTransitOptions(metro)["types"] as? [String] ?? []))
            selector.window.identifier = identifier
            selector.unavailableReason = { [weak self] in
                guard let self = self, self.engineReady else { return localized("引擎未就绪，请稍后重试。") }
                if self.epoch != documentEpoch { return localized("城市已切换，请重新打开线路选择。") }
                if self.context != target || self.cityDraft !== draft { return localized("视图已切换，请重新打开线路选择。") }
                if self.busy || self.fieldWriting || !self.fieldQueue.isEmpty || self.rmp.busy {
                    return localized("请等待当前操作完成后再修改线路选择。")
                }
                return nil
            }
            selector.apply = { [weak self] selector in
                guard let self = self else { selector.window.saving = false; selector.refreshAvailability(); return }
                self.run {
                    defer { selector.window.saving = false; selector.refreshAvailability() }
                    if let reason = selector.unavailableReason?() { throw AppFailure(message: reason) }
                    let reservation = draft?.reserveTransitRefresh()
                    defer { if let reservation { draft?.clearUnusedTransitRefresh(reservation) } }
                    _ = try await self.engine.request("setTransitLines", ["metro": metro,
                        "documentEpoch": documentEpoch, "lineIds": selector.selection.payload, "draft": draft != nil])
                    selector.window.dirty = false; selector.window.saving = false; selector.window.close()
                    guard self.epoch == documentEpoch else { return }
                    try await self.refreshSnapshot()
                    guard !metro, self.epoch == documentEpoch, self.context == target else { return }
                    try await self.refreshMapPreview()
                }
            }
            selector.window.retained.append(selector)
            selector.window.onClose = { [weak self, weak selector] in
                guard let selector = selector else { return }
                self?.panels.removeAll { $0 === selector.window }
                selector.window.retained.removeAll()
            }
            panels.append(selector.window); selector.show()
        }
    }
}
