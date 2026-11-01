// 将预览器快捷操作接入宿主设置与首次引导。
using System.Text.Json;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly Dictionary<string, (string Scene, int Revision)> _previewLayerWrites = new();
    private bool _mapInteractionGuideSeen, _mapGuideOpen, _mapGuideOffered;
    private (string Scene, int Revision)? _presentedGuideScene, _appliedGuideScene;

    private void SendPreviewUiState()
    {
        if (!_viewerReady || _snapshot.ValueKind != JsonValueKind.Object) return;
        SendViewer(new { type = "setPreviewUiState", layers = _snapshot.GetProperty("layers"),
            enabled = _documentEnabled && _context == "map", guideAvailable = _simplifiedRibbonIntro?.IsOpen != true,
            pendingLayers = _previewLayerWrites.Where(item => item.Value == (_sceneId, _revision)).Select(item => item.Key).ToArray(),
            sceneId = _sceneId, revision = _revision });
    }

    private async Task RequestPreviewLayerAsync(JsonElement frame)
    {
        var scene = frame.GetProperty("sceneId").GetString();
        var revision = frame.GetProperty("revision").GetInt32();
        if (scene != _sceneId || revision != _revision) return;
        var layer = frame.GetProperty("layer").GetString()!;
        var visible = frame.GetProperty("visible").GetBoolean();
        if (!_layerButtons.ContainsKey(layer)) throw new InvalidDataException($"未知图层：{layer}");
        if (!_documentEnabled || _context != "map") { SendPreviewUiState(); return; }
        var identity = (_sceneId, _revision);
        if (_previewLayerWrites.TryGetValue(layer, out var pending) && pending == identity) return;
        _previewLayerWrites[layer] = identity;
        SendPreviewUiState();
        var generation = _documentGeneration;
        bool Current() => !_closing && generation == _documentGeneration && scene == _sceneId && revision == _revision;
        try { await QueueLayerActionAsync(() => SetLayerVisibleAsync(layer, visible), Current); }
        finally
        {
            if (_previewLayerWrites.TryGetValue(layer, out pending) && pending == identity) _previewLayerWrites.Remove(layer);
            if (!_closing) SendPreviewUiState();
        }
    }

    private async Task ApplyPresentedSceneStateAsync(string scene, int revision)
    {
        await ApplySceneStateAsync();
        if (scene != _sceneId || revision != _revision) return;
        _appliedGuideScene = (scene, revision);
        SendPreviewUiState();
        TryShowMapInteractionGuide();
    }

    private void TryShowMapInteractionGuide()
    {
        if (_mapInteractionGuideSeen || _mapGuideOffered || _mapGuideOpen || _closing || !_viewerReady ||
            !_documentEnabled || _context == "metro" || _mapTask.Active || _engineBusy || _engineUnavailable ||
            OperationError.IsOpen || _simplifiedRibbonIntro?.IsOpen == true ||
            _presentedGuideScene != (_sceneId, _revision) || _appliedGuideScene != (_sceneId, _revision)) return;
        _mapGuideOffered = _mapGuideOpen = true;
        SendViewer(new { type = "showMapInteractionGuide", sceneId = _sceneId, revision = _revision });
    }
}
