import AppKit
import WebKit

@MainActor extension NativeSmoke {
    func inspectCamera() async throws {
        let result = try await owner.viewer.view.callAsyncJavaScript("""
        const camera = window.__CSL_NATIVE_CAMERA__;
        const button = document.querySelector('.preview-3d-toggle');
        if (typeof camera !== 'function' || !button || button.disabled)
            throw new Error('Camera probe requires the debug viewer and an available 3D button');
        const pause = () => new Promise(resolve => {
            const timer = setTimeout(resolve, 50);
            requestAnimationFrame(() => { clearTimeout(timer); resolve(); });
        });
        const settle = async (target) => {
            const deadline = performance.now() + 5000;
            let state = camera(), intermediate = false;
            while (performance.now() < deadline) {
                state = camera();
                if (state.pitch > 0.001 && state.pitch < 51.999) intermediate = true;
                if (!state.moving && Math.abs(state.pitch - target) < 0.001) return {state, intermediate};
                await pause();
            }
            throw new Error('Camera failed to settle: ' + JSON.stringify(state));
        };
        await window.cslPreview.dispatch({type: 'fitBounds'});
        const before = (await settle(0)).state;
        button.click();
        const raised = await settle(52);
        const pressed = button.getAttribute('aria-pressed');
        button.click();
        const lowered = await settle(0);
        return {before, raised, lowered, pressed, finalPressed: button.getAttribute('aria-pressed'),
                reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches};
        """, arguments: [:], in: nil, contentWorld: .page)
        let model = object(result)
        try JSONSerialization.data(withJSONObject: model, options: [.prettyPrinted, .sortedKeys]).write(to: output.appendingPathComponent("camera.json"))
        let raised = object(object(model["raised"])["state"])
        let lowered = object(object(model["lowered"])["state"])
        let before = object(model["before"])
        try requireLayout(raised["enabled"] as? Bool == true && model["pressed"] as? String == "true", "3D camera enabled")
        try requireLayout(lowered["enabled"] as? Bool == false && model["finalPressed"] as? String == "false", "2D camera restored")
        try requireLayout(abs((raised["pitch"] as? Double ?? -1) - 52) < 0.001 && abs(lowered["pitch"] as? Double ?? -1) < 0.001, "actual camera pitch is 52° then 0°")
        try requireLayout(abs((before["bearing"] as? Double ?? -1000) - (lowered["bearing"] as? Double ?? 1000)) < 0.001, "2D camera bearing restored")
        try requireLayout(lowered["extrusion"] as? Bool == false, "3D extrusion removed in 2D")
        checks.append("webkit-actual-camera-2d-3d-roundtrip")
    }

    func inspectMapToolbar() async throws {
        let window = owner.window!
        let size = window.contentRect(forFrameRect: window.frame).size
        window.setContentSize(NSSize(width: 1100, height: 700))
        defer { window.setContentSize(size) }
        try await settleLayout()

        let controlsWork = try await owner.viewer.view.evaluateJavaScript("""
        (() => {
            const search = document.querySelector('.preview-search input');
            const collapse = document.querySelector('.preview-collapse-toggle');
            const searchTab = document.querySelector('.preview-edge-search');
            const viewTab = document.querySelector('.preview-edge-view');
            const scale = document.querySelector('.maplibregl-ctrl-scale');
            const rotation = Array.from(document.querySelectorAll('.preview-rotation button:enabled'));
            const zoom = Array.from(document.querySelectorAll('[data-preview-icon="zoom-in"], [data-preview-icon="zoom-out"]'));
            if (rotation.length !== 2 || zoom.length !== 2 || !collapse || !search || !searchTab || !viewTab || !scale) return false;
            const [upper, lower] = rotation.map(button => button.getBoundingClientRect());
            if (Math.abs(upper.left - 12) > 2 || upper.left !== lower.left || upper.right !== lower.right
                || upper.bottom >= lower.top || lower.bottom >= scale.getBoundingClientRect().top
                || rotation.some(button => !button.textContent.includes('90°'))) return false;
            search.focus();
            collapse.click();
            if (search.getClientRects().length || rotation[0].getClientRects().length || document.activeElement !== collapse) return false;
            searchTab.click();
            if (!search.getClientRects().length) return false;
            viewTab.click();
            if ([...zoom, ...rotation].some(button => !button.getClientRects().length)) return false;
            collapse.click();
            return search.getClientRects().length > 0 && rotation.every(button => button.getClientRects().length > 0);
        })()
        """) as? Bool ?? false
        try requireLayout(controlsWork && owner.mapPlaceholder.state == .ready, "map toolbar collapse preserves the preview and restores focus")
        checks.append("webkit-narrow-toolbar-collapse-focus-and-zoom-buttons")

        let original = object(owner.snapshot["output"])["rotation"] as? Int ?? 0
        for (index, expected) in [(1, (original + 180) % 360), (0, original)] {
            _ = try await owner.viewer.view.evaluateJavaScript("""
            (() => { const button = document.querySelectorAll('.preview-rotation button')[\(index)]; button.click(); button.click(); return true; })()
            """)
            try await Task.sleep(nanoseconds: 100_000_000)
            try await settleSettingsWrites()
            try requireLayout(object(owner.snapshot["output"])["rotation"] as? Int == expected, "map rotation applies both clicks in order")
        }
        checks.append("webkit-map-toolbar-rotation-roundtrip")
    }

