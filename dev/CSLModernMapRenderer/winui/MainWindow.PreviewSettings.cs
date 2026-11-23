// 本机质量偏好只传给内嵌预览，不写入主题或导出设置。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Controls.Primitives;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private EditorWindow? _previewSettingsWindow;
    private PreviewQualitySettings _previewQuality = new();
    private ComboBox? _previewQualityPreset;
    private bool _syncingPreviewQuality, _applyingPreviewQuality;
    private string? _previewQualityRequestId;
    private TaskCompletionSource<JsonElement>? _previewQualityCompletion;
    private bool _outsideNoticeDismissed, _changingOutsideTerrain;
    private CheckBox? _outsideTerrainChoice;
    private bool _outsideChoiceBaseline;

    private bool OutsideTerrainAvailable => !_loadingDocument && !_engineUnavailable &&
        _snapshot.ValueKind == JsonValueKind.Object &&
        _snapshot.TryGetProperty("outsideTerrainAvailable", out var available) && available.GetBoolean() &&
        _snapshot.GetProperty("documentEpoch").GetInt32() == _engineDocumentEpoch;
    private bool OutsideTerrainEnabled => OutsideTerrainAvailable && _snapshot.GetProperty("previewOutsideTerrain").GetBoolean();

    private void RefreshOutsideTerrainControls()
    {
        var available = OutsideTerrainAvailable && _context != "metro" && !_cityArtwork;
        var enabled = OutsideTerrainEnabled;
        var canChange = !_engineBusy && !_mapTask.Active && !_previewStarting && !_changingOutsideTerrain && !_applyingPreviewQuality;
        OutsideTerrainNotice.IsOpen = available && !enabled && !_outsideNoticeDismissed;
        EnableOutsideTerrain.IsEnabled = canChange;
        if (_outsideTerrainChoice is { } choice)
        {
            choice.Visibility = available ? Visibility.Visible : Visibility.Collapsed;
            choice.IsEnabled = canChange;
            if (_outsideChoiceBaseline != enabled)
                choice.IsChecked = _outsideChoiceBaseline = enabled;
        }
    }

    private async Task ApplyOutsideTerrainAsync(bool enabled)
    {
        if (!OutsideTerrainAvailable || enabled == OutsideTerrainEnabled) return;
        if (_engineBusy || _mapTask.Active || _previewStarting || _changingOutsideTerrain)
            throw new InvalidOperationException(QualityText("Busy"));
        var generation = _documentGeneration;
        _changingOutsideTerrain = true;
        RefreshOutsideTerrainControls();
        try
        {
            await _engine.RequestAsync("setPreviewOutsideTerrain", new { documentEpoch = _engineDocumentEpoch, enabled });
            if (generation != _documentGeneration) return;
            await RefreshSnapshotAsync();
            if (generation == _documentGeneration) await RefreshLivePreviewAsync();
        }
        finally { _changingOutsideTerrain = false; RefreshOutsideTerrainControls(); }
    }

    private static string QualityText(string key) => UiLanguage.Text("PreviewQuality_" + key);
    private StackPanel CreatePreviewQualitySection()
    {
        var panel = new StackPanel { Spacing = 10 };
        _previewQualityPreset = new ComboBox
        {
            Header = QualityText("Preset"), HorizontalAlignment = HorizontalAlignment.Stretch,
            Name = "PreviewQualityPreset",
        };
        foreach (var key in new[] { "Smooth", "Standard", "Fine", "Custom" })
            _previewQualityPreset.Items.Add(new ComboBoxItem { Content = QualityText(key), Tag = key, IsEnabled = key != "Custom" });
        _previewQualityPreset.SelectionChanged += async (_, _) =>
        {
            if (_syncingPreviewQuality || _previewQualityPreset.SelectedItem is not ComboBoxItem { Tag: string preset } || preset == "Custom") return;
            try { await ApplyPreviewQualityAsync(PreviewQualitySettings.FromPreset(preset)); }
            catch (Exception error) { SetError(error); }
            finally { RefreshPreviewQualitySection(); }
        };
        panel.Children.Add(_previewQualityPreset);
        panel.Children.Add(OverviewCommand("settings.preview", QualityText("Open")));
        panel.Children.Add(new TextBlock { Text = QualityText("Scope"), TextWrapping = TextWrapping.Wrap });
        RefreshPreviewQualitySection();
        return panel;
    }

    private void RefreshPreviewQualitySection()
    {
        RefreshOutsideTerrainControls();
        if (_previewQualityPreset is null) return;
        _syncingPreviewQuality = true;
        try
        {
            _previewQualityPreset.SelectedItem = _previewQualityPreset.Items.Cast<ComboBoxItem>()
                .Single(item => (string)item.Tag == _previewQuality.Preset);
            _previewQualityPreset.IsEnabled = !_applyingPreviewQuality && !_previewStarting && !_mapTask.Active && !_engineBusy;
        }
        finally { _syncingPreviewQuality = false; }
    }

    private async Task ApplyPreviewQualityAsync(PreviewQualitySettings next)
    {
        next.Validate();
        if (_applyingPreviewQuality || _previewStarting || _mapTask.Active || _engineBusy)
            throw new InvalidOperationException(QualityText("Busy"));
        _applyingPreviewQuality = true;
        RefreshPreviewQualitySection();
        try
        {
            if (_viewerReady && next != _previewQuality)
            {
                _previewQualityRequestId = Guid.NewGuid().ToString("N");
                _previewQualityCompletion = new(TaskCreationOptions.RunContinuationsAsynchronously);
                SendViewer(new { type = "setPreviewQuality", requestId = _previewQualityRequestId,
                    quality = new { pixelScale = next.PixelScale, lineDetail = next.LineDetail, polygonDetail = next.PolygonDetail } });
                var result = await _previewQualityCompletion.Task.WaitAsync(TimeSpan.FromSeconds(30));
                if (result.GetProperty("outcome").GetString() != "applied")
                    throw new InvalidOperationException(result.GetProperty("message").GetString());
            }
            // Keep the displayed state accurate even if saving the preference fails.
            _previewQuality = next;
            UserData.WritePreviewQuality(_ribbonLayoutDirectory, next);
        }
        finally
        {
            _previewQualityCompletion = null; _previewQualityRequestId = null;
            _applyingPreviewQuality = false;
            RefreshPreviewQualitySection();
        }
    }

    private async Task ShowPreviewSettingsAsync()
    {
        if (_previewSettingsWindow is { } existing) { existing.Activate(); return; }
        if (_engineUnavailable || !_openingEditors.Add("preview-settings")) return;
        try
        {
            var generation = _documentGeneration;
            var sampleServer = await _engine.RequestAsync("startPreview");
            if (_closing || generation != _documentGeneration) return;
            var window = _previewSettingsWindow = new EditorWindow(QualityText("Title"), "adjustments", 860, 760, translucent: true, owner: this);
            var draft = _previewQuality;
            var syncing = false;
            Action? refreshSamples = null;
            var presetButtons = new Dictionary<string, ToggleButton>();
            var status = new TextBlock { TextWrapping = TextWrapping.Wrap, Name = "PreviewQualityStatus" };
            var body = new StackPanel { Spacing = 16 };
            body.Children.Add(new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Informational,
                Title = QualityText("Scope"), Message = QualityText("ExportHint"), Name = "PreviewQualityExportNotice" });
            var presets = new Grid { ColumnSpacing = 8 };
            foreach (var key in new[] { "Smooth", "Standard", "Fine" })
            {
                presets.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                var button = new ToggleButton { Content = QualityText(key), HorizontalAlignment = HorizontalAlignment.Stretch,
                    Name = "PreviewQuality" + key };
                Grid.SetColumn(button, presetButtons.Count);
                presets.Children.Add(button); presetButtons[key] = button;
            }
            body.Children.Add(presets);

            ComboBox Choice(string name, params (string Key, object Value)[] choices)
            {
                var combo = new ComboBox { Header = QualityText(name), Name = "PreviewQuality" + name,
                    HorizontalAlignment = HorizontalAlignment.Stretch };
                foreach (var (key, value) in choices) combo.Items.Add(new ComboBoxItem { Content = QualityText(key), Tag = value });
                return combo;
            }
            var clarity = Choice("Clarity", ("Scale75", .75), ("Scale100", 1d), ("Scale125", 1.25));
            var lines = Choice("Lines", ("LinesStandard", "standard"), ("LinesFine", "fine"));
            var polygons = Choice("Polygons", ("PolygonsStandard", "standard"), ("PolygonsFine", "fine"));
            var fields = new StackPanel { Spacing = 10 };
            foreach (var (control, help) in new[] { (clarity, "ClarityHelp"), (lines, "LinesHelp"), (polygons, "PolygonsHelp") })
            {
                var field = new StackPanel { Spacing = 4 };
                field.Children.Add(control);
                field.Children.Add(new TextBlock { Text = QualityText(help), TextWrapping = TextWrapping.Wrap,
                    FontSize = 12, Opacity = 0.75 });
                fields.Children.Add(field);
            }
            body.Children.Add(new Expander { Header = QualityText("Details"), Content = fields, IsExpanded = true,
                HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch });
            void Select(ComboBox combo, object value) => combo.SelectedItem = combo.Items.Cast<ComboBoxItem>().Single(item => item.Tag.Equals(value));
            void Sync()
            {
                syncing = true;
                try
                {
                    Select(clarity, draft.PixelScale); Select(lines, draft.LineDetail); Select(polygons, draft.PolygonDetail);
                    foreach (var (key, button) in presetButtons) button.IsChecked = key == draft.Preset;
                    refreshSamples?.Invoke();
                }
                finally { syncing = false; }
            }
            void Changed(object sender, SelectionChangedEventArgs args)
            {
                if (syncing) return;
                draft = new((double)((ComboBoxItem)clarity.SelectedItem).Tag,
                    (string)((ComboBoxItem)lines.SelectedItem).Tag, (string)((ComboBoxItem)polygons.SelectedItem).Tag);
                Sync();
            }
            clarity.SelectionChanged += Changed; lines.SelectionChanged += Changed; polygons.SelectionChanged += Changed;
            foreach (var (key, button) in presetButtons)
                button.Click += (_, _) => { draft = PreviewQualitySettings.FromPreset(key); Sync(); };
            Sync();

            var outsideLabel = new StackPanel { Spacing = 4 };
            outsideLabel.Children.Add(new TextBlock { Text = QualityText("OutsideLabel"), TextWrapping = TextWrapping.Wrap });
            outsideLabel.Children.Add(new TextBlock { Text = QualityText("OutsideMemory"), FontSize = 12,
                Opacity = 0.75, TextWrapping = TextWrapping.Wrap });
            var outside = _outsideTerrainChoice = new CheckBox { Name = "PreviewQualityOutsideTerrain",
                Content = outsideLabel, IsChecked = _outsideChoiceBaseline = OutsideTerrainEnabled };
            fields.Children.Add(outside);
            RefreshOutsideTerrainControls();

            var samples = new StackPanel { Spacing = 10 };
            samples.Children.Add(new TextBlock { Text = QualityText("Samples"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold });
            using var demo = new PreviewQualityDemo(new Uri(sampleServer.GetProperty("viewerUrl").GetString()!),
                Path.Combine(Path.GetDirectoryName(_ribbonLayoutDirectory)!, "WebView2-WinUI"));
            samples.Children.Add(demo);
            void UpdateSamples()
            {
                demo.Update(draft, QualityText("Standard"), QualityText("Current"), window.Root.ActualTheme);
            }
            refreshSamples = UpdateSamples;
            UpdateSamples();
            window.Root.ActualThemeChanged += (_, _) => UpdateSamples();
            body.Children.Add(samples);
            body.Children.Add(status);
            var scroll = new ScrollViewer { Content = body, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
            window.SetBody(scroll);
            window.ActionsRightAligned = true;
            window.AddAction(UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_BA2E93E730"), () =>
            { draft = new(); outside.IsChecked = false; Sync(); return Task.CompletedTask; });
            window.AddAction(UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_2CD0F3BE87"), () => window.RequestCloseAsync());
            var applying = false;
            window.CanCloseAsync = () => Task.FromResult(!applying);
            var apply = window.AddAction(QualityText("Apply"), async () =>
            {
                if (_closing || generation != _documentGeneration) { status.Text = UiLanguage.Text("MainWindow_PreviewSettings_ShowPreviewSettingsAsync_8B710BEF56"); return; }
                applying = true; scroll.IsEnabled = false;
                foreach (var action in window.Actions.Children.OfType<Control>()) action.IsEnabled = false;
                try
                {
                    await ApplyPreviewQualityAsync(draft);
                    if (generation == _documentGeneration && outside.Visibility == Visibility.Visible)
                        await ApplyOutsideTerrainAsync(outside.IsChecked == true);
                    status.Text = QualityText(_viewerReady ? "Applied" : "Saved");
                }
                catch (Exception error) { status.Text = error.Message; SetError(error); }
                finally
                {
                    applying = false; scroll.IsEnabled = true;
                    foreach (var action in window.Actions.Children.OfType<Control>()) action.IsEnabled = true;
                }
            });
            apply.Name = "PreviewQualityApply";
            apply.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
            try { await window.ShowAsync(); }
            finally { _outsideTerrainChoice = null; if (_previewSettingsWindow == window) _previewSettingsWindow = null; }
        }
        finally { _openingEditors.Remove("preview-settings"); }
    }
}
