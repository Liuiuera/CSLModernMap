// 兼容预览复用 PNG 渲染，连续编辑只提交最新设置。
using System.Text.Json;
using Microsoft.UI.Xaml.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private bool _compatibilityPreview;
    private string? _compatibilityRequestId, _compatibilityContext;

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
        ApplyPreviewStatus();
        QueueCompatibilityPreview(immediate: true);
        return Task.CompletedTask;
    }

    private void ResetCompatibilityImage()
    {
        _compatibilityRequestId = null;
        _compatibilityContext = null;
        CompatibilityImage.Source = null;
        CompatibilitySurface.ChangeView(0, 0, 1);
    }

    private void QueueCompatibilityPreview(bool immediate = false)
    {
        if (!_compatibilityPreview || _closing || _engineUnavailable || _context == "metro" || _documentLoaded != true) return;
        _compatibilityContext = _context;
        var request = _compatibilityRequestId = Guid.NewGuid().ToString("N");
        ApplyPreviewStatus();
        _ = RenderCompatibilityPreviewAsync(request, immediate);
    }

    private async Task RenderCompatibilityPreviewAsync(string request, bool immediate)
    {
        try
        {
            if (!immediate) await Task.Delay(350);
            if (_closing || request != _compatibilityRequestId) return;
            var result = await _engine.RequestAsync("renderCompatibilityPreview", new
            {
                requestId = request, documentEpoch = _engineDocumentEpoch,
                context = _context == "transit" ? "transit" : "normal",
            });
            if (request == _compatibilityRequestId && !result.GetProperty("started").GetBoolean())
            {
                _compatibilityRequestId = null;
                ShowCompatibilityError(UiLanguage.Text("Preview_ImageFailed"));
            }
        }
        catch (Exception error)
        {
            if (request != _compatibilityRequestId) return;
            _compatibilityRequestId = null;
            ShowCompatibilityError(error.Message);
        }
        ApplyPreviewStatus();
    }

    private async Task HandleCompatibilityPreviewAsync(string name, JsonElement payload)
    {
        var request = payload.GetProperty("requestId").GetString();
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
            if (request == _compatibilityRequestId) _compatibilityRequestId = null;
            if (name == "compatibilityPreviewReady" && !_closing && !_engineUnavailable)
                await _engine.RequestAsync("releaseCompatibilityPreview", new { requestId = request });
        }
        ApplyPreviewStatus();
    }

    private void ShowCompatibilityError(string message)
    {
        OperationError.Severity = InfoBarSeverity.Error;
        OperationError.Message = UiLanguage.Text("Preview_ImageFailed") + " " + message;
        OperationError.IsOpen = true;
    }
}
