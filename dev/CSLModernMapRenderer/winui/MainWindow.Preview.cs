// 隔离内嵌预览的生命周期，地图数据和导出不依赖浏览器启动。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private bool _loadingDocument, _previewStarting, _previewClosed, _previewSlow;
    private int _previewInstance;
    private string? _previewFailure;
    private CancellationTokenSource? _previewWait;
    private bool _fullSceneCompiled, _previewWaitDismissed, _previewNoticeDismissed;
    private bool PreviewInteractive => _viewerReady && _mapTask.HasMap && !_loadingDocument &&
        (!_mapTask.Active || _mapTask.Background);

    private async Task InitializePreviewAsync()
    {
        if (_closing || _engineUnavailable || _previewStarting) return;
        _previewNoticeDismissed = false;
        var instance = ++_previewInstance;
        _viewerReady = false; _previewStarting = true; _previewFailure = null; _previewSlow = false;
        _presentedGuideScene = _appliedGuideScene = null;
        if (!_loadingDocument) _mapTask.Reset(false);
        ApplyMapTaskState();
        try
        {
            var preview = await _engine.RequestAsync("startPreview");
            if (_closing || instance != _previewInstance) return;
            if (_previewClosed)
            {
                MapSurface.Children.Remove(MapView);
                MapView = new WebView2();
                MapSurface.Children.Insert(0, MapView);
                _previewClosed = false;
            }
            var profile = Path.Combine(Path.GetDirectoryName(_ribbonLayoutDirectory)!, "WebView2-WinUI");
            Directory.CreateDirectory(profile);
            var environment = await CoreWebView2Environment.CreateWithOptionsAsync(
                null, profile, new CoreWebView2EnvironmentOptions());
            if (_closing || instance != _previewInstance) return;
            await MapView.EnsureCoreWebView2Async(environment);
            if (_closing || instance != _previewInstance) return;
            var core = MapView.CoreWebView2 ?? throw new InvalidOperationException("WebView2 initialization returned no browser.");
            StartupTrace.Write($"Preview initialized: runtime={environment.BrowserVersionString}, instance={instance}");
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            await core.AddScriptToExecuteOnDocumentCreatedAsync("window.__CSL_PREVIEW_QUALITY__ = " +
                JsonSerializer.Serialize(_previewQuality, PreviewQualitySettings.JsonOptions) + ";");
            if (_closing || instance != _previewInstance) return;
            core.WebMessageReceived += (sender, args) => Root.DispatcherQueue.TryEnqueue(() =>
            {
                if (_closing || instance != _previewInstance) return;
                try { HandleViewerEvent(args.TryGetWebMessageAsString()); }
                catch (Exception error) { _ = FailPreviewAsync(error, "viewerMessage"); }
            });
            core.NavigationCompleted += (sender, args) =>
            {
                if (_closing || instance != _previewInstance) return;
                StartupTrace.Write($"Preview navigation: success={args.IsSuccess}, status={args.WebErrorStatus}, instance={instance}");
                if (!args.IsSuccess && args.WebErrorStatus != CoreWebView2WebErrorStatus.OperationCanceled)
                    _ = FailPreviewAsync(new IOException(args.WebErrorStatus.ToString()), "navigation");
            };
            core.ProcessFailed += (sender, args) =>
            {
                if (_closing || instance != _previewInstance) return;
                var details = $"Kind: {args.ProcessFailedKind}\nReason: {args.Reason}\nExitCode: {args.ExitCode}";
                StartupTrace.Write("Preview process failure: " + details);
                if (args.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessUnresponsive)
                {
                    if (_fullSceneCompiled && !_previewWaitDismissed) { _previewSlow = true; ApplyMapTaskState(); }
                }
                else if (args.ProcessFailedKind is CoreWebView2ProcessFailedKind.BrowserProcessExited or CoreWebView2ProcessFailedKind.RenderProcessExited)
                    _ = FailPreviewAsync(new IOException(details), "browserProcess");
            };
            MapView.Source = new Uri(preview.GetProperty("viewerUrl").GetString()!);
        }
        catch (Exception error)
        {
            if (!_closing && instance == _previewInstance) await FailPreviewAsync(error, "initialization");
        }
    }

    private async Task StopPreviewAsync()
    {
        _previewQualityCompletion?.TrySetException(new InvalidOperationException(UiLanguage.Text("PreviewQuality_Restarted")));
        ++_previewInstance;
        _viewerReady = _previewStarting = _previewSlow = false;
        _previewWait?.Cancel();
        _fullSceneCompiled = false;
        _awaitingScene = _waitingSceneReady = false;
        _discardSceneResults = true;
        if (!_previewClosed) { MapView.Close(); _previewClosed = true; }
        if (!_loadingDocument) _mapTask.Reset(false);
        ApplyMapTaskState();
        await _engine.RequestAsync("setPreviewEnabled", new { enabled = false });
        await RefreshSnapshotAsync();
    }

    private async Task FailPreviewAsync(Exception error, string stage)
    {
        if (_closing || _previewFailure is not null) return;
        _previewFailure = UiLanguage.Text("Preview_Failed");
        var failure = TaskError.FromException(error, UiLanguage.Text("Preview_Failed"), stage);
        StartupTrace.Write($"Preview failed: scene={_sceneId}, revision={_revision}, documentEpoch={_engineDocumentEpoch}\n{failure.Details}");
        try { await StopPreviewAsync(); }
        catch (Exception stopError) { if (!_engineUnavailable) SetError(stopError); }
        if (_closing) return;
        OperationError.Severity = InfoBarSeverity.Error;
        OperationError.Message = _previewFailure;
        OperationError.Content = ErrorFeedback.Actions(failure, Root);
        OperationError.IsOpen = true;
        ApplyMapTaskState();
    }

    private async Task RetryPreviewAsync()
    {
        _compatibilityPreview = false;
        ResetCompatibilityImage();
        await StopPreviewAsync();
        OperationError.IsOpen = false;
        await InitializePreviewAsync();
    }

    private async Task OpenExternalPreviewAsync()
    {
        _compatibilityPending = false;
        _compatibilityDelay?.Cancel();
        await StopPreviewAsync();
        await OpenBrowserPreviewAsync();
    }

    private async void ObservePreviewWait()
    {
        _previewNoticeDismissed = false;
        _previewWait?.Cancel();
        _previewWait?.Dispose();
        _previewWait = new CancellationTokenSource();
        var cancellation = _previewWait.Token;
        try
        {
            await Task.Delay(TimeSpan.FromSeconds(30), cancellation);
            if (!_closing && _fullSceneCompiled && !_previewWaitDismissed && _mapTask.Active)
            {
                _previewSlow = true;
                ApplyMapTaskState();
            }
        }
        catch (OperationCanceledException) when (cancellation.IsCancellationRequested) { }
    }

    private void ApplyPreviewStatus()
    {
        RefreshPreviewQualitySection();
        var available = _snapshot.ValueKind == JsonValueKind.Object &&
            _snapshot.GetProperty("hasDocument").GetBoolean() && !_loadingDocument && !_engineUnavailable;
        var stopped = !_viewerReady && !_previewStarting;
        var show = _context != "metro" && !_compatibilityPreview && !_previewNoticeDismissed && (stopped || _previewSlow);
        PreviewActions.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        PreviewCompatibility.IsEnabled = available && !_engineBusy;
        PreviewRetry.IsEnabled = !_previewStarting && !_engineUnavailable && !_engineBusy;
        PreviewHtml.IsEnabled = available && !_engineBusy;
        if (show && !_loadingDocument)
        {
            MapOverlay.Visibility = Visibility.Collapsed;
            PreviewNoticeTitle.Text = UiLanguage.Text(_previewSlow ? "Preview_Slow" : "Preview_Stopped");
            PreviewNoticeDetail.Text = UiLanguage.Text("Preview_NotRunningDetail");
        }
        if (_fullSceneCompiled && !_loadingDocument && !_artworkEntryPreparing && !(_cityArtworkUpdating && _cityArtworkOperation?.Kind == "initial"))
            MapOverlay.Visibility = Visibility.Collapsed;
        var imageMode = _compatibilityPreview && _context != "metro";
        CompatibilitySurface.Visibility = CompatibilityNotice.Visibility = imageMode ? Visibility.Visible : Visibility.Collapsed;
        CompatibilityUpdating.Visibility = imageMode && (_compatibilityPending || _compatibilityRendering)
            ? Visibility.Visible : Visibility.Collapsed;
        CompatibilityRetry.IsEnabled = !_engineBusy && !_engineUnavailable;
        MapView.Visibility = _context == "metro" || _compatibilityPreview || _previewClosed ? Visibility.Collapsed : Visibility.Visible;
        if (imageMode && !_loadingDocument) MapOverlay.Visibility = Visibility.Collapsed;
        MapView.IsHitTestVisible = PreviewInteractive && !_artworkEntryPreparing && !(_cityArtworkUpdating && _cityArtworkOperation?.Kind == "initial");
    }

    private void DismissPreviewNotice()
    {
        _previewNoticeDismissed = true;

        _previewWaitDismissed = true;
        _previewWait?.Cancel();
        _previewSlow = false;
        ApplyMapTaskState();
    }
}
