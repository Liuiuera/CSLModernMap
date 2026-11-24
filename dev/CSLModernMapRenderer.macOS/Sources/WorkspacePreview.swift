import AppKit

struct PreviewSceneIdentity: Equatable {
    let epoch: Int
    let scene: String
    let revision: Int

    func isPresented(by frame: JSON) -> Bool {
        guard !scene.isEmpty, frame["sceneId"] as? String == scene,
              frame["revision"] as? Int == revision else { return false }
        switch frame["type"] as? String {
        case "sceneLoaded": return object(frame["performance"])["completion"] as? String == "idle"
        case "scenePresentationReady": return true
        default: return false
        }
    }
}

struct FieldUpdate {
    let method: String
    let params: JSON
    let refresh: Bool
    let epoch: Int
    let previewScene: PreviewSceneIdentity?
    let context: String
}

extension InterfaceController {
    var previewIdentity: PreviewSceneIdentity { PreviewSceneIdentity(epoch: epoch, scene: sceneID, revision: revision) }
    private var previewControlsAvailable: Bool {
        cityDraft == nil && engineReady && !terminating && !busy && !sceneID.isEmpty && snapshot["hasDocument"] as? Bool == true
    }
    var previewControlsEnabled: Bool { context == "map" && previewControlsAvailable }
    var previewRotationEnabled: Bool {
        (context == "map" || context == "transit") && previewControlsAvailable && !staticMode && mapPlaceholder.state == .ready
    }

    func sendPreviewUiState() {
        guard viewer.ready else { return }
        let symbols = Dictionary(uniqueKeysWithValues: MapSymbolCategory.allCases.map {
            ($0.rawValue, defaultVisibility($0.title))
        })
        viewer.send(["type": "setPreviewUiState", "layers": viewerLayers(), "symbolLayers": symbols,
                     "enabled": previewControlsEnabled, "rotationEnabled": previewRotationEnabled, "guideAvailable": !sceneID.isEmpty,
                     "pendingLayers": previewLayerWrites.filter { $0.value == previewIdentity }.map { $0.key }.sorted(),
                     "sceneId": sceneID, "revision": revision])
    }

    func requestPreviewLayer(_ frame: JSON) {
        guard textValue(frame["sceneId"]) == sceneID, frame["revision"] as? Int == revision else { return }
        let layer = textValue(frame["layer"])
        let knownLayer = MapSymbolCategory(rawValue: layer) != nil || object(snapshot["layers"])[layer] is Bool
        guard layer != "label", knownLayer, let visible = frame["visible"] as? Bool else { sendPreviewUiState(); return }
        guard previewControlsEnabled else { sendPreviewUiState(); return }
        let identity = previewIdentity
        guard previewLayerWrites[layer] != identity else { return }
        previewLayerWrites[layer] = identity
        sendPreviewUiState()
        changeLayerVisibility(layer, enabled: visible, previewScene: identity)
    }

    func requestMapRotation(_ frame: JSON) {
        guard previewRotationEnabled,
              textValue(frame["sceneId"]) == sceneID, frame["revision"] as? Int == revision,
              let delta = frame["delta"] as? Int, delta == -90 || delta == 90 else { return }
        update("setOutputOption", ["key": "rotation", "rotationDelta": delta], refresh: false, previewScene: previewIdentity)
    }
}
