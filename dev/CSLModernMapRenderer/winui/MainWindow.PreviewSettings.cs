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
            try { await ApplyPreviewQualityAsync(_previewQuality.WithPreset(preset)); }
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
                    quality = JsonSerializer.SerializeToElement(next, PreviewQualitySettings.JsonOptions) });
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
            body.Children.Add(new Expander { Header = QualityText("Details"), Content = fields,
                HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch });
            var section = ExportDesign.Group(QualityText("Distance"));
            var guidance = ExportDesign.Text(QualityText("DistanceGuide"));
            ExportDesign.Secondary(guidance);
            section.Children.Add(guidance);
            var distance = new Grid { ColumnSpacing = 6, RowSpacing = 6 };
            foreach (var weight in new[] { 1.2, 1, 1.2, 1 })
                distance.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(weight, GridUnitType.Star) });
            for (int row = 0; row < 4; row++) distance.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            void Cell(FrameworkElement element, int row, int column)
            {
                Grid.SetRow(element, row); Grid.SetColumn(element, column);
                element.VerticalAlignment = VerticalAlignment.Center;
                if (column > 0 && element is TextBlock) element.HorizontalAlignment = HorizontalAlignment.Center;
                distance.Children.Add(element);
            }
            foreach (var (name, column) in new[] { ("Far", 1), ("Middle", 2), ("Near", 3) })
                Cell(new TextBlock { Text = QualityText(name), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold }, 0, column);
            Action? refreshScale = null;
            void ScaleRow(string name, int row, double farFactor, Func<int> read, Action<int> write)
            {
                var title = QualityText(name);
                Cell(ExportDesign.Text(title), row, 0);
                var far = new TextBlock(); var near = new TextBlock();
                var input = new NumberBox { Name = "PreviewQuality" + name, Minimum = 50, Maximum = 200,
                    SmallChange = 5, LargeChange = 10, MinWidth = 0, MaxWidth = 88,
                    HorizontalAlignment = HorizontalAlignment.Stretch,
                    SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact };
                Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(input, QualityText("Middle") + " · " + title);
                Cell(far, row, 1); Cell(input, row, 2); Cell(near, row, 3);
                void ShowValues()
                {
                    var value = read();
                    input.Value = value;
                    far.Text = $"≤ {value * farFactor:0.##}%"; near.Text = $"{value * 1.25:0.##}%";
                }
                input.ValueChanged += (_, args) =>
                {
                    if (syncing) return;
                    if (double.IsFinite(args.NewValue)) write((int)Math.Round(args.NewValue));
                    Sync();
                };
                refreshScale += ShowValues;
            }
            ScaleRow("TextPercent", 1, .5, () => draft.TextPercent, value => draft = draft with { TextPercent = value });
            ScaleRow("LinePercent", 2, .75, () => draft.LinePercent, value => draft = draft with { LinePercent = value });
            ScaleRow("IconPercent", 3, .5, () => draft.IconPercent, value => draft = draft with { IconPercent = value });
            section.Children.Add(distance);
            var caps = ExportDesign.Text(QualityText("DistanceHelp"));
            ExportDesign.Secondary(caps);
            section.Children.Add(caps);
            var distanceCard = ExportDesign.Card(section);
            distanceCard.Name = "PreviewQualityDistance";
            body.Children.Add(distanceCard);
            void Select(ComboBox combo, object value) => combo.SelectedItem = combo.Items.Cast<ComboBoxItem>().Single(item => item.Tag.Equals(value));
            void Sync()
            {
                syncing = true;
                try
                {
                    Select(clarity, draft.PixelScale); Select(lines, draft.LineDetail); Select(polygons, draft.PolygonDetail);
                    refreshScale?.Invoke();
                    foreach (var (key, button) in presetButtons) button.IsChecked = key == draft.Preset;
                    refreshSamples?.Invoke();
                }
                finally { syncing = false; }
            }
            void Changed(object sender, SelectionChangedEventArgs args)
            {
                if (syncing) return;
                draft = draft with { PixelScale = (double)((ComboBoxItem)clarity.SelectedItem).Tag,
                    LineDetail = (string)((ComboBoxItem)lines.SelectedItem).Tag, PolygonDetail = (string)((ComboBoxItem)polygons.SelectedItem).Tag };
                Sync();
            }
            clarity.SelectionChanged += Changed; lines.SelectionChanged += Changed; polygons.SelectionChanged += Changed;
            foreach (var (key, button) in presetButtons)
                button.Click += (_, _) => { draft = draft.WithPreset(key); Sync(); };
            Sync();

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
            { draft = new(); Sync(); return Task.CompletedTask; });
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
            finally { if (_previewSettingsWindow == window) _previewSettingsWindow = null; }
        }
        finally { _openingEditors.Remove("preview-settings"); }
    }
}
