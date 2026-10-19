// 管理地图导出窗口的选项和执行流程。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage;
using Windows.Storage.Streams;

namespace CSLModernMapWinUI;

internal sealed partial class ExportWindow
{
    private readonly EditorWindow _window;
    private readonly EngineClient _engine;
    private readonly Func<Task>? _editMapSettings;
    private readonly Func<Task>? _settingsChanged;
    private readonly Func<Uri, Task<bool>> _openUri;
    private readonly JsonElement _formatCapabilities;
    private readonly JsonElement? _appearancePalettes;
    private readonly string _context;
    private readonly string _cityName;
    private readonly string _profileTarget;
    private string _outputDirectory;
    private readonly JsonObject? _initialRegion;
    private JsonObject? _customRegion;
    private JsonObject _output;
    private readonly ComboBox _format = new() { Header = UiLanguage.Text("ExportWindow__format_7A1260E05E"), HorizontalAlignment = HorizontalAlignment.Right, Width = 180 };
    private readonly NumberBox _size = new() { Header = UiLanguage.Text("ExportWindow__size_0FE38504DD"), Minimum = 512, Maximum = 32768, SmallChange = 256 };
    private readonly ComboBox _rotation = new() { Header = UiLanguage.Text("ExportWindow__rotation_144514527B"), ItemsSource = new[] { 0, 90, 180, 270 } };
    private readonly CheckBox _pngInternalBuildings = new() { Content = UiLanguage.Text("Poi_PngInternalBuildings") };
    private readonly CheckBox _densityEnabled = new() { Content = UiLanguage.Text("PoiProfileEditorPanel_Build_C563296C44") };
    private readonly ComboBox _densityTier = new() { Header = UiLanguage.Text("PoiProfileEditorPanel_Build_163850E3D5"),
        HorizontalAlignment = HorizontalAlignment.Stretch };
    private Control? _densitySection;
    private JsonElement _poiProfile;
    private bool _loadingDensity, _savingDensity;
    private readonly CheckBox _useActionRegion = new() { Content = UiLanguage.Text("ExportWindow__useActionRegion_061399F78D") };
    private readonly CheckBox _autoOpen = new() { Content = UiLanguage.Text("ExportWindow__autoOpen_4986B00E95"), IsChecked = true };
    private readonly TextBlock _dimensions = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _range = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _contextTitle = new() { FontSize = 22, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold };
    private readonly TextBlock _contextDesc = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _status = new() { TextWrapping = TextWrapping.Wrap };
    private readonly Button _start;
    private readonly Button _cancel;
    private readonly Button _chooseRange = new() { Content = UiLanguage.Text("ExportWindow__chooseRange_A2BF7686B7") };
    private readonly Button _viewResult = new() { Content = UiLanguage.Text("ExportWindow__viewResult_D06BC6EFDD"), IsEnabled = false };
    private readonly Button _copyResult = new() { Content = UiLanguage.Text("ExportWindow__copyResult_189B772103"), IsEnabled = false };
    private readonly Button _openResultFolder = new() { Content = UiLanguage.Text("ExportWindow__openResultFolder_4A45AD491A"), IsEnabled = false };
    private Button? _resetOutputButton;
    private readonly Control _pngSection;
    private readonly Control _extentSection;
    private Control? _layersSection;
    private Control? _imageSection;
    private StackPanel? _summaryPanel;
    private JsonElement _summaryProfile;
    private readonly ProgressBar _progress = new() { Minimum = 0, Maximum = 100, Visibility = Visibility.Collapsed };
    private readonly List<Control> _editable = new();
    private bool _busy, _choosing, _engineFailed;
    private string? _requestId, _latestSuccessfulPng;
    private string? _activeContext, _activeFormat, _activeTarget;
    private int _activeDocumentEpoch;
    private readonly int _documentEpoch;
    private double _savedSize;
    private int _savedRotation;
    private Task _outputSaveTail = Task.CompletedTask;
    internal bool IsBusy => _busy;
    internal Window OwnerWindow => _window;

