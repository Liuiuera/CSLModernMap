// 更新主题编辑器中的实时预览。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace CSLModernMapWinUI;

internal sealed partial class ThemeEditorDialog
{
    private Image? _textSample, _textSideSample;
    private TextBlock? _textSampleStatus;

    private void BuildSamples()
    {
        _samplePanel.Children.Clear(); _textSideSample = null;
        _samplePanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Preview_BuildSamples_EBD3615F5E"), FontSize = 12 });
        if (_section == "roads" && _working["roads"] is JsonObject roads)
        {
            if (roads[_selectedRoad] is not JsonObject) _selectedRoad = RoadLevels.FirstOrDefault(level => roads[level.Key] is JsonObject).Key ?? "highway";
            var line = new Border { HorizontalAlignment = HorizontalAlignment.Stretch, Margin = new Thickness(10, 12, 10, 8) };
            var caption = new TextBlock { FontSize = 12, TextWrapping = TextWrapping.Wrap };
            _samplePanel.Children.Add(line); _samplePanel.Children.Add(caption);
            _sampleUpdates.Add(() =>
            {
                if (_working["roads"]?[_selectedRoad] is not JsonObject cfg) return;
                if (cfg["fill"] is { } fill) line.Background = BrushFor(fill.GetValue<string>());
                if (cfg["border"] is { } border) line.BorderBrush = BrushFor(border.GetValue<string>());
                var minimum = cfg["min_fill_px"] is { } minFill ? Numeric(minFill) : 0;
                var borderWidth = cfg["border_width"] is { } bw ? SizeAt(bw, 1) : 1;
                var scale = roads["width_scale"] is { } widthScale ? Numeric(widthScale) : .15;
                line.BorderThickness = new Thickness(borderWidth / 2); line.Height = Math.Max(.5 + 16 * scale, minimum) + borderWidth;
                caption.Text = RoadLevels.First(level => level.Key == _selectedRoad).Label + UiLanguage.Format("ThemeEditorDialog_Preview_BuildSamples_1A0787C40B", minimum);
            });
        }
        else if (_section == "terrain" && _working["terrain"]?["elevation_colors"] is JsonArray)
        {
            _samplePanel.Children.Add(RampBand(["terrain", "elevation_colors"]));
            var range = new TextBlock { FontSize = 12 };
            _samplePanel.Children.Add(range);
            _sampleUpdates.Add(() =>
            { var heights = _working["terrain"]!["elevation_colors"]!.AsArray().OfType<JsonObject>().Select(stop => Numeric(stop["threshold"]!)).ToArray();
                range.Text = heights.Length == 0 ? UiLanguage.Text("ThemeEditorDialog_Preview_BuildSamples_5BD3906590") : $"{heights.Min():0.##} m — {heights.Max():0.##} m"; });
        }
        else if (_section == "labels" && _working["labels"]?[_labelType] is JsonObject)
        {
            _textSideSample = new Image { Stretch = Stretch.Uniform, MaxHeight = 80, MinHeight = 48 };
            _samplePanel.Children.Add(_textSideSample);
        }
        else _samplePanel.Children.Add(new TextBlock { Text = UiLanguage.Text("ThemeEditorDialog_Preview_BuildSamples_1350C527B6"), FontSize = 12, TextWrapping = TextWrapping.Wrap });
    }
    private void UpdateSamples()
    {
        foreach (var update in _sampleUpdates.ToArray()) update();
        if (_section == "labels" && _working["labels"]?[_labelType] is JsonObject && _textSample is not null)
        {
            _sampleCancellation?.Cancel(); _sampleCancellation?.Dispose();
            _sampleCancellation = new CancellationTokenSource(); var revision = ++_sampleRevision;
            _ = UpdateTextSampleAsync(revision, _sampleCancellation.Token);
        }
    }
    private async Task UpdateTextSampleAsync(int revision, CancellationToken token)
    {
        try
        {
            var result = await _engine.RequestAsync("renderThemeTextSample", new
            { data = JsonNode.Parse(Snapshot()), type = _labelType, renderScale = 1, documentEpoch = _documentEpoch }, token);
            if (_invalidated || token.IsCancellationRequested || revision != _sampleRevision) return;
            var url = result.GetProperty("url").GetString()!;
            bool Current() => !_invalidated && !token.IsCancellationRequested && revision == _sampleRevision;
            if (_textSample is { } sample) await PreviewImages.SetAsync(sample, url, Current,
                error => { if (_textSampleStatus is not null) _textSampleStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_UpdateTextSampleAsync_A3C0F4544E") + error; });
            if (_textSideSample is { } side) await PreviewImages.SetAsync(side, url, Current);
            if (!Current()) return;
            if (_textSampleStatus is not null) _textSampleStatus.Text = result.GetProperty("fontSubstituted").GetBoolean()
                ? UiLanguage.Text("ThemeEditorDialog_Preview_UpdateTextSampleAsync_DC0A20DDA8") + result.GetProperty("resolvedFont").GetString() : "";
        }
        catch (OperationCanceledException) { }
        catch (InvalidOperationException error)
        { if (!_invalidated && !token.IsCancellationRequested && revision == _sampleRevision && _textSampleStatus is not null) _textSampleStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_UpdateTextSampleAsync_2964235EB9") + error.Message; }
    }
    private void CancelPreview()
    { _previewDelay?.Cancel(); _previewDelay?.Dispose(); _previewDelay = null; _previewRequestId = null; }
    private void SchedulePreview(bool immediate = false)
    {
        if (_invalidated || _building || _flushingPreviewInputs) return;
        CancelPreview();
        _previewDelay = new CancellationTokenSource();
        _previewStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_SchedulePreview_A8EECC3509");
        _ = PreviewAfterDelayAsync(_previewDelay.Token, immediate);
    }
    private async Task PreviewAfterDelayAsync(CancellationToken token, bool immediate)
    {
        try
        {
            if (!immediate) await Task.Delay(350, token);
            if (_invalidated || token.IsCancellationRequested) return;
            _flushingPreviewInputs = true;
            try
            {
                if (!FlushInputs()) { _previewStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_PreviewAfterDelayAsync_E04BCC3467"); return; }
            }
            finally { _flushingPreviewInputs = false; }
            var requestId = _previewRequestId = Guid.NewGuid().ToString("N");
            var scene = _metadata.GetProperty("scenes").TryGetProperty(_section, out var mapped) ? mapped.GetString() : "overall";
            var data = JsonNode.Parse(Snapshot());
            var result = await _engine.RequestAsync("renderThemePreview", new
            { requestId, data, scene, size = 640, documentEpoch = _documentEpoch }, token);
            if (_previewRequestId == requestId && !result.GetProperty("started").GetBoolean())
                _previewStatus.Text = result.GetProperty("message").GetString()!;
        }
        catch (OperationCanceledException) { }
        catch (InvalidOperationException error) { if (!_invalidated && !token.IsCancellationRequested) _previewStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_PreviewAfterDelayAsync_DE72B02080") + error.Message; }
    }
    public void HandlePreviewEvent(string name, JsonElement payload)
    {
        if (_invalidated || payload.GetProperty("requestId").GetString() != _previewRequestId) return;
        if (name == "themePreviewFailed") { _previewStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_HandlePreviewEvent_DE72B02080") + payload.GetProperty("message").GetString(); return; }
        var requestId = _previewRequestId;
        _ = PreviewImages.SetAsync(_preview, payload.GetProperty("url").GetString()!,
            () => !_invalidated && _previewRequestId == requestId,
            error => _previewStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_HandlePreviewEvent_61D83FB564") + error,
            () => _previewStatus.Text = UiLanguage.Text("ThemeEditorDialog_Preview_HandlePreviewEvent_DCAB07490E"));
    }
}
