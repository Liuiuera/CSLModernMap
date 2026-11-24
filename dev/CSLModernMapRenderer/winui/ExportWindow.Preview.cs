// 处理导出窗口中的预览生成与展示。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Path = System.IO.Path;

namespace CSLModernMapWinUI;

internal sealed partial class ExportWindow
{
    private readonly MapRangePreview _rangeCanvas = new(128);
    private readonly StackPanel _rangePreview = new() { Spacing = 4, Visibility = Visibility.Collapsed };
    private readonly TextBlock _contentSummary = ExportDesign.Text("");
    private readonly StackPanel _resultActions = new() { Orientation = Orientation.Horizontal, Spacing = 8,
        Visibility = Visibility.Collapsed };
    private readonly TextBlock _themeTitle = ExportDesign.Text("");
    private readonly Image _themeImage = new() { Width = 44, Height = 34, Stretch = Stretch.UniformToFill };
    private readonly InfoBar _feedback = new() { IsOpen = false, IsClosable = true, Visibility = Visibility.Collapsed };
    private readonly InfoBar _gameRunningNotice = new() { IsOpen = false, IsClosable = false,
        Severity = InfoBarSeverity.Warning, Visibility = Visibility.Collapsed,
        Message = UiLanguage.Text("ExportWindow_GameRunningNotice") };
    private readonly MapRangeControls _rangeControls = new() { Spacing = 4 };
    private RadioButton _rangeFull => _rangeControls.Full;
    private RadioButton _rangeEntry => _rangeControls.Entry;
    private RadioButton _rangeCustom => _rangeControls.Custom;
    private readonly ComboBox _sizePreset = new() { Header = UiLanguage.Text("ExportWindow_Preview__sizePreset_C08C3589C7"), HorizontalAlignment = HorizontalAlignment.Stretch };
    private readonly StackPanel _sizeFields = new() { Spacing = 4 };
    private readonly StackPanel _rotationFields = new() { Spacing = 4 };
    private readonly Dictionary<string, RadioButton> _formatChoices = new();
    private string? _latestResultPath, _latestResultFormat;
    private string _sharedSignature = "";
    private bool _ready, _closed, _syncingRange, _syncingSize, _refreshingShared, _refreshSharedAgain;
    private bool _customSizeSelected;
    private readonly TextBlock _sizeHelp = ExportDesign.Text(UiLanguage.Text("ExportWindow_Preview__sizeHelp_A073C771CC"));
    private readonly TextBlock _rotationHelp = ExportDesign.Text(UiLanguage.Text("ExportWindow_Preview__rotationHelp_4787583E64"));

    private FrameworkElement BuildFormatChoices()
    {
        var cards = new Grid { ColumnSpacing = 8, RowSpacing = 8 };
        var group = Guid.NewGuid().ToString("N");
        foreach (string format in _format.Items)
        {
            var symbol = format switch
            {
                "PNG" => Symbol.Pictures, "HTML" => Symbol.World,
                "RMG" => Symbol.Edit, _ => Symbol.Map,
            };
            var tile = new StackPanel { Spacing = 2 };
            var heading = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
            heading.Children.Add(new SymbolIcon(symbol));
            heading.Children.Add(ExportDesign.Text(format, true));
            tile.Children.Add(heading);
            var description = ExportDesign.Text(FormatDescription(format));
            ExportDesign.Secondary(description);
            tile.Children.Add(description);
            var radio = new RadioButton { GroupName = group, Content = tile,
                HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch,
                IsChecked = SelectedFormat == format, MinWidth = 0 };
            AutomationProperties.SetName(radio, UiLanguage.Text("ExportWindow_Preview_BuildFormatChoices_7A1260E05E") + " · " + format + " · " + FormatDescription(format));
            radio.Checked += (_, _) => { if (_format.SelectedItem as string != format) _format.SelectedItem = format; };
            _formatChoices.Add(format, radio);
            _editable.Add(radio);
            var card = ExportDesign.Card(radio);
            card.Padding = new Thickness(10, 5, 10, 5);
            cards.Children.Add(card);
        }
        void Arrange()
        {
            var tiles = cards.Children.OfType<FrameworkElement>().ToArray();
            var columns = Math.Max(1, Math.Min(tiles.Length, (int)((cards.ActualWidth + 8) / 220)));
            cards.ColumnDefinitions.Clear(); cards.RowDefinitions.Clear();
            for (var n = 0; n < columns; n++) cards.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            for (var n = 0; n < tiles.Length; n++)
            {
                if (n % columns == 0) cards.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                Grid.SetColumn(tiles[n], n % columns); Grid.SetRow(tiles[n], n / columns);
            }
        }
        cards.SizeChanged += (_, _) => Arrange();
        Arrange();
        return cards;
    }

