// 将预览器快捷操作接入宿主设置与首次引导。
using System.Text.Json;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly Dictionary<string, (string Scene, int Revision)> _previewLayerWrites = new();
    private bool _mapInteractionGuideSeen, _mapGuideOpen, _mapGuideOffered;
    private bool _draftWelcomeSeen, _draftWelcomeOpen;
    private (string Scene, int Revision)? _presentedGuideScene, _appliedGuideScene;

    private void SendPreviewUiState()
    {
        if (!_viewerReady || _snapshot.ValueKind != JsonValueKind.Object) return;
        SendViewer(new { type = "setPreviewUiState", layers = _snapshot.GetProperty("layers"),
            immersive = _immersive,
            enabled = _documentEnabled && _context == "map", guideAvailable = !_draftWelcomeOpen && _simplifiedRibbonIntro?.IsOpen != true,
            pendingLayers = _previewLayerWrites.Where(item => item.Value == (_sceneId, _revision)).Select(item => item.Key).ToArray(),
            sceneId = _sceneId, revision = _revision });
    }

    private async Task RequestPreviewLayerAsync(JsonElement frame)
    {
        var instance = _previewInstance;
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
        bool Current() => IsCurrentPreview(instance) && generation == _documentGeneration && scene == _sceneId && revision == _revision;
        try { await QueueLayerActionAsync(() => SetLayerVisibleAsync(layer, visible, instance), Current, instance); }
        finally
        {
            if (IsCurrentPreview(instance))
            {
                if (_previewLayerWrites.TryGetValue(layer, out pending) && pending == identity) _previewLayerWrites.Remove(layer);
                SendPreviewUiState();
            }
        }
    }

    private async Task ApplyPresentedSceneStateAsync(string scene, int revision)
    {
        var instance = _previewInstance;
        await ApplySceneStateAsync();
        if (!IsCurrentPreview(instance) || scene != _sceneId || revision != _revision) return;
        _appliedGuideScene = (scene, revision);
        SendPreviewUiState();
        TryShowMapInteractionGuide();
    }

    private void TryShowMapInteractionGuide()
    {
        if (_mapGuideOpen || _draftWelcomeOpen || _openingArtwork || _cityArtwork || _closing || !_viewerReady ||
            !_documentEnabled || _context == "metro" || _mapTask.Active || _engineBusy || _engineUnavailable ||
            OperationError.IsOpen && OperationError.Severity == InfoBarSeverity.Error || _simplifiedRibbonIntro?.IsOpen == true ||
            _presentedGuideScene != (_sceneId, _revision) || _appliedGuideScene != (_sceneId, _revision)) return;
        if (!_draftWelcomeSeen && CommandEnabled("map.edit_city", _commands["map.edit_city"]))
        {
            if (VisualTreeHelper.GetOpenPopupsForXamlRoot(Root.XamlRoot).FirstOrDefault() is { } popup)
            {
                popup.Closed -= RetryAfterPopup;
                popup.Closed += RetryAfterPopup;
                return;
            }
            _ = RunAsync(ShowDraftWelcomeAsync);
            return;
        }
        if (_mapInteractionGuideSeen || _mapGuideOffered || OperationError.IsOpen || MapReadNotice.IsOpen) return;
        _mapGuideOffered = _mapGuideOpen = true;
        SendViewer(new { type = "showMapInteractionGuide", sceneId = _sceneId, revision = _revision });
    }

    private void RetryAfterPopup(object? sender, object args)
    {
        ((Microsoft.UI.Xaml.Controls.Primitives.Popup)sender!).Closed -= RetryAfterPopup;
        Root.DispatcherQueue.TryEnqueue(TryShowMapInteractionGuide);
    }

    private async Task ShowDraftWelcomeAsync()
    {
        var generation = _documentGeneration;
        _draftWelcomeOpen = true;
        try
        {
            SendPreviewUiState();
            var dialog = new DraftWelcomeDialog { XamlRoot = Root.XamlRoot, RequestedTheme = Root.ActualTheme };
            var choice = await dialog.ShowAsync();
            if (_closing || _allowMainClose || generation != _documentGeneration) return;
            _draftWelcomeSeen = true;
            // 关闭欢迎页后直接交还地图；本次会话仍可从地图帮助手动打开操作提示。
            _mapGuideOffered = true;
            UserData.MarkDraftWelcomeSeen(_ribbonLayoutDirectory);
            if (choice == ContentDialogResult.Primary) await ExecuteCommandAsync("map.edit_city");
        }
        finally
        {
            _draftWelcomeOpen = false;
            if (!_closing) SendPreviewUiState();
        }
    }
}
