// 城市草稿复用当前预览；作品修订与观察相机分开管理。
using System.Diagnostics;
using System.Text.Json;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private bool _cityArtwork;
    private bool _cityArtworkUpdating;
    private bool _cityArtworkExporting => _exportWindow?.IsDraftExportBusy == true;
    private int _cityArtworkRevision;
    private JsonElement? _pendingCityArtwork;
    private sealed record ArtworkObservation(double Scale, double X, double Y, double Width, double Height);
    private ArtworkObservation? _artworkView, _artworkRestoreView;
    private bool _resettingArtworkCanvas;
    private bool _restoreCityArtworkPreview;

    private async Task ResetArtworkCanvasAsync()
    {
        if (!_cityArtwork || _resettingArtworkCanvas || _cityArtworkExporting) return;
        _resettingArtworkCanvas = true;
        _artworkReset.IsEnabled = false;
        _artworkRestoreView ??= _artworkView;
        _restoreCityArtworkPreview = true;
        ++_artworkCloseupGeneration; _pendingArtworkCloseup = null;
        try
        {
            await _artworkWriteTail;
            if (_cityArtwork && !_closing) await RetryPreviewAsync();
        }
        finally
        {
            _resettingArtworkCanvas = false;
            _artworkReset.IsEnabled = true;
        }
    }

    private void RestoreArtworkView()
    {
        if (_artworkRestoreView is not { } view) return;
        _artworkRestoreView = null;
        SendArtworkCommand("setArtworkView", new { observation = new { scale = view.Scale, x = view.X, y = view.Y,
            viewportWidth = view.Width, viewportHeight = view.Height } });
    }
    private sealed record CityArtworkOperation(long Id, int Epoch, string Kind, long Started,
        int Revision = 0, string? SceneId = null, int SceneRevision = 0,
        long Sent = 0, double SerializeMs = 0, int JsonChars = 0,
        string Stage = "preparing", double? Percent = null);
    private long _cityArtworkSerial;
    private CityArtworkOperation? _cityArtworkOperation;

    private CityArtworkOperation StartCityArtworkOperation(string kind, long? started = null)
    {
        var operation = new CityArtworkOperation(++_cityArtworkSerial, _engineDocumentEpoch,
            kind, started ?? Stopwatch.GetTimestamp());
        _cityArtworkOperation = operation;
        _pendingCityArtwork = null;
        _cityArtworkUpdating = true; ApplyMapTaskState(); UpdateCommandStates();
        return operation;
    }

    private void UpdateCityArtworkProgress(string stage, double? percent = null)
    {
        if (_cityArtworkOperation is not { } operation) return;
        if (operation.Stage != stage)
            StartupTrace.Write($"City artwork stage: operation={operation.Id}, artwork={operation.Revision}, scene={operation.SceneId}/{operation.SceneRevision}, stage={stage}");
        _cityArtworkOperation = operation with { Stage = stage, Percent = percent };
        ApplyMapTaskState();
    }

    private string CityArtworkProgressText()
    {
        if (_cityArtworkOperation is not { } operation) return "1/4 · " + EditorText("LoadingTheme");
        var (step, label) = operation.Stage switch {
            "preparing" => (1, "LoadingTheme"),
            "waitingBase" => (1, "LoadingBase"),
            "delivering" => (3, "LoadingDelivery"),
            "presenting" => (4, "LoadingPresent"),
            "labels" => (2, "LoadingLabels"),
            "icons" => (2, "LoadingIcons"),
            "transport" => (2, "LoadingTransport"),
            _ => (2, "LoadingLayout"),
        };
        return $"{step}/4 · {EditorText(label)}" + (operation.Percent is { } value ? $" · {value:0}%" : "");
    }

    private bool MatchesCityArtwork(JsonElement frame) => _cityArtwork &&
        _cityArtworkOperation is { } operation && operation.Epoch == _engineDocumentEpoch &&
        (!frame.TryGetProperty("operationId", out var id) || id.GetInt64() == operation.Id) &&
        frame.GetProperty("documentEpoch").GetInt32() == operation.Epoch &&
        (frame.TryGetProperty("artworkRevision", out var revision) ? revision : frame.GetProperty("revision")).GetInt32() == operation.Revision;

    private async Task BeginCityArtworkAsync()
        => await BeginCityArtworkCoreAsync(4096, _snapshot.GetProperty("output").GetProperty("rotation").GetInt32(),
            _selectedBounds is { } selected ? new { bounds = selected, source = "selection" } : null);

    private async Task BeginCityArtworkCoreAsync(int longEdge, int rotation, object? region, double? elementScale = null, object? poiDensity = null)
    {
        if (_context == "metro" || !_viewerReady || string.IsNullOrEmpty(_sceneId) || _compatibilityPreview)
            throw new InvalidOperationException(UiLanguage.Text("Artwork_PreviewRequired"));
        _cityArtwork = true;
        _cityArtworkRevision = 0;
        var operation = StartCityArtworkOperation("initial");
        try
        {
            await _engine.RequestAsync("beginCityArtwork", new {
                documentEpoch = operation.Epoch, operationId = operation.Id, kind = _context == "transit" ? "transit" : "normal", longEdge, rotation, region,
                elementScale = elementScale ?? longEdge / 4096d, poiDensity, incremental = true });
        }
        catch { if (_cityArtworkOperation?.Id == operation.Id) await LeaveCityArtworkAsync(); throw; }
    }

    private async Task RefreshCityArtworkAsync()
        => await RefreshCityArtworkCoreAsync(null);

    private Task RefreshCityArtworkCoreAsync(long? started)
    {
        var epoch = _engineDocumentEpoch;
        var editor = _artworkEditor;
        return QueueArtworkWriteAsync(async () => {
            if (!_cityArtwork || epoch != _engineDocumentEpoch || editor != _artworkEditor) return;
            var operation = StartCityArtworkOperation("update", started);
            try {
                var result = await _engine.RequestAsync("refreshCityArtwork", new { documentEpoch = epoch, operationId = operation.Id, debounce = true });
                if (_cityArtworkOperation?.Id == operation.Id && result.TryGetProperty("artworkRevision", out var revision))
                    _cityArtworkRevision = revision.GetInt32();
            }
            catch { if (_cityArtworkOperation?.Id == operation.Id) { _cityArtworkUpdating = false; ApplyMapTaskState(); } throw; }
        });
    }

    private async Task LeaveCityArtworkAsync()
    {
        var epoch = _engineDocumentEpoch;
        _cityArtwork = _cityArtworkUpdating = false;
        _artworkView = _artworkRestoreView = null;
        _restoreCityArtworkPreview = false;
        _pendingCityArtwork = null; _cityArtworkOperation = null;
        UnmountArtworkEditor();
        SendViewer(new { type = "leaveArtwork", sceneId = _sceneId, revision = _revision,
            documentEpoch = epoch, artworkRevision = _cityArtworkRevision });
        ApplyMapTaskState(); UpdateCommandStates();
        await _engine.RequestAsync("leaveCityArtwork", new { documentEpoch = epoch });
        if (epoch == _engineDocumentEpoch && !_cityArtwork) await RefreshLivePreviewAsync();
    }

    private void TryPresentCityArtwork()
    {
        if (!_cityArtwork || !_viewerReady || _awaitingScene || _appliedGuideScene != (_sceneId, _revision) ||
            _pendingCityArtwork is not { } frame) return;
        if (frame.GetProperty("sceneId").GetString() != _sceneId || frame.GetProperty("revision").GetInt32() != _revision ||
            _cityArtworkOperation is not { } operation || !MatchesCityArtwork(frame.GetProperty("artwork"))) return;
        var artwork = frame.GetProperty("artwork");
        _cityArtworkRevision = artwork.GetProperty("revision").GetInt32();
        UpdateCityArtworkProgress("presenting");
        SendViewer(new { type = "beginArtwork", sceneId = _sceneId, revision = _revision,
            documentEpoch = _engineDocumentEpoch, artworkRevision = _cityArtworkRevision });
        var sending = Stopwatch.GetTimestamp();
        var json = JsonSerializer.Serialize(new { type = "loadArtwork", sceneId = _sceneId, revision = _revision, artwork });
        _cityArtworkOperation = _cityArtworkOperation! with { Sent = sending,
            SerializeMs = Stopwatch.GetElapsedTime(sending).TotalMilliseconds, JsonChars = json.Length };
        MapView.CoreWebView2.PostWebMessageAsJson(json);
        _pendingCityArtwork = null;
    }
}