    private static string FormatDescription(string format) => format switch
    {
        "SVG" => UiLanguage.Text("ExportWindow_RmpSvgDescription"),
        "PNG" => UiLanguage.Text("ExportWindow_Preview_FormatDescription_9962B1348A"),
        "HTML" => UiLanguage.Text("ExportWindow_Preview_FormatDescription_95E163193C"),
        "RMG" => UiLanguage.Text("ExportWindow_Preview_FormatDescription_C04E46FB03"),
        _ => UiLanguage.Text("ExportWindow_Preview_FormatDescription_D1B9C29526"),
    };

    private (string Title, string Icon) LayerPresentation(string key) => key switch
    {
        "satellite" => (UiLanguage.Text("Satellite_LayerTitle"), "layer.terrain"),
        "lines" => (UiLanguage.Text("ExportWindow_Preview_LayerPresentation_223B24B821"), "context.lines"),
        "stations" => (UiLanguage.Text("ExportWindow_Preview_LayerPresentation_53E88DD68F"), "context.stations"),
        "facility_names" => (_context == "transit" ? UiLanguage.Text("ExportWindow_Preview_LayerPresentation_CF2382056B") : UiLanguage.Text("ExportWindow_Preview_LayerPresentation_4AA668A329"), "context.names"),
        "line_labels" => (UiLanguage.Text("ExportWindow_Preview_LayerPresentation_2035432E8A"), "context.names"),
        "facilities" => (_context == "transit" ? UiLanguage.Text("ExportWindow_Preview_LayerPresentation_4C2772DFEA") : UiLanguage.Text("ExportWindow_Preview_LayerPresentation_22CD02F940"),
            _context == "transit" ? "label.transit_icon" : "context.facilities"),
        "base_water" => (UiLanguage.Text("ExportWindow_Preview_LayerPresentation_784E7FAA7F"), "context.water"),
        "base_road" => (UiLanguage.Text("ExportWindow_Preview_LayerPresentation_BB06598E0D"), "layer.road"),
        "base_district" => (UiLanguage.Text("ExportWindow_Preview_LayerPresentation_A3883FE83C"), "layer.district"),
        _ => (RibbonIcons.LayerTitle(key), "layer." + key),
    };

    private Border CreateLayerState(string title, string iconId, bool enabled, string state)
    {
        var row = new Grid { ColumnSpacing = 8 };
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        row.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var icon = RibbonIcons.Create(iconId);
        icon.Width = icon.Height = 20;
        row.Children.Add(icon);
        var text = ExportDesign.Text(title + " · " + state);
        Grid.SetColumn(text, 1); row.Children.Add(text);
        var border = ExportDesign.Card(row);
        border.Padding = new Thickness(8, 6, 8, 6);
        border.CornerRadius = new CornerRadius(4);
        AutomationProperties.SetName(border, text.Text);
        void ApplyColors()
        {
            if (_appearancePalettes is not { } palettes) return;
            var transparent = new SolidColorBrush(Microsoft.UI.Colors.Transparent);
            if (new Windows.UI.ViewManagement.AccessibilitySettings().HighContrast)
            {
                var foreground = (Brush)Application.Current.Resources["SystemControlForegroundBaseHighBrush"];
                text.Foreground = icon.Foreground = foreground;
                border.Background = transparent;
                border.BorderBrush = enabled ? foreground : transparent;
                return;
            }
            var palette = palettes.GetProperty(border.ActualTheme == ElementTheme.Dark ? "dark" : "light");
            SolidColorBrush Color(string token) => new(AppearanceControls.ParseColor(palette.GetProperty(token).GetString()!));
            icon.Foreground = Color(enabled ? "PRIMARY" : "DISABLED");
            text.Foreground = Color(enabled ? "TOGGLE_TEXT" : "TEXT_MUTED");
            border.Background = enabled ? Color("TOGGLE_BG") : transparent;
            border.BorderBrush = enabled ? Color("TOGGLE_BORDER") : transparent;
        }
        border.Loaded += (_, _) => ApplyColors();
        border.ActualThemeChanged += (_, _) => ApplyColors();
        return border;
    }