    public ExportWindow(EngineClient engine, JsonElement profile, object? region, string? format,
        Window? owner = null,
        Func<Task>? editMapSettings = null, Func<Uri, Task<bool>>? openUri = null,
        JsonElement? appearancePalettes = null, Func<Task>? settingsChanged = null,
        Func<string, string, RmpExportOptions, Task>? exportRmp = null)
    {
        _window = new(UiLanguage.Text("ExportWindow_View_456F962F93"), 1000, 760, translucent: true, owner: owner, modal: owner is not null) { ActionsRightAligned = true };
        _engine = engine;
        _exportRmp = exportRmp;
        if (exportRmp is not null) profile = RmpExportProfile(profile);
        _editMapSettings = editMapSettings;
        _settingsChanged = settingsChanged;
        _openUri = openUri ?? (async uri => await Windows.System.Launcher.LaunchUriAsync(uri));
        _appearancePalettes = appearancePalettes;
        _formatCapabilities = profile.GetProperty("formatCapabilities").Clone();
        _context = profile.GetProperty("context").GetString()!;
        _cityName = profile.GetProperty("cityName").GetString() ?? throw new JsonException("Export profile requires cityName.");
        _outputDirectory = profile.GetProperty("outputDir").GetString()!;
        _profileTarget = _context switch
        {
            "transit" => "transit_png",
            "metro" => "metro",
            _ => "normal_png",
        };
        _documentEpoch = profile.GetProperty("documentEpoch").GetInt32();
        _initialRegion = region is null ? null : JsonNode.Parse(JsonSerializer.Serialize(region))!.AsObject();
        _output = ReadOutput(profile);
        var metro = _context == "metro";

        foreach (var item in profile.GetProperty("formats").EnumerateArray()) _format.Items.Add(item.GetString()!);
        var currentFormat = profile.TryGetProperty("output", out var outputNode) && outputNode.TryGetProperty("format", out var savedFormat)
            ? savedFormat.GetString() : "PNG";
        _format.SelectedItem = format is not null && _format.Items.Contains(format)
            ? format : _format.Items.Contains(currentFormat) ? currentFormat : _format.Items[0];
        _size.Minimum = metro ? 0 : 512;
        _size.Value = (_output["size"] ?? throw new JsonException("Export profile requires size.")).GetValue<double>();
        _rotation.SelectedItem = (int)(_output["rotation"] ?? throw new JsonException("Export profile requires rotation.")).GetValue<double>();
        _useActionRegion.IsEnabled = _initialRegion is not null && !metro;
        _useActionRegion.IsChecked = _useActionRegion.IsEnabled;
        _format.SelectionChanged += (_, _) => { UpdateFormat(); UpdateDimensions(); UpdateContextCopy(); };
        _size.ValueChanged += (_, _) => { UpdateDimensions(); SyncSizePreset(); };
        _rotation.SelectionChanged += async (_, _) =>
        {
            UpdateDimensions();
            var rotation = (int)(_rotation.SelectedItem ?? 0);
            if (rotation == _savedRotation) return;
            _savedRotation = rotation;
            await CommitOutputOptionAsync("rotation", rotation);
        };
        _savedSize = _size.Value;
        _savedRotation = (int)(_rotation.SelectedItem ?? 0);
        _size.LostFocus += async (_, _) =>
        {
            if (_size.Value == _savedSize) return;
            if (!double.IsFinite(_size.Value) || _size.Value != Math.Truncate(_size.Value) || _size.Value < _size.Minimum || _size.Value > _size.Maximum)
            {
                _status.Text = UiLanguage.Text("ExportWindow_View_9E411D9C27");
                _size.Value = _savedSize;
                return;
            }
            _savedSize = _size.Value;
            await CommitOutputOptionAsync("size", (int)_savedSize);
        };
        _useActionRegion.Checked += (_, _) => { _customRegion = null; UpdateDimensions(); UpdateContextCopy(); };
        _useActionRegion.Unchecked += (_, _) => { UpdateDimensions(); UpdateContextCopy(); };
        _chooseRange.Click += async (_, _) => await ChooseRangeAsync();
        _viewResult.Click += async (_, _) => await ViewResultAsync();
        _copyResult.Click += async (_, _) => await CopyResultAsync();
        _openResultFolder.Click += async (_, _) => await OpenResultFolderAsync();

        var title = new StackPanel { Spacing = 4 };
        title.Children.Add(_contextTitle);
        title.Children.Add(_contextDesc);
        ExportDesign.Secondary(_contextDesc);
        var header = new StackPanel { Spacing = 12 };
        header.Children.Add(title);
        header.Children.Add(BuildFormatChoices());
        var settings = new StackPanel { Spacing = 12 };

        var extent = Section(settings, UiLanguage.Text("ExportWindow_View_000E18C870"));
        _extentSection = (Control)settings.Children.Last();
        BuildRangeControls(extent);

        var image = Section(settings, UiLanguage.Text("ExportWindow_View_349F3DEF65"));
        _imageSection = (Control)settings.Children.Last();
        if (_exportRmp is null) BuildSizePresets(image, metro);
        else BuildRmpExportControls(image);
        _resetOutputButton = new Button { Content = UiLanguage.Text("ExportWindow_View_54B91E5C45"), FontSize = 12,
            Padding = new Thickness(8, 4, 8, 4), HorizontalAlignment = HorizontalAlignment.Right };
        _resetOutputButton.Click += async (_, _) => await ResetOutputProfileAsync();
        var imageHeader = new Grid { ColumnSpacing = 12 };
        imageHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        imageHeader.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var imageTitle = image.Children[0]; image.Children.RemoveAt(0);
        imageHeader.Children.Add(imageTitle);
        Grid.SetColumn(_resetOutputButton, 1); imageHeader.Children.Add(_resetOutputButton);
        image.Children.Insert(0, imageHeader);
        if (!metro) BuildDensityControls(image, profile.GetProperty("poiProfile"));

        var poiContent = new StackPanel { Spacing = 8 };
        _pngSection = new ContentControl { Content = ExportDesign.Divider(poiContent),
            HorizontalContentAlignment = HorizontalAlignment.Stretch, IsTabStop = false };
        if (!metro && profile.TryGetProperty("poiProfile", out var poiProfile))
        {
            var edit = new Button { Content = UiLanguage.Text("ExportWindow_View_95170F6894"), IsEnabled = _editMapSettings is not null };
            edit.Click += async (_, _) =>
            {
                if (_editMapSettings is not null) await _editMapSettings();
                await RefreshSharedSnapshotAsync();
            };
            poiContent.Children.Add(edit);
        }
        else
        {
            _pngSection.Visibility = Visibility.Collapsed;
        }

        var layers = new StackPanel { Spacing = 8 };
        _layersSection = new ContentControl { Content = layers,
            HorizontalContentAlignment = HorizontalAlignment.Stretch, IsTabStop = false };
        _summaryPanel = layers;
        RefreshSnapshotSummary(profile, layers);
        var resultPane = BuildStatePane(profile);

        _resultActions.Children.Add(_viewResult);
        _resultActions.Children.Add(_copyResult);
        _resultActions.Children.Add(_openResultFolder);
        _resultActions.SizeChanged += (_, _) =>
            _resultActions.Orientation = _resultActions.ActualWidth >= 480 ? Orientation.Horizontal : Orientation.Vertical;

        var footer = new StackPanel { Spacing = 8 };
        footer.Children.Add(_progress);
        footer.Children.Add(_feedback);
        footer.Children.Add(_resultActions);
        var body = new Grid { RowSpacing = 12 };
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        body.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        body.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        body.Children.Add(header);
        var workspace = BuildWorkspace(settings, resultPane);
        Grid.SetRow(workspace, 1); body.Children.Add(workspace);
        var footerBorder = ExportDesign.Divider(footer);
        Grid.SetRow(footerBorder, 2); body.Children.Add(footerBorder);
        _window.SetBody(body);
        _editable.AddRange([_format, _size, _rotation, _autoOpen,
            _useActionRegion, _chooseRange, _resetOutputButton]);
        if (metro)
        {
            _rotation.Visibility = _extentSection.Visibility = Visibility.Collapsed;
            _pngSection.Visibility = Visibility.Collapsed;
        }
        _cancel = _window.AddAction(UiLanguage.Text("ExportWindow_View_2CD0F3BE87"), async () =>
        {
            if (_busy) await CancelAsync();
            else await _window.RequestCloseAsync();
        }, manageEnabled: false);
        _start = _window.AddAction(UiLanguage.Text("ExportWindow_View_76420433F2"), StartAsync, manageEnabled: false);
        _start.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        _autoOpen.VerticalAlignment = VerticalAlignment.Center;
        _autoOpen.HorizontalAlignment = HorizontalAlignment.Left;
        void ArrangeAutoOpen()
        {
            var compact = _window.Root.ActualWidth < 480;
            if (compact && _window.Root.Children.Contains(_autoOpen)) _window.Root.Children.Remove(_autoOpen);
            if (!compact && footer.Children.Contains(_autoOpen)) footer.Children.Remove(_autoOpen);
            if (compact && !footer.Children.Contains(_autoOpen)) footer.Children.Insert(0, _autoOpen);
            if (!compact && !_window.Root.Children.Contains(_autoOpen))
            { Grid.SetRow(_autoOpen, 1); _window.Root.Children.Add(_autoOpen); }
        }
        _window.Root.SizeChanged += (_, _) => ArrangeAutoOpen();
        ArrangeAutoOpen();
        _window.CanCloseAsync = () =>
        {
            if (_busy) _status.Text = TargetIsDirectory
                ? UiLanguage.Text("ExportWindow_View_BD768D3131")
                : UiLanguage.Text("ExportWindow_View_41A0338CF7");
            return Task.FromResult(!_busy && !_choosing);
        };
        UpdateFormat();
        UpdateDimensions();
        UpdateContextCopy();
        InitializeResults();
        _status.Text = "";
    }