    func inspectPreviewIntegration() async throws {
        try await inspectMapToolbar()
        let scene = owner.previewIdentity
        var frame: JSON = ["sceneId":scene.scene, "revision":scene.revision, "type":"sceneLoaded",
            "completion":"complete", "performance":["completion":"idle"]]
        try requireLayout(scene.isPresented(by:frame), "idle sceneLoaded releases pending artwork")
        frame["performance"] = ["completion":"timeout"]
        try requireLayout(!scene.isPresented(by:frame), "timeout waits for the late idle event")
        frame["type"] = "scenePresentationReady"
        try requireLayout(scene.isPresented(by:frame), "late idle releases pending artwork")
        frame["revision"] = scene.revision - 1
        try requireLayout(!scene.isPresented(by:frame), "previous revision cannot release pending artwork")
        checks.append("artwork-scene-ready-normal-timeout-and-stale-revision")
        let saved = owner.snapshot
        try requireLayout(!owner.themes.isEmpty && owner.themes.map { $0.id } == array(saved["themes"]).map { textValue($0["id"]) }, "theme catalog comes from engine")
        var expanded = saved
        expanded["themes"] = array(saved["themes"]) + [["id": "modern", "name": "轻简现代"], ["id": "urban_white", "name": "城市白图"], ["id": "personal-test", "name": "个人主题"]]
        expanded["theme"] = "modern"
        owner.applySnapshot(expanded)
        try requireLayout(owner.currentTheme?.id == "modern" && owner.currentTheme?.thumbnail != nil, "renamed theme can use existing thumbnail")
        try openThemeGallery()
        try await settleLayout()
        func themeButtons(_ view: NSView) -> [NSButton] {
            let own = (view as? NSButton).map { $0.identifier?.rawValue.hasPrefix("theme:") == true ? [$0] : [] } ?? []
            return own + view.subviews.flatMap(themeButtons)
        }
        let cards = themeButtons(owner.gallery!.contentViewController!.view)
        try requireLayout(cards.count == array(expanded["themes"]).count, "theme gallery displays every entry")
        try requireLayout(cards.first { $0.identifier?.rawValue == "theme:modern" }?.state == .on, "theme gallery selection uses engine id")
        try requireLayout(owner.gallery!.contentSize.height > 200 && owner.gallery!.contentSize.height <= 406, "theme gallery has a bounded scroll area")
        owner.gallery?.close(); owner.applySnapshot(saved)
        checks.append("dynamic-theme-catalog-gallery-and-id-selection")

        _ = try await owner.viewer.view.evaluateJavaScript("""
        window.__nativePreviewStates = [];
        window.__nativePreviewDispatch = window.cslPreview.dispatch;
        window.cslPreview.dispatch = async command => {
          if (command.type === 'setPreviewUiState') window.__nativePreviewStates.push(JSON.parse(JSON.stringify(command)));
          if (window.__holdLayerPaint && command.type === 'setLayerVisibility' && command.layer === 'watermark')
            await new Promise(resolve => { window.__releaseLayerPaint = resolve; });
          return window.__nativePreviewDispatch(command);
        }; null
        """)
        let layer = "watermark", previous = object(saved["layers"])["watermark"] as? Bool ?? false
        func post(_ visible: Bool, revision: Int? = nil, key: String = "watermark") async throws {
            let frame: JSON = ["type": "requestLayerVisibility", "sceneId": owner.sceneID, "revision": revision ?? owner.revision, "layer": key, "visible": visible]
            _ = try await owner.viewer.view.evaluateJavaScript("window.webkit.messageHandlers.cslPreview.postMessage(\(try jsonString(frame))); null")
            try await Task.sleep(nanoseconds: 100_000_000)
            try await settleSettingsWrites()
        }
        _ = try await owner.viewer.view.evaluateJavaScript("window.__holdLayerPaint = true; null")
        let refresh = Task { try await post(!previous) }
        var waitingForPaint = false
        for _ in 0..<200 {
            waitingForPaint = try await owner.viewer.view.evaluateJavaScript("typeof window.__releaseLayerPaint === 'function'") as? Bool ?? false
            if waitingForPaint { break }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        let spinnerWhileWaiting = !owner.statusSpinner.isHidden && owner.fieldWriting
        _ = try await owner.viewer.view.evaluateJavaScript("window.__holdLayerPaint = false; window.__releaseLayerPaint?.(); delete window.__releaseLayerPaint; null")
        try await refresh.value
        try requireLayout(waitingForPaint && spinnerWhileWaiting && owner.statusSpinner.isHidden,
                          "layer spinner stays visible until WebKit applies the change")
        checks.append("webkit-layer-refresh-progress")
        try requireLayout(object(owner.snapshot["layers"])[layer] as? Bool == !previous, "web layer request updates native snapshot")
        let states = array(try await owner.viewer.view.evaluateJavaScript("window.__nativePreviewStates"))
        try requireLayout(states.contains { ($0["pendingLayers"] as? [String] ?? []).contains(layer) }, "web layer request receives pending acknowledgement")
        try requireLayout(owner.previewLayerWrites.isEmpty && (states.last?["pendingLayers"] as? [String]) == [], "pending state clears after snapshot acknowledgement")
        try requireLayout(object(states.last?["layers"])[layer] as? Bool == !previous, "web acknowledgement contains committed layer state")
        try await post(previous, revision: owner.revision - 1)
        try requireLayout(object(owner.snapshot["layers"])[layer] as? Bool == !previous, "old scene revision cannot modify layers")
        owner.context = "transit"
        try await post(previous)
        try requireLayout(object(owner.snapshot["layers"])[layer] as? Bool == !previous, "transit view rejects map layer shortcuts")
        owner.context = "map"
        try await post(previous, key: "unknown-layer")
        try requireLayout(owner.previewLayerWrites.isEmpty, "unknown layer does not enter the write queue")
        let oldRoadLabels = owner.defaultVisibility(MapSymbolCategory.roadText.title)
        let oldDistrictLabels = owner.defaultVisibility(MapSymbolCategory.districtText.title)
        try await post(!oldRoadLabels, key: "road_text")
        try requireLayout(owner.defaultVisibility(MapSymbolCategory.roadText.title) != oldRoadLabels
                          && owner.defaultVisibility(MapSymbolCategory.districtText.title) == oldDistrictLabels,
                          "quick layer request changes only road labels")
        try requireLayout(owner.previewLayerWrites.isEmpty, "quick label pending state clears")
        try await post(false, key: "label")
        try requireLayout(object(owner.snapshot["layers"])["label"] as? Bool == true, "quick layers cannot change the legacy label master")
        try await post(oldRoadLabels, key: "road_text")

        owner.fieldWriting = true
        owner.requestPreviewLayer(["sceneId": owner.sceneID, "revision": owner.revision, "layer": layer, "visible": previous])
        owner.requestPreviewLayer(["sceneId": owner.sceneID, "revision": owner.revision, "layer": layer, "visible": previous])
        try requireLayout(owner.fieldQueue.count == 1, "duplicate layer clicks queue one write")
        owner.revision += 1; owner.fieldWriting = false; owner.drainFieldQueue()
        try await settleSettingsWrites()
        owner.revision -= 1
        try requireLayout(object(owner.snapshot["layers"])[layer] as? Bool == !previous && owner.previewLayerWrites.isEmpty, "scene change discards queued layer write")
        try await post(previous)
        try requireLayout(object(owner.snapshot["layers"])[layer] as? Bool == previous, "layer can be restored after rejected stale request")
        _ = try await owner.viewer.view.evaluateJavaScript("window.cslPreview.dispatch = window.__nativePreviewDispatch; delete window.__nativePreviewDispatch; delete window.__nativePreviewStates; null")
        checks.append("webkit-layer-bridge-pending-state-and-stale-scene-guards")
    }
}