    private void BuildRangeControls(StackPanel extent)
    {
        foreach (var radio in new[] { _rangeFull, _rangeEntry, _rangeCustom }) _editable.Add(radio);
        extent.Children.Add(_rangeControls);
        _rangeFull.Checked += (_, _) =>
        {
            if (_syncingRange) return;
            _customRegion = null; _useActionRegion.IsChecked = false;
            UpdateDimensions(); UpdateContextCopy();
        };
        _rangeEntry.Checked += (_, _) =>
        {
            if (_syncingRange) return;
            _customRegion = null; _useActionRegion.IsChecked = true;
            UpdateDimensions(); UpdateContextCopy();
        };
        _rangeCustom.Checked += async (_, _) =>
        {
            if (_syncingRange) return;
            if (_customRegion is null) await ChooseRangeAsync();
            else { _useActionRegion.IsChecked = false; UpdateDimensions(); }
            SyncRangeControls();
        };
        SyncRangeControls();
    }

    private void SyncRangeControls()
    {
        _syncingRange = true;
        _rangeEntry.Visibility = _initialRegion is null ? Visibility.Collapsed : Visibility.Visible;
        _rangeEntry.IsEnabled = !_busy && !_choosing && !_engineFailed && _initialRegion is not null;
        var chosen = _useActionRegion.IsChecked == true && _initialRegion is not null
            ? _rangeEntry : _customRegion is not null ? _rangeCustom : _rangeFull;
        chosen.IsChecked = true;
        _chooseRange.Visibility = ShowsRange && chosen == _rangeCustom ? Visibility.Visible : Visibility.Collapsed;
        _syncingRange = false;
        DrawRangeOutline();
    }

    private void DrawRangeOutline() => _rangeCanvas.Select(EffectiveBounds());

    private Control BuildDensityControls(JsonElement profile)
    {
        foreach (var (value, label) in PoiProfileEditorPanel.DensityChoices)
            _densityTier.Items.Add(new ComboBoxItem { Content = label, Tag = value });
        LoadDensityProfile(profile);
        _densityEnabled.Checked += async (_, _) => await SavePoiProfileFieldAsync("density_enabled", true);
        _densityEnabled.Unchecked += async (_, _) => await SavePoiProfileFieldAsync("density_enabled", false);
        _densityTier.SelectionChanged += async (_, _) =>
        {
            if (_densityTier.SelectedItem is ComboBoxItem item)
                await SavePoiProfileFieldAsync("density_tier", (string)item.Tag);
        };
        var content = new StackPanel { Spacing = 4 };
        content.Children.Add(_densityTier);
        content.Children.Add(_densityEnabled);
        content.Children.Add(_pngInternalBuildings);
        _pngInternalBuildings.Checked += async (_, _) => await SavePoiProfileFieldAsync("png_internal_buildings", true);
        _pngInternalBuildings.Unchecked += async (_, _) => await SavePoiProfileFieldAsync("png_internal_buildings", false);
        var help = ExportDesign.Text(UiLanguage.Text("ExportWindow_DensityHelp"));
        ExportDesign.Secondary(help);
        content.Children.Add(help);
        _densitySection = new ContentControl { Content = content,
            HorizontalContentAlignment = HorizontalAlignment.Stretch, IsTabStop = false };
        return _densitySection;
    }

    private void LoadDensityProfile(JsonElement profile)
    {
        _poiProfile = profile.Clone();
        _loadingDensity = true;
        try
        {
            _densityEnabled.IsChecked = profile.GetProperty("density_enabled").GetBoolean();
            _pngInternalBuildings.IsChecked = profile.GetProperty("png_internal_buildings").GetBoolean();
            _densityTier.SelectedItem = _densityTier.Items.Cast<ComboBoxItem>()
                .Single(item => (string)item.Tag == profile.GetProperty("density_tier").GetString());
            _densityTier.IsEnabled = _densityEnabled.IsChecked == true;
        }
        finally { _loadingDensity = false; }
    }

