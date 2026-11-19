// 兼容预览复用 PNG 渲染，连续编辑只提交最新设置。
using System.Text.Json;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private bool _compatibilityPreview, _compatibilityPending, _compatibilityRendering;
    private string? _compatibilityRequestId, _compatibilityActiveRequest, _compatibilityContext;
    private CancellationTokenSource? _compatibilityDelay;
    private bool _compatibilityDelayReady;

    private async Task EnableCompatibilityPreviewAsync()
    {
        _compatibilityPreview = true;
        await StopPreviewAsync();
        _previewFailure = null;
        OperationError.IsOpen = false;
        _selectedBounds = null;
        _selectedRegionSource = null;
        ShowInspectorHome();
        QueueCompatibilityPreview();
        await RefreshSnapshotAsync();
    }

    private void ResetCompatibilityImage()
    {
        _compatibilityDelay?.Cancel();
        _compatibilityPending = false;
        _compatibilityRequestId = null;
        _compatibilityContext = null;
        CompatibilityImage.Source = null;
        CompatibilitySurface.ChangeView(0, 0, 1);
    }

    private void QueueCompatibilityPreview()
    {
        if (!_compatibilityPreview || _closing) return;
        _compatibilityContext = _context;
        _compatibilityRequestId = Guid.NewGuid().ToString("N");
        _compatibilityDelayReady = false;
        _compatibilityPending = _context != "metro";
        _compatibilityDelay?.Cancel();
        _compatibilityDelay?.Dispose();
        _compatibilityDelay = new CancellationTokenSource();
        _ = DebounceCompatibilityPreviewAsync(_compatibilityDelay.Token);
        ApplyPreviewStatus();
    }

    private async Task DebounceCompatibilityPreviewAsync(CancellationToken cancellation)
    {
        try
        {
            await Task.Delay(350, cancellation);
            _compatibilityDelayReady = true;
            await RunAsync(StartCompatibilityPreviewAsync);
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
    }

    private async Task StartCompatibilityPreviewAsync()
    {
        if (!_compatibilityPreview || !_compatibilityPending || !_compatibilityDelayReady || _compatibilityRendering ||
            _engineBusy || _engineUnavailable || _closing || _loadingDocument || _context == "metro") return;
        if (_snapshot.ValueKind != JsonValueKind.Object || !_snapshot.GetProperty("hasDocument").GetBoolean()) return;
        var request = _compatibilityRequestId;
        _compatibilityActiveRequest = request;
        _compatibilityPending = false;
        _compatibilityRendering = true;
        ApplyPreviewStatus();
        try
        {
            var result = await _engine.RequestAsync("renderCompatibilityPreview", new
            {
                requestId = request, documentEpoch = _engineDocumentEpoch,
                context = _context == "transit" ? "transit" : "normal",
            });
            if (!result.GetProperty("started").GetBoolean())
            {
                _compatibilityRendering = false;
                _compatibilityPending = true;
            }
        }
        catch (Exception error)
        {
            _compatibilityRendering = false;
            ShowCompatibilityError(error.Message);
        }
        ApplyPreviewStatus();
    }

    private async Task HandleCompatibilityPreviewAsync(string name, JsonElement payload)
    {
        var request = payload.GetProperty("requestId").GetString();
        if (request == _compatibilityActiveRequest) _compatibilityRendering = false;
        bool Current() => !_closing && _compatibilityPreview && request == _compatibilityRequestId &&
            payload.GetProperty("documentEpoch").GetInt32() == _engineDocumentEpoch && _context != "metro";
        try
        {
            if (name == "compatibilityPreviewReady")
            {
                if (Current())
                {
                    var bitmap = await PreviewImages.LoadAsync(payload.GetProperty("url").GetString()!);
                    if (Current()) CompatibilityImage.Source = bitmap;
                }
            }
            else if (name == "compatibilityPreviewFailed" && Current())
                ShowCompatibilityError(payload.GetProperty("message").GetString()!);
        }
        catch (Exception error) { if (Current()) ShowCompatibilityError(error.Message); }
        finally
        {
            if (name == "compatibilityPreviewReady" && !_closing && !_engineUnavailable)
                await _engine.RequestAsync("releaseCompatibilityPreview", new { requestId = request });
        }
        ApplyPreviewStatus();
        if (_compatibilityPending) await StartCompatibilityPreviewAsync();
    }

    private void ShowCompatibilityError(string message)
    {
        OperationError.Severity = InfoBarSeverity.Error;
        OperationError.Message = UiLanguage.Text("Preview_ImageFailed") + " " + message;
        OperationError.IsOpen = true;
    }
}