    private static JsonObject ReadOutput(JsonElement profile)
    {
        if (profile.TryGetProperty("output", out var output) && output.ValueKind == JsonValueKind.Object)
            return JsonNode.Parse(output.GetRawText())!.AsObject();
        // 地铁继续使用独立的旧版交通选项。
        return JsonNode.Parse(JsonSerializer.Serialize(new
        {
            size = profile.GetProperty("size").GetDouble(),
            rotation = profile.GetProperty("rotation").GetDouble(),
            format = "PNG",
            bounds = JsonSerializer.Deserialize<double[]>(profile.GetProperty("bounds").GetRawText()),
            rangeSource = "city",
            theme = profile.TryGetProperty("theme", out var theme) ? theme.GetString() : "",
            layers = profile.TryGetProperty("layers", out var layers) ? JsonSerializer.Deserialize<JsonElement>(layers.GetRawText()) : default,
            lineIds = profile.TryGetProperty("lineIds", out var lineIds) ? JsonSerializer.Deserialize<JsonElement>(lineIds.GetRawText()) : default,
            lineCount = profile.TryGetProperty("lineCount", out var lineCount) ? lineCount.GetInt32() : 0,
        }))!.AsObject();
    }

    private string SelectedFormat => (string?)_format.SelectedItem ?? "PNG";

    private JsonElement SelectedFormatCapability => _formatCapabilities.GetProperty(SelectedFormat);
    private string? CapabilityString(string key) => SelectedFormatCapability.GetProperty(key).GetString();
    private bool CapabilityFlag(string key) => SelectedFormatCapability.GetProperty(key).GetBoolean();

    private bool ShowsOutputOptions => CapabilityFlag("showsOutputOptions");
    private bool ShowsRange => CapabilityFlag("showsRange");
    private bool ShowsPoiProfile => CapabilityFlag("showsPoiProfile");
    private bool ShowsSharedLayers => CapabilityFlag("showsSharedLayers");
    private bool ShowsTransitLines => CapabilityFlag("showsTransitLines");
    private bool ResultIsImage => CapabilityFlag("resultImage");
    private bool TargetIsDirectory => CapabilityString("targetKind") == "directory";
    private string? OutputProfileTarget => CapabilityString("outputProfileTarget");
    private bool ShowsOutputField(string field) => SelectedFormatCapability.GetProperty("outputFields")
        .EnumerateArray().Any(value => value.GetString() == field);

    private static string ReadTheme(JsonElement profile)
    {
        if (profile.TryGetProperty("output", out var output) && output.TryGetProperty("theme", out var theme))
            return theme.GetString() ?? "";
        return profile.TryGetProperty("theme", out var oldTheme) ? oldTheme.GetString() ?? "" : "";
    }

    private static StackPanel Section(StackPanel parent, string title)
    {
        var content = new StackPanel { Spacing = 8 };
        content.Children.Add(ExportDesign.Text(title, true));
        parent.Children.Add(new ContentControl { Content = ExportDesign.Divider(content),
            HorizontalContentAlignment = HorizontalAlignment.Stretch, IsTabStop = false });
        return content;
    }

    private static TextBlock Line(string label, string value) => new()
    {
        Text = $"{label}：{value}",
        TextWrapping = TextWrapping.Wrap,
    };

    // 五种上下文共用导出窗口，格式和范围只属于当前窗口。
    private void UpdateContextCopy()
    {
        if (_exportRmp is not null)
        {
            _contextTitle.Text = UiLanguage.Text("ExportWindow_UpdateContextCopy_FA5D23884E");
            _contextDesc.Text = UiLanguage.Text("ExportWindow_RmpContent");
            return;
        }
        if (SelectedFormat is "RMG" or "RMP")
        {
            _contextTitle.Text = SelectedFormat + UiLanguage.Text("ExportWindow_UpdateContextCopy_43C05A6D01");
            _contextDesc.Text = SelectedFormat == "RMP"
                ? UiLanguage.Text("ExportWindow_UpdateContextCopy_7BE47589C1")
                : UiLanguage.Text("ExportWindow_UpdateContextCopy_2F52FBFF32");
            ToolTipService.SetToolTip(_contextTitle, _contextDesc.Text);
            return;
        }
        var kind = _context switch
        {
            "metro" => "metro",
            "transit" => "transit",
            "selection" => "selection",
            "random_spot" => "random_spot",
            _ when _useActionRegion.IsChecked == true && _initialRegion is not null => "selection",
            _ => "normal",
        };
        var (title, description) = kind switch
        {
            "metro" => (UiLanguage.Text("ExportWindow_UpdateContextCopy_FA5D23884E"), UiLanguage.Text("ExportWindow_UpdateContextCopy_C25AEFA0CD")),
            "transit" => (UiLanguage.Text("ExportWindow_UpdateContextCopy_5A85707DE5"), UiLanguage.Text("ExportWindow_UpdateContextCopy_A164782976")),
            "selection" => (UiLanguage.Text("ExportWindow_UpdateContextCopy_E0961BFB18"), UiLanguage.Text("ExportWindow_UpdateContextCopy_3749B13C38")),
            "random_spot" => (UiLanguage.Text("ExportWindow_UpdateContextCopy_485DDBB48A"), UiLanguage.Text("ExportWindow_UpdateContextCopy_82BA1293BA")),
            _ => (UiLanguage.Text("ExportWindow_UpdateContextCopy_A5E8039E52"), UiLanguage.Text("ExportWindow_UpdateContextCopy_6339D3A748")),
        };
        _contextTitle.Text = title;
        _contextDesc.Text = description;
        ToolTipService.SetToolTip(_contextTitle, _contextDesc.Text);
    }