    private async Task SavePoiProfileFieldAsync(string key, object value)
    {
        if (_loadingDensity || !_ready || _savingDensity) return;
        _savingDensity = true;
        _densityTier.IsEnabled = _densityEnabled.IsChecked == true;
        RefreshControlStates();
        try
        {
            var result = await _engine.RequestAsync("setPoiProfileField", new
            {
                target = _context,
                key,
                value,
                documentEpoch = _documentEpoch,
            });
            LoadDensityProfile(result.GetProperty("profile"));
            if (_settingsChanged is not null) await _settingsChanged();
            await RefreshSharedSnapshotAsync();
            _status.Text = "";
        }
        catch (Exception error)
        {
            LoadDensityProfile(_poiProfile);
            ShowFailure(error);
        }
        finally { _savingDensity = false; RefreshControlStates(); }
    }

    private void BuildSizePresets(StackPanel image, bool metro)
    {
        if (metro) _sizePreset.Items.Add(new ComboBoxItem { Content = UiLanguage.Text("ExportWindow_Preview_BuildSizePresets_ED705D3508"), Tag = 0 });
        foreach (var n in new[] { 2048, 4096, 8192 })
            _sizePreset.Items.Add(new ComboBoxItem { Content = $"{n:N0} px", Tag = n });
        _sizePreset.Items.Add(new ComboBoxItem { Content = UiLanguage.Text("ExportWindow_Preview_BuildSizePresets_4EAFA9E925"), Tag = -1 });
        _sizePreset.SelectionChanged += async (_, _) =>
        {
            if (_syncingSize || _sizePreset.SelectedItem is not ComboBoxItem { Tag: int n }) return;
            _customSizeSelected = n < 0;
            if (n < 0)
            {
                SyncSizePreset();
                _size.DispatcherQueue.TryEnqueue(() => _size.Focus(FocusState.Programmatic));
                return;
            }
            _size.Value = _savedSize = n;
            SyncSizePreset();
            await CommitOutputOptionAsync("size", n);
        };
        _editable.Add(_sizePreset);
        ExportDesign.Secondary(_sizeHelp); ExportDesign.Secondary(_rotationHelp);
        _sizeFields.Children.Add(_sizePreset);
        _sizeFields.Children.Add(_size);
        _sizeFields.Children.Add(_dimensions);
        _sizeFields.Children.Add(_sizeHelp);
        _rotationFields.Children.Add(_rotation);
        _rotationFields.Children.Add(_rotationHelp);
        image.Children.Add(new ResponsiveFieldGrid(OutputFieldColumnBreakpoint, _sizeFields, _rotationFields));
        SyncSizePreset();
    }

    private void SyncSizePreset()
    {
        if (_exportRmp is not null) return;
        _syncingSize = true;
        _sizePreset.SelectedItem = _customSizeSelected ? _sizePreset.Items.OfType<ComboBoxItem>().Last()
            : _sizePreset.Items.OfType<ComboBoxItem>().FirstOrDefault(item => item.Tag is int n && n == _size.Value)
                ?? _sizePreset.Items.OfType<ComboBoxItem>().Last();
        _syncingSize = false;
        _sizeFields.Visibility = ShowsOutputField("size") ? Visibility.Visible : Visibility.Collapsed;
        _size.Visibility = ShowsOutputField("size") && _sizePreset.SelectedItem is ComboBoxItem { Tag: -1 }
            ? Visibility.Visible : Visibility.Collapsed;
        _rotationFields.Visibility = _rotation.Visibility;
    }

