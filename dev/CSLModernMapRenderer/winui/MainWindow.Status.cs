// 更新主窗口的状态与进度提示。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private readonly MapTaskState _mapTask = new();
    private bool _waitingSceneReady, _discardSceneResults, _externalExporting;

    private int BeginMapTask(MapTaskPhase phase, string title, bool canCancel = true, string? requestId = null, bool waitForScene = true, bool background = false)
    {
        _discardSceneResults = false;
        _waitingSceneReady = waitForScene;
        _awaitingScene = waitForScene;
        var generation = _mapTask.Begin(phase, title, canCancel, requestId, background);
        _previewWait?.Cancel();
        _fullSceneCompiled = _previewWaitDismissed = _previewSlow = false;
        SetDocumentEnabled(false);
        ApplyMapTaskState();
        return generation;
    }

    private void ApplyMapTaskState()
    {
        BrowseSettingsPanel.Visibility = _cityArtwork ? Visibility.Collapsed : Visibility.Visible;
        ArtworkSettingsPlaceholder.Visibility = _cityArtwork ? Visibility.Visible : Visibility.Collapsed;
        UpdateArtworkSaveState();
        ViewerToolbar.Visibility = _cityArtwork ? Visibility.Collapsed : Visibility.Visible;
        _artworkToolbar.Visibility = _cityArtwork ? Visibility.Visible : Visibility.Collapsed;
        var cityLoading = _cityArtworkUpdating || _artworkEntryPreparing;
        var cityPreparing = _artworkEntryPreparing || _cityArtworkUpdating && _cityArtworkOperation?.Kind == "initial";
        var cityTitle = EditorText(cityPreparing ? "LoadingEnter" : "LoadingUpdate");
        var detail = cityLoading ? CityArtworkProgressText() : _mapTask.Detail;
        MapOverlayDetail.Text = detail;
        MapOverlayDetail.Visibility = string.IsNullOrWhiteSpace(detail) ? Visibility.Collapsed : Visibility.Visible;
        var step = string.IsNullOrWhiteSpace(detail) ? _mapTask.Title : detail;
        StatusText.Text = cityLoading
            ? cityTitle + " · " + detail
            : _cityArtworkExporting ? UiLanguage.Text("Artwork_Export") + "…"
            : _mapTask.Active
            ? (_mapTask.Background && !string.IsNullOrWhiteSpace(detail) ? _mapTask.Title + " · " : "") + step
                + (_mapTask.Percent is { } percent ? $" {percent:0}%" : "")
            : (_exportWindow?.IsBusy == true || _externalExporting)
                ? UiLanguage.Text("MainWindow_Status_ApplyMapTaskState_C8921F1516")
                : _buildingListLoading ? UiLanguage.Text("BuildingList_Open") + " · " + UiLanguage.Text("MapTaskState_Status_D04FCBDA73") : _mapTask.Status;
        PreviewLoadingNotice.Message = detail
            + (!cityLoading && _mapTask.Percent is { } loadingPercent ? $"（{loadingPercent:0}%）" : "");
        StatusSpinner.IsActive = _mapTask.Active || _buildingListLoading || cityLoading || _cityArtworkExporting;
        StatusSpinner.Visibility = StatusSpinner.IsActive ? Visibility.Visible : Visibility.Collapsed;
        PreviewLoadingNotice.IsOpen = cityLoading && !cityPreparing || _mapTask.Active && _mapTask.Background;
        PreviewLoadingNotice.Title = cityLoading ? cityTitle : UiLanguage.Text("MainWindow_Status_ApplyMapTaskState_75306678A9");
        PreviewLoadingNotice.Severity = cityLoading ? InfoBarSeverity.Informational : InfoBarSeverity.Warning;
        MapOverlay.Visibility = cityPreparing || _mapTask.ShowOverlay ? Visibility.Visible : Visibility.Collapsed;
        MapOverlayText.Text = cityPreparing ? cityTitle :
            _context == "metro" && !_mapTask.Active ? RmpText("EmptyWorkspace") : _mapTask.Title;
        var percentValue = cityLoading ? _cityArtworkOperation?.Percent : _mapTask.Percent;
        MapOverlaySpinner.IsActive = cityPreparing && percentValue is null || !cityLoading && _mapTask.Active && percentValue is null;
        MapOverlaySpinner.Visibility = MapOverlaySpinner.IsActive ? Visibility.Visible : Visibility.Collapsed;
        Progress.Visibility = cityLoading || _mapTask.Active ? Visibility.Visible : Visibility.Collapsed;
        Progress.IsIndeterminate = percentValue is null;
        Progress.Value = percentValue ?? 0;
        MapOverlayCancel.Visibility = _cityArtwork && cityPreparing || _mapTask.Active && _mapTask.CanCancel ? Visibility.Visible : Visibility.Collapsed;
        MapView.IsHitTestVisible = !cityPreparing && (!_mapTask.Active || _mapTask.Background);
        ApplyPreviewStatus();
        TryShowSimplifiedRibbonIntro();
        TryShowMapInteractionGuide();
    }

    private void CompleteMapTask(int generation)
    {
        if (!_mapTask.Complete(generation)) return;
        _previewWait?.Cancel();
        _previewSlow = false;
        _fullSceneCompiled = false;
        _awaitingScene = _waitingSceneReady = false;
        SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object && _snapshot.GetProperty("hasDocument").GetBoolean() && !_engineBusy);
        ApplyMapTaskState();
    }

    private void ShowNotice(string message)
    {
        OperationError.Severity = InfoBarSeverity.Informational;
        OperationError.Message = message;
        OperationError.IsOpen = true;
    }

    private void UpdateMapProgress(JsonElement payload)
    {
        if (_exportWindow?.IsBusy == true || _externalExporting) return;
        var requestId = payload.TryGetProperty("requestId", out var id) && id.ValueKind == JsonValueKind.String ? id.GetString() : null;
        double? percent = payload.TryGetProperty("percent", out var raw) && raw.TryGetDouble(out var value) ? value : null;
        if (_mapTask.Progress(percent, payload.GetProperty("message").GetString() ?? "", requestId)) ApplyMapTaskState();
    }

    private async Task CompilePreviewAsync()
    {
        if (_compatibilityPreview) { QueueCompatibilityPreview(); return; }
        if (!_viewerReady) { await RetryPreviewAsync(); return; }
        if (_engineBusy) { ShowNotice(UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_2A55669135")); return; }
        var generation = BeginMapTask(MapTaskPhase.Generating, _context == "transit" ? UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_058E698B71") : UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_A8A3F1C48A"));
        var result = await _engine.RequestAsync("compilePreview", new { force = true });
        if (generation != _mapTask.Generation) return;
        if (!result.GetProperty("started").GetBoolean()) SetError(UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_BA6C957739"));
    }
}
