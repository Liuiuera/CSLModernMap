// 隔离内嵌预览的生命周期，地图数据和导出不依赖浏览器启动。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private bool? _documentLoaded;
    private bool _loadingDocument => _documentLoaded == false;
    private bool _previewStarting, _previewRecovering, _previewClosed, _previewSlow;
    private int _previewInstance;
    private string? _previewFailure;
    private CancellationTokenSource? _previewWait;
    private bool _fullSceneCompiled, _previewWaitDismissed, _previewNoticeDismissed;
    private bool PreviewInteractive => _viewerReady && _mapTask.HasMap && !_loadingDocument;

    private bool IsCurrentPreview(int instance) => !_closing && instance == _previewInstance;

    private async Task InitializePreviewAsync(bool recovering = false)
    {
        if (_closing || _engineUnavailable) return;
        var instance = recovering ? ResetPreview() : ++_previewInstance;
        _previewNoticeDismissed = false;
        _viewerReady = false; _previewStarting = true; _previewFailure = null; _previewSlow = false;
        _previewRecovering = recovering;
        _presentedGuideScene = _appliedGuideScene = null;
        if (recovering)
        {
            OperationError.IsOpen = false;
            if (!_loadingDocument)
                _mapTask.Begin(MapTaskPhase.Loading, UiLanguage.Text("Preview_Recovering"), canCancel: false);
        }
        else if (!_loadingDocument) _mapTask.Reset(false);
        ApplyMapTaskState();
        try
        {
            var preview = await _engine.RequestAsync("startPreview");
            if (!IsCurrentPreview(instance)) return;
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
            if (!IsCurrentPreview(instance)) return;
            await MapView.EnsureCoreWebView2Async(environment);
            if (!IsCurrentPreview(instance)) return;
            var core = MapView.CoreWebView2 ?? throw new InvalidOperationException("WebView2 initialization returned no browser.");
            StartupTrace.Write($"Preview initialized: runtime={environment.BrowserVersionString}, instance={instance}");
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.AreDevToolsEnabled = false;
            await core.AddScriptToExecuteOnDocumentCreatedAsync("window.__CSL_PREVIEW_QUALITY__ = " +
                JsonSerializer.Serialize(_previewQuality, PreviewQualitySettings.JsonOptions) + ";");
            if (!IsCurrentPreview(instance)) return;
            core.WebMessageReceived += (sender, args) => Root.DispatcherQueue.TryEnqueue(() =>
            {
                if (!IsCurrentPreview(instance)) return;
                try { HandleViewerEvent(args.TryGetWebMessageAsString()); }
                catch (Exception error) { _ = FailPreviewAsync(error, "viewerMessage"); }
            });
            core.NavigationCompleted += (sender, args) =>
            {
                if (!IsCurrentPreview(instance)) return;
                StartupTrace.Write($"Preview navigation: success={args.IsSuccess}, status={args.WebErrorStatus}, instance={instance}");
                if (!args.IsSuccess && args.WebErrorStatus != CoreWebView2WebErrorStatus.OperationCanceled)
                    _ = FailPreviewAsync(new IOException(args.WebErrorStatus.ToString()), "navigation");
            };
            core.ProcessFailed += (sender, args) =>
            {
                if (!IsCurrentPreview(instance)) return;
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
            if (IsCurrentPreview(instance)) await FailPreviewAsync(error, "initialization");
        }
    }

    private int ResetPreview()
    {
        var instance = ++_previewInstance;
        _previewQualityCompletion?.TrySetException(new InvalidOperationException(UiLanguage.Text("PreviewQuality_Restarted")));
        _viewerReady = _previewStarting = _previewRecovering = _previewSlow = false;
        _previewWait?.Cancel();
        _fullSceneCompiled = false;
        _awaitingScene = false;
        _pendingViewerScene = null;
        _completeScene = null;
        _previewLayerWrites.Clear();
        if (!_previewClosed) { MapView.Close(); _previewClosed = true; }
        if (!_loadingDocument) _mapTask.Reset(false);
        return instance;
    }

    private async Task<bool> StopPreviewAsync()
    {
        var instance = ResetPreview();
        ApplyMapTaskState();
        try
        {
            await _engine.RequestAsync("setPreviewEnabled", new { enabled = false });
        }
        catch (Exception error) when (!IsCurrentPreview(instance))
        {
            StartupTrace.Write($"Superseded preview cleanup failed: instance={instance}\n{error}");
        }
        return IsCurrentPreview(instance);
    }

    private async Task FailPreviewAsync(Exception error, string stage)
    {
        if (_closing || _previewFailure is not null) return;
        _previewFailure = UiLanguage.Text("Preview_Failed");
        var failure = TaskError.FromException(error, UiLanguage.Text("Preview_Failed"), stage);
        StartupTrace.Write($"Preview failed: instance={_previewInstance}, scene={_sceneId}, revision={_revision}, documentEpoch={_engineDocumentEpoch}\n{failure.Details}");
        OperationError.Severity = InfoBarSeverity.Error;
        OperationError.Message = _previewFailure;
        OperationError.Content = ErrorFeedback.Actions(failure, Root);
        OperationError.IsOpen = true;
        await EnableCompatibilityPreviewAsync();
    }

    private async Task RetryPreviewAsync()
    {
        if (_closing || _engineUnavailable) return;
        _compatibilityPreview = false;
        ResetCompatibilityImage();
        await InitializePreviewAsync(recovering: true);
    }

    private async Task FinishPreviewStartupAsync(int instance)
    {
        try
        {
            var state = await _engine.RequestAsync("setPreviewEnabled", new { enabled = true });
            if (!IsCurrentPreview(instance)) return;
            if (_pendingViewerScene is { } scene)
            {
                _pendingViewerScene = null;
                HandleEngineEvent("sceneReady", scene);
                return;
            }
            if (!state.GetProperty("hasDocument").GetBoolean())
            {
                _previewRecovering = false;
                return;
            }
            if (_cityArtwork && _restoreCityArtworkPreview)
            {
                BeginMapTask(MapTaskPhase.Generating, EditorText("ResetCanvas"), canCancel: false);
                await _engine.RequestAsync("restoreCityArtworkPreview", new { documentEpoch = _engineDocumentEpoch });
                if (IsCurrentPreview(instance)) _restoreCityArtworkPreview = false;
            }
            else await CompilePreviewCoreAsync(force: false);
        }
        catch (Exception error)
        {
            if (IsCurrentPreview(instance)) await FailPreviewAsync(error, "startup");
        }
        finally
        {
            if (IsCurrentPreview(instance))
            {
                _previewStarting = false;
                ApplyMapTaskState();
            }
        }
    }

    private async Task OpenExternalPreviewAsync()
    {
        _compatibilityRequestId = null;
        if (!await StopPreviewAsync()) return;
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
        var stopped = !_viewerReady;
        var show = _context != "metro" && !_compatibilityPreview && !_previewNoticeDismissed && (stopped || _previewSlow);
        PreviewActions.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        PreviewCompatibility.IsEnabled = !_engineUnavailable;
        PreviewRetry.IsEnabled = !_engineUnavailable;
        PreviewHtml.IsEnabled = _documentLoaded == true && !_engineUnavailable;
        if (show && !_loadingDocument)
        {
            if (!CanOpenRecentExport) MapOverlay.Visibility = Visibility.Collapsed;
            PreviewNoticeTitle.Text = UiLanguage.Text(_previewSlow ? "Preview_Slow" : "Preview_Stopped");
            PreviewNoticeDetail.Text = UiLanguage.Text("Preview_NotRunningDetail");
        }
        if (_fullSceneCompiled && !_loadingDocument && !_artworkEntryPreparing && !(_cityArtworkUpdating && _cityArtworkOperation?.Kind == "initial"))
            MapOverlay.Visibility = Visibility.Collapsed;
        var imageMode = _compatibilityPreview && _context != "metro";
        CompatibilitySurface.Visibility = CompatibilityNotice.Visibility = imageMode ? Visibility.Visible : Visibility.Collapsed;
        CompatibilityUpdating.Visibility = imageMode && _compatibilityRequestId is not null
            ? Visibility.Visible : Visibility.Collapsed;
        CompatibilityRetry.IsEnabled = PreviewRetry.IsEnabled;
        MapView.Visibility = _context == "metro" || _compatibilityPreview || _previewClosed ? Visibility.Collapsed : Visibility.Visible;
        if (imageMode && _documentLoaded == true) MapOverlay.Visibility = Visibility.Collapsed;
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