    private StackPanel BuildStatePane(JsonElement profile)
    {
        var pane = new StackPanel { Spacing = 6 };
        if (_draftState.HasValue)
        {
            pane.Children.Add(ExportDesign.Text(UiLanguage.Text("CityEditor_Draft"), true));
            pane.Children.Add(_contentSummary);
            pane.Children.Add(ExportDesign.Text(UiLanguage.Text("Export_DraftSettingsHelp")));
            var go = new Button { Content = UiLanguage.Text("Export_GoToDraft") };
            AutomationProperties.SetAutomationId(go, "export.goToDraft");
            go.Click += async (_, _) => { GoToDraft = true; await _window.RequestCloseAsync(); };
            _editable.Add(go);
            pane.Children.Add(go);
            return pane;
        }
        if (_exportRmp is not null)
        {
            pane.Children.Add(ExportDesign.Text(UiLanguage.Text("ExportWindow_Preview_BuildStatePane_2584921CF7"), true));
            pane.Children.Add(_contentSummary);
            return pane;
        }
        _rangePreview.Children.Add(ExportDesign.Text(UiLanguage.Text("ExportWindow_Preview_BuildStatePane_6FB4C5C8E4"), true));
        _rangePreview.Children.Add(_rangeCanvas);
        pane.Children.Add(_rangePreview);
        var theme = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 8 };
        _themeTitle.VerticalAlignment = VerticalAlignment.Center;
        theme.Children.Add(_themeImage);
        theme.Children.Add(_themeTitle);
        pane.Children.Add(theme);
        pane.Children.Add(ExportDesign.Text(UiLanguage.Text("ExportWindow_Preview_BuildStatePane_2584921CF7"), true));
        pane.Children.Add(_contentSummary);
        var details = new StackPanel { Spacing = 12 };
        details.Children.Add(_layersSection!);
        details.Children.Add(_pngSection);
        pane.Children.Add(new Expander { Header = UiLanguage.Text("ExportWindow_Preview_BuildStatePane_A41EB6B5A2"), Content = details,
            HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Stretch });
        UpdateTheme(profile);
        return pane;
    }

    private void UpdateTheme(JsonElement profile)
    {
        var name = ReadTheme(profile);
        _themeTitle.Text = name;
        if (profile.TryGetProperty("themeName", out var displayName)) _themeTitle.Text = displayName.GetString();
        // 只使用随程序提供的主题缩略图。
        var safe = Path.GetFileName(name);
        _themeImage.Source = safe == name && File.Exists(Path.Combine(AppContext.BaseDirectory, "Assets", "Themes", name + ".png"))
            ? new BitmapImage(new Uri($"ms-appx:///Assets/Themes/{name}.png")) : null;
        _themeImage.Visibility = _themeImage.Source is null ? Visibility.Collapsed : Visibility.Visible;
    }

    private static Grid BuildWorkspace(StackPanel settings, StackPanel state)
    {
        var grid = new Grid { RowSpacing = 8 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(0) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        grid.Children.Add(settings);
        grid.Children.Add(state);
        Grid.SetRow(state, 1);
        grid.SizeChanged += (_, args) =>
        {
            var wide = args.NewSize.Width >= 760;
            if (wide == (Grid.GetColumn(state) == 1)) return;
            grid.ColumnDefinitions[1].Width = wide ? new GridLength(260) : new GridLength(0);
            grid.ColumnSpacing = wide ? 16 : 0;
            grid.RowSpacing = wide ? 0 : 8;
            Grid.SetColumn(state, wide ? 1 : 0);
            Grid.SetRow(state, wide ? 0 : 1);
        };
        return grid;
    }

    private void InitializeResults()
    {
        _window.Activated += (_, args) =>
        {
            if (args.WindowActivationState != WindowActivationState.Deactivated) UpdateGameRunningNotice();
        };
        _feedback.RegisterPropertyChangedCallback(InfoBar.IsOpenProperty, (_, _) =>
            _feedback.Visibility = _feedback.IsOpen ? Visibility.Visible : Visibility.Collapsed);
        _window.Closed += (_, _) => _closed = true;
        _window.Root.Loaded += async (_, _) =>
        {
            await LoadRangeOverviewAsync();
        };
        _status.RegisterPropertyChangedCallback(TextBlock.TextProperty, (_, _) =>
        {
            _feedback.Content = null;
            var text = _status.Text ?? "";
            _feedback.Message = text;
            _feedback.IsOpen = text.Length != 0;
            _feedback.Visibility = text.Length != 0 ? Visibility.Visible : Visibility.Collapsed;
            _feedback.Severity = new[] { UiLanguage.Text("ExportWindow_Preview_InitializeResults_28384D7AFD"), UiLanguage.Text("ExportWindow_Preview_InitializeResults_402590868F"), UiLanguage.Text("ExportWindow_Preview_InitializeResults_60120FF05F"), UiLanguage.Text("ExportWindow_Preview_InitializeResults_1559A0A78F"), UiLanguage.Text("ExportWindow_Preview_InitializeResults_B7D961B0C1"), UiLanguage.Text("ExportWindow_Preview_InitializeResults_F9D19345A0") }.Any(text.Contains)
                ? InfoBarSeverity.Error : text.StartsWith(UiLanguage.Text("ExportWindow_Preview_InitializeResults_3E6886D17A")) || text.StartsWith(UiLanguage.Text("ExportWindow_Preview_InitializeResults_FA7DE3D933")) ? InfoBarSeverity.Success : InfoBarSeverity.Informational;
        });
        var retry = new Button { Content = UiLanguage.Text("ExportWindow_Preview_InitializeResults_0A07FEF6ED") };
        retry.Click += async (_, _) => await StartAsync();
        _feedback.ActionButton = retry;
        _feedback.RegisterPropertyChangedCallback(InfoBar.SeverityProperty, (_, _) =>
            retry.Visibility = _feedback.Severity == InfoBarSeverity.Error ? Visibility.Visible : Visibility.Collapsed);
        retry.Visibility = Visibility.Collapsed;
        _editable.Add(retry);
        _ready = true;
        UpdateGameRunningNotice();
    }

    private void UpdateGameRunningNotice()
    {
        var running = false;
        foreach (var name in new[] { "Cities", "Cities2" })
        {
            var processes = System.Diagnostics.Process.GetProcessesByName(name);
            running |= processes.Length != 0;
            foreach (var process in processes) process.Dispose();
        }
        _gameRunningNotice.IsOpen = running;
        _gameRunningNotice.Visibility = running ? Visibility.Visible : Visibility.Collapsed;
    }

    internal async Task RefreshSharedSnapshotAsync()
    {
        if (_draftState.HasValue || _exportRmp is not null || !_ready || _closed || _engineFailed || _busy || _choosing) return;
        if (_refreshingShared) { _refreshSharedAgain = true; return; }
        _refreshingShared = true;
        try
        {
            var profile = await _engine.RequestAsync("getExportProfile", new { context = _context, profileTarget = _profileTarget });
            if (_closed || _busy || _engineFailed) return;
            if (_densitySection is not null) LoadDensityProfile(profile.GetProperty("poiProfile"));
            var output = ReadOutput(profile);
            var shared = new JsonObject();
            if (profile.TryGetProperty("sharedFingerprint", out var fingerprint)) shared["style"] = fingerprint.GetString();
            if (profile.TryGetProperty("metroOptions", out var diagram)) shared["diagram"] = JsonNode.Parse(diagram.GetRawText());
            foreach (var key in new[] { "theme", "layers", "bounds", "lineIds", "lineCount", "transitOptions" })
            {
                if (output[key] is not { } value) continue;
                _output[key] = value.DeepClone(); shared[key] = value.DeepClone();
            }
            if (profile.TryGetProperty("poiVisibility", out var visibility))
                shared["poiVisibility"] = JsonNode.Parse(visibility.GetRawText());
            var signature = shared.ToJsonString();
            if (signature == _sharedSignature) return;
            _sharedSignature = signature;
            if (_summaryPanel is not null) RefreshSnapshotSummary(profile, _summaryPanel);
            UpdateTheme(profile); UpdateDimensions();
        }
        catch (Exception error) { if (!_closed) ShowFailure(error); }
        finally
        {
            _refreshingShared = false;
            if (_refreshSharedAgain) { _refreshSharedAgain = false; await RefreshSharedSnapshotAsync(); }
        }
    }

    private async Task LoadRangeOverviewAsync()
    {
        if (_draftState.HasValue || _context == "metro" || _closed || _engineFailed) return;
        try
        {
            var range = await _engine.RequestAsync("getRangeProfile");
            if (_closed || !range.GetProperty("available").GetBoolean()) return;
            await _rangeCanvas.LoadAsync(range, () => !_closed && !_engineFailed,
                loaded: () =>
                {
                    _rangePreview.Visibility = ShowsRange ? Visibility.Visible : Visibility.Collapsed;
                    DrawRangeOutline();
                });
        }
        catch (Exception error)
        {
            _rangePreview.Visibility = Visibility.Collapsed;
            if (!_closed) ShowFailure(error);
        }
    }
}