    private JsonObject? EffectiveRegion()
    {
        if (_useActionRegion.IsChecked == true && _initialRegion is not null)
            return (JsonObject)_initialRegion.DeepClone();
        return _customRegion is null ? null : (JsonObject)_customRegion.DeepClone();
    }

    private double[] EffectiveBounds()
    {
        if (EffectiveRegion() is { } region)
            return region["bounds"]!.AsArray().Select(item => item!.GetValue<double>()).ToArray();
        var bounds = (_output["bounds"] ?? throw new JsonException("Export profile requires bounds.")).AsArray();
        return bounds.Select(item => item!.GetValue<double>()).ToArray();
    }

    private void UpdateDimensions()
    {
        if (_context == "metro")
        {
            _dimensions.Text = _size.Value == 0 ? UiLanguage.Text("ExportWindow_UpdateDimensions_0820C7D712") : UiLanguage.Format("ExportWindow_UpdateDimensions_97DFD85740", (int)_size.Value);
            _range.Text = UiLanguage.Text("ExportWindow_UpdateDimensions_432581CED0");
            return;
        }
        var b = EffectiveBounds();
        var spanZ = b[3] - b[1];
        var spanX = b[2] - b[0];
        var rotated = (int)(_rotation.SelectedItem ?? 0) is 90 or 270;
        var width = rotated ? spanX : spanZ;
        var height = rotated ? spanZ : spanX;
        var length = Math.Max(width, height);
        var pixels = double.IsFinite(_size.Value) ? (int)_size.Value : 0;
        _dimensions.Text = length > 0 && pixels > 0
            ? UiLanguage.Format("ExportWindow_UpdateDimensions_DAC5591541", Math.Max(1, (int)Math.Round(pixels * width / length)), Math.Max(1, (int)Math.Round(pixels * height / length)))
            : UiLanguage.Text("ExportWindow_UpdateDimensions_5C5EC61CE8");
        var source = EffectiveRegion()?["source"]?.GetValue<string>() ??
            (_output["rangeSource"] ?? throw new JsonException("Export profile requires rangeSource.")).GetValue<string>();
        var rangeName = source switch
        {
            "selection" => UiLanguage.Text("ExportWindow_UpdateDimensions_149E2A7ED9"),
            "random_spot" => UiLanguage.Text("ExportWindow_UpdateDimensions_B4AD227A3F"),
            "custom" => UiLanguage.Text("ExportWindow_UpdateDimensions_AEFAC3869B"),
            _ => UiLanguage.Text("ExportWindow_UpdateDimensions_653AEA4D67"),
        };
        _range.Text = UiLanguage.Format("ExportWindow_UpdateDimensions_49C485EFFB", rangeName, height / 1000, width / 1000);
        SyncRangeControls();
    }

    private void UpdateFormat()
    {
        var png = (string?)_format.SelectedItem == "PNG";
        _pngSection.Visibility = png && ShowsPoiProfile ? Visibility.Visible : Visibility.Collapsed;
        if (_densitySection is not null) _densitySection.Visibility = png && ShowsPoiProfile ? Visibility.Visible : Visibility.Collapsed;
        _extentSection.Visibility = ShowsRange ? Visibility.Visible : Visibility.Collapsed;
        _imageSection!.Visibility = _exportRmp is not null || ShowsOutputField("size") || ShowsOutputField("rotation")
            ? Visibility.Visible : Visibility.Collapsed;
        _rotation.Visibility = ShowsOutputField("rotation") ? Visibility.Visible : Visibility.Collapsed;
        _dimensions.Visibility = ShowsOutputField("size") ? Visibility.Visible : Visibility.Collapsed;
        _range.Visibility = ShowsRange ? Visibility.Visible : Visibility.Collapsed;
        _chooseRange.Visibility = ShowsRange ? Visibility.Visible : Visibility.Collapsed;
        _useActionRegion.Visibility = ShowsRange && _initialRegion is not null ? Visibility.Visible : Visibility.Collapsed;
        _layersSection!.Visibility = ShowsSharedLayers || ShowsTransitLines ? Visibility.Visible : Visibility.Collapsed;
        if (_resetOutputButton is not null)
            _resetOutputButton.Visibility = OutputProfileTarget is null ? Visibility.Collapsed : Visibility.Visible;
        _output["format"] = (string?)_format.SelectedItem ?? "PNG";
        foreach (var (format, radio) in _formatChoices) radio.IsChecked = format == SelectedFormat;
        _rangePreview.Visibility = ShowsRange && _rangeWorld is not null ? Visibility.Visible : Visibility.Collapsed;
        RefreshSnapshotSummary(_summaryProfile, _summaryPanel!);
        if (_exportRmp is null) SyncSizePreset();
        else UpdateRmpExportControls();
        UpdateResultActions();
    }

