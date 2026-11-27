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

    private bool CurrentDocumentAvailable => _snapshot.ValueKind == JsonValueKind.Object &&
        _snapshot.GetProperty("documentEpoch").GetInt32() == _engineDocumentEpoch &&
        _snapshot.GetProperty("hasDocument").GetBoolean();

    private Task EnableCompatibilityPreviewAsync()
    {
        _compatibilityPreview = true;
        ResetPreview();
        _ = SafeRequestAsync("setPreviewEnabled", new { enabled = false });
        _selectedBounds = null;
        _selectedRegionSource = null;
        ShowInspectorHome();
        QueueCompatibilityPreview(immediate: true);
        return Task.CompletedTask;
    }

    private void ResetCompatibilityImage()
    {
        _compatibilityDelay?.Cancel();
        _compatibilityPending = false;
        _compatibilityRequestId = _compatibilityActiveRequest = null;
        _compatibilityRendering = false;
        _compatibilityContext = null;
        CompatibilityImage.Source = null;
        CompatibilitySurface.ChangeView(0, 0, 1);
    }

    private void QueueCompatibilityPreview(bool immediate = false)
    {
        if (!_compatibilityPreview || _closing) return;
        _compatibilityContext = _context;
        _compatibilityRequestId = Guid.NewGuid().ToString("N");
        _compatibilityDelayReady = immediate;
        _compatibilityPending = _context != "metro";
        _compatibilityDelay?.Cancel();
        _compatibilityDelay?.Dispose();
        _compatibilityDelay = null;
        if (immediate) _ = RunAsync(StartCompatibilityPreviewAsync);
        else
        {
            _compatibilityDelay = new CancellationTokenSource();
            _ = DebounceCompatibilityPreviewAsync(_compatibilityDelay.Token);
        }
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
        if (!_compatibilityPreview || !_compatibilityPending || !_compatibilityDelayReady ||
            _engineUnavailable || _closing || _context == "metro") return;
        if (!CurrentDocumentAvailable) return;
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
            if (request == _compatibilityActiveRequest && !result.GetProperty("started").GetBoolean())
            {
                _compatibilityRendering = false;
                ShowCompatibilityError(UiLanguage.Text("Preview_ImageFailed"));
            }
        }
        catch (Exception error)
        {
            if (request != _compatibilityActiveRequest) return;
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
