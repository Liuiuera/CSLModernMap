import AppKit

@MainActor extension NativeSmoke {
    private func waitForDraft(_ message: String, until: () -> Bool) async throws {
        let deadline = Date().addingTimeInterval(60)
        while Date() < deadline {
            if until() { return }
            await withCheckedContinuation { continuation in
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { continuation.resume() }
            }
        }
        throw AppFailure(message: message)
    }

    private func draftToolbarButtons() throws -> [NSButton] {
        guard let scroll = owner.band.subviews.first as? NSScrollView,
              let toolbar = scroll.documentView as? NSStackView else {
            throw AppFailure(message: "Draft toolbar missing")
        }
        let buttons = toolbar.arrangedSubviews.compactMap { $0 as? NSButton }
        try requireLayout(buttons.first?.identifier?.rawValue == "退出草稿", "exit is the leftmost draft action")
        for title in ["退出草稿", "草稿设置", "刷新草稿", "保存主题", "导出"] {
            let button = buttons.first { $0.identifier?.rawValue == title }
            try requireLayout(button?.title == localized(title) && button?.imagePosition != .imageOnly,
                              "draft toolbar has visible text for " + title)
        }
        try requireLayout(buttons.allSatisfy {
            !["适应全图", "适应画布", "100%", "放大", "缩小", "重置画布"].contains($0.identifier?.rawValue ?? "")
        }, "draft toolbar omits camera controls")
        return buttons
    }