    private void UpdateResultActions()
    {
        var canView = !_busy && !_engineFailed && _latestResultPath is not null;
        var canCopy = !_busy && !_engineFailed && ResultIsImage && _latestSuccessfulPng is not null;
        _viewResult.IsEnabled = canView;
        _openResultFolder.IsEnabled = canView;
        _copyResult.IsEnabled = canCopy;
        _viewResult.Visibility = _latestResultPath is not null ? Visibility.Visible : Visibility.Collapsed;
        _copyResult.Visibility = ResultIsImage && _latestSuccessfulPng is not null ? Visibility.Visible : Visibility.Collapsed;
        _openResultFolder.Visibility = _latestResultPath is not null && _latestResultFormat is not ("RMG" or "RMP")
            ? Visibility.Visible : Visibility.Collapsed;
        _resultActions.Visibility = _viewResult.Visibility == Visibility.Visible || _copyResult.Visibility == Visibility.Visible
            ? Visibility.Visible : Visibility.Collapsed;
        _viewResult.Content = _latestResultFormat is "RMG" or "RMP" ? UiLanguage.Text("ExportWindow_UpdateResultActions_4A45AD491A") : UiLanguage.Text("ExportWindow_UpdateResultActions_D06BC6EFDD");
    }

    private void RefreshSnapshotSummary(JsonElement profile, StackPanel target)
    {
        _summaryProfile = profile;
        target.Children.Clear();
        if (_exportRmp is not null)
        {
            _contentSummary.Text = UiLanguage.Text("ExportWindow_RmpContent");
            return;
        }
        var summary = new List<string>();
        var sharedCopy = ExportDesign.Text(UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_3E1BD838DD"));
        ExportDesign.Secondary(sharedCopy);
        target.Children.Add(sharedCopy);
        var output = profile.TryGetProperty("output", out var outputNode) ? outputNode : default;
        var layers = output.ValueKind == JsonValueKind.Object && output.TryGetProperty("layers", out var outputLayers)
            ? outputLayers : profile.TryGetProperty("layers", out var oldLayers) ? oldLayers : default;
        if (ShowsSharedLayers && layers.ValueKind == JsonValueKind.Object)
        {
            var rows = new List<FrameworkElement>();
            var visible = new List<string>();
            foreach (var item in layers.EnumerateObject())
            {
                var (title, icon) = LayerPresentation(item.Name);
                var enabled = item.Value.GetBoolean();
                var state = enabled ? UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_4E1449E7D5") : UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_145219E726");
                if (_context == "transit" && item.Name == "facility_names")
                {
                    var placement = output.GetProperty("transitOptions").GetProperty("station_name_placement").GetString();
                    enabled = placement != "hidden";
                    state = enabled ? UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_4E1449E7D5") : UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_145219E726");
                    if (enabled) state += " · " + (placement == "icon" ? UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_0A6C1B2384") : UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_66E80E45EF"));
                }
                rows.Add(CreateLayerState(title, icon, enabled, state));
                if (enabled) visible.Add(title);
            }
            target.Children.Add(new ResponsiveFieldGrid(320, rows.ToArray()));
            summary.Add(UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_8499E380DF") + "：" + (visible.Count == 0 ? UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_484D556139") : string.Join("、", visible)));
        }
        var poiVisibility = profile.TryGetProperty("poiVisibility", out var visibility) ? visibility : default;
        if (ShowsSharedLayers && poiVisibility.ValueKind == JsonValueKind.Object && poiVisibility.TryGetProperty("icons", out var icons) &&
            poiVisibility.TryGetProperty("names", out var namesSnapshot))
        {
            var groupNames = new Dictionary<string, string>
            {
                ["area"] = UiLanguage.Text("Poi_Area"),
                ["transit"] = UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_ED867CCBF0"), ["facility"] = UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_22CD02F940"), ["ordinary"] = UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_85155A34A5"),
            };
            foreach (var (key, label) in groupNames)
            {
                if (!icons.TryGetProperty(key, out var icon) || !namesSnapshot.TryGetProperty(key, out var name)) continue;
                var state = Line(label, UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_EA3DB27869") + (icon.GetBoolean() ? UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_4E1449E7D5") : UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_145219E726")) +
                    UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_5BA40AB50B") + (icon.GetBoolean() && name.GetBoolean() ? UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_4E1449E7D5") : UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_145219E726")));
                ExportDesign.Secondary(state); target.Children.Add(state);
            }
        }
        var lineIds = output.ValueKind == JsonValueKind.Object && output.TryGetProperty("lineIds", out var outputLineIds)
            ? outputLineIds : profile.TryGetProperty("lineIds", out var oldLineIds) ? oldLineIds : default;
        var lineCount = output.ValueKind == JsonValueKind.Object && output.TryGetProperty("lineCount", out var outputLineCount)
            ? outputLineCount : profile.TryGetProperty("lineCount", out var oldLineCount) ? oldLineCount : default;
        if (ShowsTransitLines && lineCount.ValueKind == JsonValueKind.Number)
        {
            var selected = lineIds.ValueKind == JsonValueKind.Array
                ? string.Format(UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_9DDE733D10"), lineIds.GetArrayLength(), lineCount.GetInt32())
                : string.Format(UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_D929FD1353"), lineCount.GetInt32());
            target.Children.Add(Line(UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_223B24B821"), selected));
            summary.Insert(0, UiLanguage.Text("ExportWindow_RefreshSnapshotSummary_223B24B821") + "：" + selected);
        }
        _contentSummary.Text = string.Join("\n", summary);
    }

    private async Task ChooseRangeAsync()
    {
        if (_busy || _choosing) return;
        _choosing = true;
        RefreshControlStates();
        try
        {
            var range = await _engine.RequestAsync("getRangeProfile");
            var window = new MapRangeWindow(_engine, range, EffectiveBounds(), owner: _window);
            if (!await window.ShowAsync() || window.AppliedRegion is null) return;
            _customRegion = JsonNode.Parse(window.AppliedRegion.Value.GetRawText())!.AsObject();
            _useActionRegion.IsChecked = false;
            UpdateDimensions();
            UpdateContextCopy();
        }
        catch (Exception error) { _status.Text = error.Message; }
        finally { _choosing = false; RefreshControlStates(); }
    }

    private async Task<string?> ChooseTargetAsync()
    {
        if (TargetIsDirectory)
        {
            var picker = new Microsoft.Windows.Storage.Pickers.FolderPicker(_window.AppWindow.Id);
            return (await picker.PickSingleFolderAsync())?.Path;
        }
        var format = SelectedFormat;
        var savePicker = new Microsoft.Windows.Storage.Pickers.FileSavePicker(_window.AppWindow.Id)
        {
            SuggestedFolder = _outputDirectory,
            SuggestedFileName = Path.GetFileNameWithoutExtension(SuggestedFileNameFor(_cityName, _context, format, DateTime.Now)),
            DefaultFileExtension = FileExtension(format),
        };
        savePicker.FileTypeChoices.Add(format, new List<string> { FileExtension(format) });
        return (await savePicker.PickSaveFileAsync())?.Path;
    }

    private static string FileExtension(string format) => format switch
    {
        "PNG" => ".png",
        "HTML" => ".html",
        "SVG" => ".svg",
        "RMP" => ".rmp.json",
        _ => throw new ArgumentOutOfRangeException(nameof(format), format, UiLanguage.Text("ExportWindow_FileExtension_32CAD75E83")),
    };

    internal static string SuggestedFileNameFor(string cityName, string context, string format, DateTime time)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var city = new string(cityName.Select(ch => invalid.Contains(ch) ? '_' : ch).ToArray()).TrimEnd(' ', '.');
        var view = context switch { "transit" => UiLanguage.Text("ExportWindow_SuggestedFileNameFor_5A85707DE5"), "metro" => UiLanguage.Text("ExportWindow_SuggestedFileNameFor_FA5D23884E"), _ => UiLanguage.Text("ExportWindow_SuggestedFileNameFor_41AB2E0ECB") };
        return $"{city}_{view}_{time:yyyyMMdd-HHmmss}{FileExtension(format)}";
    }

    private async Task ResetOutputProfileAsync()
    {
        var target = OutputProfileTarget;
        if (target is null || !ShowsOutputOptions) return;
        try
        {
            await _outputSaveTail;
            var keys = new[] { "size", "rotation" }.Where(ShowsOutputField).ToArray();
            if (keys.Length == 0) return;
            var result = await _engine.RequestAsync("resetProfileFields", new
            {
                kind = "output",
                target,
                keys,
                documentEpoch = _documentEpoch,
            });
            var profile = result.GetProperty("profile");
            _customSizeSelected = false;
            foreach (var key in keys)
            {
                if (!profile.TryGetProperty(key, out var value)) continue;
                _output[key] = JsonNode.Parse(value.GetRawText());
                switch (key)
                {
                    case "size": _size.Value = _savedSize = value.GetDouble(); break;
                    case "rotation": _savedRotation = value.GetInt32(); _rotation.SelectedItem = _savedRotation; break;
                }
            }
            UpdateDimensions();
            SyncSizePreset();
            _status.Text = UiLanguage.Text("ExportWindow_ResetOutputProfileAsync_23EE320097");
        }
        catch (Exception error) { _status.Text = UiLanguage.Text("ExportWindow_ResetOutputProfileAsync_288BFB28A8") + error.Message; }
    }

    private async Task SaveOutputOptionAsync(string key, object value)
    {
        if (_context == "metro")
        {
            await _engine.RequestAsync("setTransitOption", new { metro = true, key = "size", value });
            return;
        }
        var target = OutputProfileTarget;
        if (target is null) return;
        var previous = _outputSaveTail;
        async Task Execute()
        {
            await previous;
            await _engine.RequestAsync("setOutputOption", new
            {
                profileTarget = target,
                key,
                value,
                documentEpoch = _documentEpoch,
            });
        }
        _outputSaveTail = Execute();
        await _outputSaveTail;
    }

    private async Task CommitOutputOptionAsync(string key, object value)
    {
        if (!ShowsOutputField(key)) return;
        _output[key] = JsonSerializer.SerializeToNode(value);
        try
        {
            await SaveOutputOptionAsync(key, value);
            _status.Text = "";
        }
        catch (Exception error) { _status.Text = UiLanguage.Text("ExportWindow_CommitOutputOptionAsync_F4E15E33A6") + error.Message; }
    }

    public void Activate() => _window.Activate();
    public Task ShowAsync() => _window.ShowAsync();
    public Task<bool> RequestCloseAsync() => _window.RequestCloseAsync();
    public void ForceClose() => _window.ForceClose();

    private void SetBusy(bool busy)
    {
        _busy = busy;
        _progress.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        if (busy) { _progress.Value = 0; _progress.IsIndeterminate = true; }
        RefreshControlStates();
    }

    private void RefreshControlStates()
    {
        var canEdit = !_busy && !_choosing && !_engineFailed && !_savingDensity;
        _cancel.Content = _busy ? UiLanguage.Text("ExportWindow_RefreshControlStates_537D17F1C5") : UiLanguage.Text("ExportWindow_RefreshControlStates_2CD0F3BE87");
        _cancel.IsEnabled = _busy ? _exportRmp is null && !_engineFailed && !TargetIsDirectory : !_choosing;
        _start.IsEnabled = canEdit;
        UpdateResultActions();
        foreach (var control in _editable)
            control.IsEnabled = canEdit && (control != _useActionRegion || _initialRegion is not null);
        _pngSection.IsEnabled = canEdit;
        if (_densitySection is not null) _densitySection.IsEnabled = canEdit;
        SyncRangeControls();
    }

    private async Task CancelAsync()
    {
        if (!_busy) return;
        try
        {
            var result = await _engine.RequestAsync("cancel");
            if (result.GetProperty("cancelled").GetBoolean()) { SetBusy(false); _status.Text = UiLanguage.Text("ExportWindow_CancelAsync_A37778F17C"); }
            else _status.Text = result.TryGetProperty("message", out var message) ? message.GetString() : UiLanguage.Text("ExportWindow_CancelAsync_BD768D3131");
        }
        catch (Exception error) { _status.Text = error.Message; }
    }

    private async Task StartAsync()
    {
        if (_busy || _choosing || _savingDensity) return;
        _choosing = true;
        RefreshControlStates();
        string? target;
        try { target = await ChooseTargetAsync(); }
        catch (Exception error) { _status.Text = error.Message; return; }
        finally { _choosing = false; RefreshControlStates(); }
        if (target is not null) await StartExportAsync(target);
    }

    // 冻结当前导出选项并启动所选格式的输出作业。
    private async Task StartExportAsync(string target)
    {
        if (_busy || _choosing || _savingDensity) return;
        _choosing = true;
        RefreshControlStates();
        string? previousSuccessfulPng = null;
        string? previousResultPath = _latestResultPath, previousResultFormat = _latestResultFormat;
        var clearedPreviousView = false;
        var clearedPreviousResult = false;
        try
        {
            if (_exportRmp is not null)
            {
                await ExportRmpFileAsync(target);
                return;
            }
            if (ShowsOutputField("size") && (!double.IsFinite(_size.Value) || _size.Value != Math.Truncate(_size.Value)))
                throw new ArgumentException(UiLanguage.Text("ExportWindow_StartAsync_71F2754B3A"));
            var format = SelectedFormat;
            _outputDirectory = TargetIsDirectory ? target : Path.GetDirectoryName(target)!;
            clearedPreviousView = _latestResultPath is not null && PathsEqual(_latestResultPath, target);
            if (clearedPreviousView) _latestResultPath = null;
            await _outputSaveTail;

            var latest = await _engine.RequestAsync("getExportProfile", new
            {
                context = _context,
                profileTarget = _profileTarget,
            });
            var currentEpoch = latest.GetProperty("documentEpoch").GetInt32();
            if (_summaryPanel is not null) RefreshSnapshotSummary(latest, _summaryPanel);

            JsonObject? latestOutput = null;
            if (ShowsOutputOptions && latest.TryGetProperty("output", out var outputSnapshot) &&
                outputSnapshot.ValueKind == JsonValueKind.Object)
            {
                latestOutput = JsonNode.Parse(outputSnapshot.GetRawText())!.AsObject();
                latestOutput["format"] = format;
                if (ShowsOutputField("size")) latestOutput["size"] = (int)_size.Value;
                if (ShowsOutputField("rotation")) latestOutput["rotation"] = (int)(_rotation.SelectedItem ?? 0);
                _output = (JsonObject)latestOutput.DeepClone();
            }

            var region = ShowsRange ? EffectiveRegion() : null;
            JsonNode? poiVisibility = ShowsSharedLayers && latest.TryGetProperty("poiVisibility", out var latestVisibility)
                ? JsonNode.Parse(latestVisibility.GetRawText()) : null;

            if (ResultIsImage)
            {
                previousSuccessfulPng = _latestSuccessfulPng;
                clearedPreviousResult = previousSuccessfulPng is not null && PathsEqual(previousSuccessfulPng, target);
            }
            else
            {
                previousSuccessfulPng = _latestSuccessfulPng;
                clearedPreviousResult = previousSuccessfulPng is not null;
            }
            if (clearedPreviousResult)
            {
                _latestSuccessfulPng = null;
                UpdateResultActions();
            }

            SetBusy(true);
            _requestId = Guid.NewGuid().ToString("N");
            _activeContext = _context;
            _activeFormat = format;
            _activeTarget = target;
            _activeDocumentEpoch = currentEpoch;
            if (CapabilityString("context") == "metro" && format == "PNG")
                await _engine.RequestAsync("setTransitOption", new { metro = true, key = "size", value = (int)_size.Value });
            var request = new Dictionary<string, object?>
            {
                ["requestId"] = _requestId,
                ["documentEpoch"] = currentEpoch,
                ["context"] = _context,
                ["format"] = format,
                ["target"] = target,
                ["auto_open"] = false,
            };
            if (OutputProfileTarget is { } outputTarget)
            {
                request["profileTarget"] = outputTarget;
                if (latestOutput is not null)
                {
                    var outputParameters = new JsonObject();
                    foreach (var key in new[] { "size", "rotation" })
                        if (ShowsOutputField(key) && latestOutput[key] is { } value)
                            outputParameters[key] = value.DeepClone();
                    request["output"] = outputParameters;
                }
            }
            if (poiVisibility is not null) request["poiVisibility"] = poiVisibility;
            if (ShowsRange) request["region"] = region;
            var result = await _engine.RequestAsync("export", request);
            if (!result.GetProperty("started").GetBoolean())
            {
                if (clearedPreviousView) { _latestResultPath = previousResultPath; _latestResultFormat = previousResultFormat; }
                if (clearedPreviousResult)
                {
                    _latestSuccessfulPng = previousSuccessfulPng;
                    UpdateResultActions();
                }
                throw new InvalidOperationException(UiLanguage.Text("ExportWindow_StartAsync_BD768D3131"));
            }
            if (_busy) _status.Text = UiLanguage.Text("ExportWindow_StartAsync_62731D95BC");
        }
        catch (Exception error)
        {
            SetBusy(false);
            _status.Text = UiLanguage.Text("ExportWindow_StartAsync_C55E9D64E5") + error.Message;
        }
        finally
        {
            _choosing = false;
            RefreshControlStates();
        }
    }

    public void HandleEngineFailure(string message)
    {
        _choosing = false;
        _engineFailed = true;
        _latestSuccessfulPng = null;
        _latestResultPath = null;
        SetBusy(false);
        _status.Text = UiLanguage.Text("ExportWindow_HandleEngineFailure_969E2FF3CB") + message + UiLanguage.Text("ExportWindow_HandleEngineFailure_4C92803985");
    }

    public async Task HandleEventAsync(string name, JsonElement payload)
    {
        if (!_busy) return;
        if (!PayloadMatchesActiveRequest(payload)) return;
        if (name == "progress")
        {
            _progress.Visibility = Visibility.Visible;
            _progress.IsIndeterminate = false;
            _progress.Value = payload.TryGetProperty("percent", out var percent) ? percent.GetDouble() : _progress.Value;
            _status.Text = payload.TryGetProperty("message", out var message) ? message.GetString() : UiLanguage.Text("ExportWindow_HandleEventAsync_62731D95BC");
            return;
        }
        if (name == "error")
        {
            SetBusy(false);
            _status.Text = UiLanguage.Text("ExportWindow_HandleEventAsync_C55E9D64E5") + payload.GetProperty("message").GetString();
            return;
        }
        if (name == "exportCancelled")
        {
            SetBusy(false);
            _status.Text = UiLanguage.Text("ExportWindow_HandleEventAsync_A37778F17C");
            return;
        }
        if (name == "externalExportDone")
        {
            var directory = payload.TryGetProperty("directory", out var resultDirectory) ? resultDirectory.GetString() : null;
            if (!ValidExternalDirectory(directory, _activeTarget))
            {
                SetBusy(false);
                _status.Text = UiLanguage.Text("ExportWindow_HandleEventAsync_1DDF09A6A1");
                return;
            }
            _latestResultPath = directory; _latestResultFormat = _activeFormat;
            SetBusy(false);
            _progress.Value = 100;
            _status.Text = UiLanguage.Text("ExportWindow_HandleEventAsync_E050BB2FC4") + directory;
            if (payload.TryGetProperty("warnings", out var warnings))
                foreach (var warning in warnings.EnumerateArray()) _status.Text += "\n" + warning.GetString();
            if (_activeFormat == "RMP")
            {
                _status.Text += UiLanguage.Text("ExportWindow_HandleEventAsync_64A2BC1A37");
                try
                {
                    if (!await _openUri(new Uri("https://railmapgen.github.io/rmp/")))
                        _status.Text += UiLanguage.Text("ExportWindow_HandleEventAsync_B462F65DA8");
                }
                catch (Exception error) { _status.Text += UiLanguage.Text("ExportWindow_HandleEventAsync_BC69D07A28") + error.Message; }
            }
            if (_autoOpen.IsChecked == true && directory is not null)
            {
                try { await Windows.System.Launcher.LaunchFolderPathAsync(directory); }
                catch (Exception error) { _status.Text += UiLanguage.Text("ExportWindow_HandleEventAsync_518A6808EA") + error.Message; }
            }
            return;
        }
        if (name == "exportDone" && payload.TryGetProperty("requestId", out var id) && id.GetString() == _requestId)
        {
            var resultPath = payload.GetProperty("path").GetString();
            if (!ValidResultPath(resultPath, _activeTarget, _activeFormat))
            {
                SetBusy(false);
                _status.Text = UiLanguage.Text("ExportWindow_HandleEventAsync_C92A908F84");
                return;
            }
            _latestResultPath = resultPath; _latestResultFormat = _activeFormat;
            SetBusy(false);
            _progress.Value = 100;
            if (_activeFormat == "PNG" && resultPath is not null)
            {
                _latestSuccessfulPng = resultPath;
            }
            UpdateResultActions();
            _status.Text = UiLanguage.Text("ExportWindow_HandleEventAsync_76AA0EDA7A") + resultPath;
            if (payload.TryGetProperty("warnings", out var renderWarnings))
                foreach (var warning in renderWarnings.EnumerateArray()) _status.Text += "\n" + warning.GetString();
            if (_autoOpen.IsChecked == true && resultPath is not null)
            {
                try { await Windows.System.Launcher.LaunchFolderPathAsync(Path.GetDirectoryName(resultPath)!); }
                catch (Exception error) { _status.Text += UiLanguage.Text("ExportWindow_HandleEventAsync_518A6808EA") + error.Message; }
            }
        }
    }

    private bool PayloadMatchesActiveRequest(JsonElement payload)
    {
        return payload.TryGetProperty("requestId", out var requestId) && requestId.ValueKind == JsonValueKind.String &&
            requestId.GetString() == _requestId &&
            payload.TryGetProperty("documentEpoch", out var epoch) && epoch.ValueKind == JsonValueKind.Number &&
            epoch.GetInt32() == _activeDocumentEpoch &&
            payload.TryGetProperty("context", out var context) && context.GetString() == _activeContext &&
            payload.TryGetProperty("format", out var format) && format.GetString() == _activeFormat &&
            payload.TryGetProperty("target", out var target) && target.GetString() == _activeTarget;
    }

    private static bool ValidResultPath(string? path, string? requestedTarget, string? format)
    {
        if (path is null || requestedTarget is null || format is not ("PNG" or "HTML")) return false;
        try
        {
            var actual = Path.GetFullPath(path);
            var expected = Path.GetFullPath(requestedTarget);
            var expectedDirectory = Path.GetDirectoryName(expected) ?? "";
            var actualDirectory = Path.GetDirectoryName(actual) ?? "";
            var extension = FileExtension(format);
            return string.Equals(expectedDirectory, actualDirectory, StringComparison.OrdinalIgnoreCase) &&
                string.Equals(Path.GetExtension(actual), extension, StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    private static bool ValidExternalDirectory(string? path, string? requestedRoot)
    {
        if (path is null || requestedRoot is null) return false;
        try
        {
            var actual = Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
            var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(requestedRoot));
            return string.Equals(actual, root, StringComparison.OrdinalIgnoreCase) ||
                actual.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
                actual.StartsWith(root + Path.AltDirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    private static bool PathsEqual(string first, string second)
    {
        try { return string.Equals(Path.GetFullPath(first), Path.GetFullPath(second), StringComparison.OrdinalIgnoreCase); }
        catch { return string.Equals(first, second, StringComparison.OrdinalIgnoreCase); }
    }

    private async Task ViewResultAsync()
    {
        if (_latestResultPath is null) return;
        try
        {
            if (_latestResultFormat is "RMG" or "RMP")
                await Windows.System.Launcher.LaunchFolderPathAsync(_latestResultPath);
            else
                await Windows.System.Launcher.LaunchFileAsync(await StorageFile.GetFileFromPathAsync(_latestResultPath));
        }
        catch (Exception error) { _status.Text = UiLanguage.Text("ExportWindow_ViewResultAsync_B2B91741A7") + error.Message; }
    }

    private async Task CopyResultAsync()
    {
        if (_latestSuccessfulPng is null) return;
        try
        {
            var file = await StorageFile.GetFileFromPathAsync(_latestSuccessfulPng);
            var package = new DataPackage();
            package.SetBitmap(RandomAccessStreamReference.CreateFromFile(file));
            Clipboard.SetContent(package);
            Clipboard.Flush();
            _status.Text = UiLanguage.Text("ExportWindow_CopyResultAsync_5DD07B5878");
        }
        catch (Exception error) { _status.Text = UiLanguage.Text("ExportWindow_CopyResultAsync_02F9E1FA9F") + error.Message; }
    }

    private async Task OpenResultFolderAsync()
    {
        if (_latestResultPath is null) return;
        try { await Windows.System.Launcher.LaunchFolderPathAsync(Path.GetDirectoryName(_latestResultPath)!); }
        catch (Exception error) { _status.Text = UiLanguage.Text("ExportWindow_OpenResultFolderAsync_E49A46DADE") + error.Message; }
    }
}
