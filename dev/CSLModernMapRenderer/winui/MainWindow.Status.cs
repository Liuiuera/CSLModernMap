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
        SetDocumentEnabled(false);
        ApplyMapTaskState();
        return generation;
    }

    private void ApplyMapTaskState()
    {
        var detail = _mapTask.Detail;
        MapOverlayDetail.Text = detail;
        MapOverlayDetail.Visibility = string.IsNullOrWhiteSpace(detail) ? Visibility.Collapsed : Visibility.Visible;
        var step = string.IsNullOrWhiteSpace(detail) ? _mapTask.Title : detail;
        StatusText.Text = _mapTask.Active
            ? (_mapTask.Background && !string.IsNullOrWhiteSpace(detail) ? _mapTask.Title + " · " : "") + step
                + (_mapTask.Percent is { } percent ? $" {percent:0}%" : "")
            : (_exportWindow?.IsBusy == true || _externalExporting)
                ? UiLanguage.Text("MainWindow_Status_ApplyMapTaskState_C8921F1516") : _mapTask.Status;
        PreviewLoadingNotice.Message = detail
            + (_mapTask.Percent is { } loadingPercent ? $"（{loadingPercent:0}%）" : "");
        StatusSpinner.IsActive = _mapTask.Active;
        StatusSpinner.Visibility = _mapTask.Active ? Visibility.Visible : Visibility.Collapsed;
        PreviewLoadingNotice.IsOpen = _mapTask.Active && _mapTask.Background;
        PreviewLoadingNotice.Title = UiLanguage.Text("MainWindow_Status_ApplyMapTaskState_75306678A9");
        MapOverlay.Visibility = _mapTask.ShowOverlay ? Visibility.Visible : Visibility.Collapsed;
        MapOverlayText.Text = _context == "metro" && !_mapTask.Active ? RmpText("EmptyWorkspace") : _mapTask.Title;
        MapOverlaySpinner.IsActive = _mapTask.Active && _mapTask.Percent is null;
        MapOverlaySpinner.Visibility = MapOverlaySpinner.IsActive ? Visibility.Visible : Visibility.Collapsed;
        Progress.Visibility = _mapTask.Active ? Visibility.Visible : Visibility.Collapsed;
        Progress.IsIndeterminate = _mapTask.Percent is null;
        Progress.Value = _mapTask.Percent ?? 0;
        MapOverlayCancel.Visibility = _mapTask.Active && _mapTask.CanCancel ? Visibility.Visible : Visibility.Collapsed;
        MapView.IsHitTestVisible = !_mapTask.Active || _mapTask.Background;
    }

    private void CompleteMapTask(int generation)
    {
        if (!_mapTask.Complete(generation)) return;
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
        if (_engineBusy) { ShowNotice(UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_2A55669135")); return; }
        var generation = BeginMapTask(MapTaskPhase.Generating, _context == "transit" ? UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_058E698B71") : UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_A8A3F1C48A"));
        var result = await _engine.RequestAsync("compilePreview", new { force = true });
        if (generation != _mapTask.Generation) return;
        if (!result.GetProperty("started").GetBoolean()) SetError(UiLanguage.Text("MainWindow_Status_CompilePreviewAsync_BA6C957739"));
    }
}