    func inspectCityDrafts() async throws {
        try await waitForDraft("Interactive city preview was not ready") { owner.commandEnabled("创建地图草稿") }
        owner.leftItem.isCollapsed = true
        let theme = try await owner.engine.request("getThemeDraft")
        _ = try await owner.viewer.view.evaluateJavaScript("""
            window.__draftProgressDispatch = window.cslPreview.dispatch;
            window.__holdDraft = true;
            window.cslPreview.dispatch = async command => {
                if (window.__holdDraft && command.type === 'loadArtwork')
                    await new Promise(resolve => { window.__showDraft = resolve; });
                return window.__draftProgressDispatch(command);
            }; null
            """)
        try await owner.startCityDraft(theme: theme, options: [
            "longEdge": 512, "elementScale": 0.125, "rotation": 90,
            "region": ["source": "custom", "bounds": [-4000.0, -2000.0, 4000.0, 2000.0]]
        ])
        try await waitForDraft("Draft did not show progress while waiting for the canvas") {
            owner.cityDraft?.creating == true && !owner.statusSpinner.isHidden
                && owner.status.stringValue.contains(localized("正在显示草稿"))
        }
        for _ in 0..<200 {
            let delivered = try await owner.viewer.view.evaluateJavaScript("typeof window.__showDraft === 'function'") as? Bool ?? false
            if delivered { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        _ = try await owner.viewer.view.evaluateJavaScript("""
            window.__holdDraft = false;
            window.__showDraft?.(); delete window.__showDraft; null
            """)
        try await waitForDraft("Ordinary draft did not reach the canvas") { (owner.cityDraft?.presented ?? 0) > 0 }
        try requireLayout(owner.status.stringValue == localized("草稿已创建") && owner.statusSpinner.isHidden,
                          "draft progress ends after the canvas confirms delivery")
        checks.append("native-draft-creation-progress")
        guard let draft = owner.cityDraft else { throw AppFailure(message: "Ordinary draft missing") }
        let args: JSON = ["documentEpoch": owner.epoch]
        let visibleRevision = draft.presented
        let mapTools = try draftToolbarButtons()
        _ = try await owner.viewer.view.evaluateJavaScript("window.__holdDraft = true; null")
        mapTools.first { $0.identifier?.rawValue == "刷新草稿" }?.performClick(nil)
        try await waitForDraft("Draft refresh did not reach delivery") {
            draft.refreshing && !owner.statusSpinner.isHidden && owner.status.stringValue.contains(localized("正在显示草稿"))
        }
        _ = try await owner.engine.request("restoreCityArtworkPreview", args)
        let stayedInDraft = try await owner.viewer.view.evaluateJavaScript("""
            (() => {
                const state = window.cslPreview.artworkState();
                return state.active && state.artwork?.revision === \(visibleRevision)
                    && document.body.classList.contains('artwork-active');
            })()
            """) as? Bool ?? false
        _ = try await owner.viewer.view.evaluateJavaScript("""
            window.__holdDraft = false;
            window.cslPreview.dispatch = window.__draftProgressDispatch;
            window.__showDraft?.(); delete window.__showDraft; null
            """)
        try requireLayout(stayedInDraft, "refreshing the scene retains the previous visible draft")
        try await waitForDraft("Refreshed draft did not replace the visible canvas") {
            draft.presented > visibleRevision && !draft.refreshing && owner.statusSpinner.isHidden
        }
        checks.append("native-draft-refresh-preserves-canvas")
        var snapshot = try await owner.engine.request("getCityArtworkState", args)
        let canvas = object(snapshot["config"])
        try requireLayout(canvas["kind"] as? String == "normal" && canvas["rotation"] as? Int == 90
            && canvas["longEdge"] as? Int == 512 && canvas["elementScale"] as? Double == 0.125
            && object(canvas["region"])["source"] as? String == "custom", "normal draft rectangle, rotation, preset and linked scale")
        checks.append("native-normal-draft-custom-geometry")

        guard let field = draft.editor.form.fields.first(where: { $0.view.identifier?.rawValue == "description" }),
              let input = field.view as? NSTextField else { throw AppFailure(message: "Draft text field missing") }
        let original = textValue(draft.editor.data["description"])
        for text in ["first draft text", "final draft text"] {
            input.stringValue = text
            field.controlTextDidChange(Notification(name: NSControl.textDidChangeNotification, object: input))
        }
        try await waitForDraft("Final focused input was not presented") {
            textValue(draft.editor.data["description"]) == "final draft text" && draft.presented > 0 && draft.revision > (snapshot["revision"] as? Int ?? 0)
        }
        try await waitForDraft("Draft undo did not become available after rendering") { owner.commandEnabled("撤销") }
        owner.execute("撤销")
        try requireLayout(textValue(draft.editor.data["description"]) == original && owner.commandEnabled("重做"), "draft undo and redo")
        owner.execute("重做")
        try requireLayout(textValue(draft.editor.data["description"]) == "final draft text", "draft redo restores edit")
        checks.append("native-last-focused-input-and-undo-redo")

        owner.execute("撤销")
        try requireLayout(try await draft.finish(leaving: true) && owner.leftItem.isCollapsed, "ordinary draft restores its original sidebar")
        checks.append("native-return-to-original-map")
        owner.enterTransit()
        try await waitForDraft("Transit map did not open") { owner.context == "transit" && owner.transitForm != nil }
        let transitTheme = try await owner.engine.request("getThemeDraft")
        try await owner.startCityDraft(theme: transitTheme, options: ["longEdge": 512])
        try await waitForDraft("Transit draft did not reach the canvas") { (owner.cityDraft?.presented ?? 0) > 0 }
        guard let transit = owner.cityDraft else { throw AppFailure(message: "Transit draft missing") }
        _ = try draftToolbarButtons()
        checks.append("native-draft-toolbar-visible-actions-and-order")
        snapshot = try await owner.engine.request("getCityArtworkState", args)
        try requireLayout(object(snapshot["config"])["kind"] as? String == "transit", "directly created public transit draft")
        let originalTheme = owner.snapshot["theme"] as? String
        let transitRevision = transit.presented
        _ = try await owner.viewer.view.evaluateJavaScript("""
            window.cslPreview.dispatch = async command => {
                if (command.type === 'loadArtwork')
                    await new Promise(resolve => { window.__showTransitDraft = resolve; });
                return window.__draftProgressDispatch(command);
            }; null
            """)
        for value in [20, 23] {
            owner.update("setTransitOption", ["draft": true, "key": "line_label_size", "value": value])
        }
        try await waitForDraft("Transit refresh did not show progress") {
            transit.refreshing && !owner.statusSpinner.isHidden
                && owner.status.stringValue.contains(localized("正在显示草稿"))
        }
        var transitDeliveryWaiting = false
        for _ in 0..<200 {
            transitDeliveryWaiting = try await owner.viewer.view.evaluateJavaScript("typeof window.__showTransitDraft === 'function'") as? Bool ?? false
            if transitDeliveryWaiting { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let previousTransitVisible = try await owner.viewer.view.evaluateJavaScript("""
            (() => {
                const state = window.cslPreview.artworkState();
                return state.active && state.artwork?.revision === \(transitRevision);
            })()
            """) as? Bool ?? false
        transit.save()
        _ = try await owner.viewer.view.evaluateJavaScript("""
            window.cslPreview.dispatch = window.__draftProgressDispatch;
            window.__showTransitDraft?.(); delete window.__showTransitDraft; null
            """)
        try requireLayout(transitDeliveryWaiting && previousTransitVisible, "transit refresh retains the previous drawing until delivery")
        try await waitForDraft("Transit refresh did not present the new drawing") {
            transit.presented > transitRevision && !transit.refreshing && owner.statusSpinner.isHidden
        }
        checks.append("native-transit-draft-refresh-preserves-canvas")
        try await waitForDraft("Save did not drain and commit final transit value") {
            !transit.finishing && !owner.fieldWriting && owner.snapshot["transitDraftChanged"] as? Bool == false
                && object(owner.snapshot["transitOptions"])["line_label_size"] as? Double == 23
        }
        try requireLayout(try await transit.finish(leaving: true), "saved transit draft exits without confirmation")
        let state = try await owner.engine.request("getSnapshot")
        try requireLayout(owner.context == "transit" && state["theme"] as? String == originalTheme
            && object(state["transitOptions"])["line_label_size"] as? Double == 23, "return to transit with the saved final value")
        checks.append("native-direct-transit-draft-and-queued-transit-save")
    }
}
