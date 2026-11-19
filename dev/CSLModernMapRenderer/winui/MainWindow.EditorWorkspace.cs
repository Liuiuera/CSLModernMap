// 城市作品工作区复用主题字段和对象检查器，统一承载导航、命令及草稿配置。
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;
using RibbonSpace;
using RibbonSpace.Controls;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private ThemeEditorDialog? _artworkEditor;
    private RibbonTab? _artworkTab, _tabBeforeArtwork;
    private RibbonGallery? _artworkThemeGallery;
    private readonly ContentControl _artworkNavigationHost = new() { HorizontalContentAlignment = HorizontalAlignment.Stretch, VerticalContentAlignment = VerticalAlignment.Stretch };
    private readonly ScrollViewer _artworkDraftScroll = new() { HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled,
        HorizontalContentAlignment = HorizontalAlignment.Stretch };
    private readonly StackPanel _artworkDraftPanel = new() { Spacing = 12, Padding = new Thickness(12) };
    private readonly TextBlock _artworkSummary = new() { TextWrapping = TextWrapping.Wrap };
    private readonly TextBlock _artworkSaveState = new() { TextWrapping = TextWrapping.Wrap, FontSize = 12 };
    private readonly StackPanel _artworkToolbar = new() { Orientation = Orientation.Horizontal, Spacing = 6, Visibility = Visibility.Collapsed };
    private readonly TextBlock _artworkObservation = new() { Text = "100%", VerticalAlignment = VerticalAlignment.Center };
    private readonly Button _artworkReset = new() { Height = 32, MinHeight = 32, Padding = new Thickness(8, 3, 8, 3) };
    private DraftResolutionInput? _artworkResolution;
    private DraftDensityInput? _artworkDensity;
    private bool _customResolutionAtEntry;
    private ElementScaleInput? _artworkElementScale;
    private ComboBox? _artworkRotation;
    private JsonElement? _artworkConfig;
    private (bool Left, bool Right) _panelsBeforeArtwork;
    private Task _artworkWriteTail = Task.CompletedTask;
    private long _artworkCloseupGeneration;
    private JsonElement? _pendingArtworkCloseup;
    private bool _openingArtwork, _leavingArtwork, _artworkEntryPreparing;
    private string _artworkTool = "select";
    private static string EditorText(string key) => UiLanguage.Text("CityEditor_" + key);

    private void BuildArtworkRibbon()
    {
        var tab = _artworkTab = new RibbonTab { Header = EditorText("Title") };
        void Add(RibbonGroup group, string title, string id, Func<Task> action, bool large = false)
        {
            var icon = (BitmapIcon)RibbonIcons.Create(id, large: true);
            if (!large) icon.Width = icon.Height = 20;
            var button = new RibbonButton { Label = title, Icon = icon,
                Size = large ? RibbonItemSize.Large : RibbonItemSize.Medium };
            // RibbonSpace 复制 IconElement，资源地址变化后需刷新显示副本。
            icon.RegisterPropertyChangedCallback(BitmapIcon.UriSourceProperty, (_, _) => { button.Icon = null; button.Icon = icon; });
            AutomationProperties.SetAutomationId(button, id);
            ToolTipService.SetToolTip(button, title);
            RegisterRibbonColors(button, icon, CommandDomain(id), large);
            RegisterCommand(id, action, true, button);
            button.Click += async (_, _) => await ExecuteCommandAsync(id);
            group.Items.Add(button);
        }
        var themes = Group(tab, EditorText("Theme"));
        _artworkThemeGallery = CreateThemeGallery();
        AutomationProperties.SetAutomationId(_artworkThemeGallery, "editor.themes");
        themes.Items.Add(_artworkThemeGallery);
        Add(themes, UiLanguage.Text("ThemeEditor_New"), "editor.new", () => _artworkEditor!.NewAsync());
        Add(themes, UiLanguage.Text("ThemeEditor_Copy"), "editor.copy", () => _artworkEditor!.CopyAsync());
        Add(themes, UiLanguage.Text("ThemeEditor_Rename"), "editor.rename", () => _artworkEditor!.RenameAsync());
        Add(themes, UiLanguage.Text("ThemeEditorDialog_Layout_BuildWorkbench_BA1125B2AC"), "editor.json", () => _artworkEditor!.EditJsonAsync());
        Add(themes, UiLanguage.Text("ThemeEditor_Import"), "editor.importTheme", () => _artworkEditor!.ImportAsync());
        Add(themes, UiLanguage.Text("ThemeEditor_Export"), "editor.exportTheme", () => _artworkEditor!.ExportAsync());
        Add(themes, UiLanguage.Text("ThemeUpdate_Review"), "editor.presetUpdate", () => _artworkEditor!.ReviewPresetUpdateAsync());
        var edit = Group(tab, EditorText("Edit"));
        Add(edit, EditorText("Undo"), "editor.undo", () => { _artworkEditor!.Undo(); return Task.CompletedTask; });
        Add(edit, EditorText("Redo"), "editor.redo", () => { _artworkEditor!.Redo(); return Task.CompletedTask; });
        var view = Group(tab, EditorText("View"));
        Add(view, EditorText("Fit"), "editor.fit", () => ObserveArtworkAsync("fit"));
        Add(view, EditorText("Actual"), "editor.actual", () => ObserveArtworkAsync("actual"));
        var output = Group(tab, EditorText("Output"));
        Add(output, EditorText("Save"), "editor.save", async () => { await _artworkEditor!.SaveAsync(); }, true);
        Add(output, EditorText("Export"), "editor.export", () => ShowExportAsync("PNG"), true);
        Add(Group(tab, EditorText("Close")), EditorText("Leave"), "editor.leave", async () => { await TryLeaveArtworkEditorAsync(); }, true);
        foreach (var (label, action) in new[] { ("−", "zoom-out"), ("+", "zoom-in"), (EditorText("Fit"), "fit"), ("100%", "actual") }) {
            var button = new Button { Content = label, Height = 32, MinHeight = 32, Padding = new Thickness(8, 3, 8, 3) };
            button.Click += (_, _) => _ = ObserveArtworkAsync(action); _artworkToolbar.Children.Add(button);
        }
        _artworkToolbar.Children.Add(_artworkObservation);
        _artworkReset.Content = EditorText("ResetCanvas");
        ToolTipService.SetToolTip(_artworkReset, EditorText("ResetCanvasHelp"));
        AutomationProperties.SetAutomationId(_artworkReset, "editor.resetCanvas");
        _artworkReset.Click += async (_, _) => await RunAsync(ResetArtworkCanvasAsync);
        _artworkToolbar.Children.Add(_artworkReset);
        Grid.SetColumn(_artworkToolbar, 2); MapToolbarHeader.Children.Add(_artworkToolbar);
    }

    private bool EditorCommandEnabled(string id)
    {
        if (!_cityArtwork || _artworkEditor is not { } editor || _leavingArtwork) return false;
        return id switch {
            "editor.leave" => !editor.IsBusy,
            "editor.save" => editor.CanSave,
            "editor.undo" => editor.CanUndo,
            "editor.redo" => editor.CanRedo,
            "editor.export" => !editor.IsBusy && !_cityArtworkExporting && !editor.HasValidationErrors,
            _ => !editor.IsBusy
        };
    }

    internal bool IsTransitDraft => _cityArtwork && _context == "transit";
    internal bool TransitDraftChanged => IsTransitDraft && _snapshot.TryGetProperty("transitDraftChanged", out var dirty) && dirty.GetBoolean();

    internal async Task SaveTransitDraftOptionsAsync()
    {
        await FlushContextWritesAsync();
        if (!TransitDraftChanged) return;
        await _engine.RequestAsync("saveDraftTransitOptions", new { documentEpoch = _engineDocumentEpoch });
        await RefreshSnapshotAsync();
    }

    private CityDraftWindow? _cityDraftWindow;

    private async Task CreateCityArtworkAsync()
    {
        if (_openingArtwork || _cityArtwork) return;
        _openingArtwork = true;
        try
        {
            if (_themeEditor is { } standalone && !await standalone.RequestCloseAsync()) return;
            if (_mapPoiSettingsWindow is { } mapSettings && !await mapSettings.RequestCloseAsync()) return;
            await FlushContextWritesAsync();
            var profile = await _engine.RequestAsync("getRangeProfile");
            var outputProfile = await _engine.RequestAsync("getExportProfile", new { context = _context });
            var create = _cityDraftWindow = new CityDraftWindow(_engine, profile, _selectedBounds,
                _snapshot.GetProperty("output").GetProperty("rotation").GetInt32(), this, outputProfile.GetProperty("poiProfile"));
            var options = await create.ShowAsync();
            if (options is null) return;
            _artworkEntryPreparing = true; ApplyMapTaskState();
            var draft = await _engine.RequestAsync("getThemeDraft");
            await BeginCityArtworkCoreAsync(options.LongEdge, options.Rotation, options.Region, options.ElementScale, options.PoiDensity);
            _customResolutionAtEntry = options.CustomResolution;
            await MountArtworkEditorAsync(draft);
        }
        finally
        {
            _cityDraftWindow = null;
            _openingArtwork = _artworkEntryPreparing = false;
            ApplyMapTaskState(); UpdateCommandStates();
        }
    }

    private ComboBox ArtworkRotationInput(int selected)
    {
        var input = new ComboBox { Header = EditorText("Rotation"), HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var angle in new[] { 0, 90, 180, 270 }) {
            var item = new ComboBoxItem { Content = $"{angle}°", Tag = angle }; input.Items.Add(item);
            if (angle == selected) input.SelectedItem = item;
        }
        AutomationProperties.SetAutomationId(input, "editor.rotation"); return input;
    }

    private async Task MountArtworkEditorAsync(JsonElement draft)
    {
        if (!_cityArtwork) return;
        _panelsBeforeArtwork = (_settingsOpen, _inspectorOpen);
        _tabBeforeArtwork = Ribbon.SelectedTab;
        _artworkEditor = ThemeEditorDialog.CreateEmbedded(this, _engine, Root.XamlRoot, draft, PreviewEditorThemeAsync);
        var editor = _artworkEditor;
        editor.StateChanged += (_, _) => { UpdateThemeGalleries(); UpdateArtworkSaveState(); UpdateCommandStates(); };
        if (IsTransitDraft) editor.FocusStyle("transports");
        var focusedStyle = (editor.CurrentSection, editor.CurrentRoadLevel, editor.CurrentLabelType);
        editor.NavigationChanged += (_, _) => {
            var current = (editor.CurrentSection, editor.CurrentRoadLevel, editor.CurrentLabelType);
            if (focusedStyle != current) {
                focusedStyle = current;
                ++_artworkCloseupGeneration; _pendingArtworkCloseup = null;
                SendArtworkCommand("cancelArtworkLocate", new { });
            }
            ShowArtworkProperties();
        };
        editor.CloseupRequested += (_, _) => _ = RunAsync(() => FocusArtworkStyleAsync(editor));
        UpdateThemeGalleries();
        _artworkNavigationHost.Content = editor.NavigationContent;
        BuildArtworkSidebar();
        if (IsTransitDraft)
        {
            await RefreshSnapshotAsync();
            var schema = await _engine.RequestAsync("getTransitSchema", new { metro = false });
            ShowPublicTransitSettings(schema, _artworkTransitPanel);
        }
        _settingsOpen = true;
        if (_artworkTab is not null && !Ribbon.Tabs.Contains(_artworkTab)) Ribbon.Tabs.Add(_artworkTab);
        Ribbon.SelectedTab = _artworkTab;
        ShowArtworkProperties();
        await RefreshArtworkConfigurationAsync();
        ApplyMapTaskState(); UpdateScaleAndLayout();
    }

    private ScrollViewer? _artworkTransitScroll;
    private SelectorBar? _artworkPages;
    private SelectorBarItem? _artworkStylesTab, _artworkDraftTab;
    private readonly StackPanel _artworkTransitPanel = new() { Padding = new Thickness(12), Spacing = 8 };
    private void BuildArtworkSidebar()
    {
        ArtworkSettingsPlaceholder.Children.Clear(); ArtworkSettingsPlaceholder.RowDefinitions.Clear();
        ArtworkSettingsPlaceholder.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        ArtworkSettingsPlaceholder.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        var tabs = _artworkPages = new SelectorBar();
        AutomationProperties.SetAutomationId(tabs, "editor.sidebarTabs");
        var style = _artworkStylesTab = new SelectorBarItem { Text = EditorText("Styles") };
        var settings = _artworkDraftTab = new SelectorBarItem { Text = EditorText("Draft") };
        var transit = new SelectorBarItem { Text = LayerTitle("metro") };
        if (IsTransitDraft) tabs.Items.Add(transit);
        tabs.Items.Add(style);
        tabs.Items.Add(settings);
        ArtworkSettingsPlaceholder.Children.Add(tabs);
        Grid.SetRow(_artworkNavigationHost, 1); ArtworkSettingsPlaceholder.Children.Add(_artworkNavigationHost);
        _artworkDraftScroll.IsEnabled = false;
        _artworkDraftPanel.Children.Clear();
        _artworkDraftPanel.Children.Add(_artworkSummary);
        var resolution = _artworkResolution = new DraftResolutionInput();
        var sizeGroup = ExportDesign.Group(EditorText("Resolution"));
        sizeGroup.Children.Add(resolution);
        _artworkDraftPanel.Children.Add(sizeGroup);
        var scaleGroup = ExportDesign.Group(EditorText("ElementSize"));
        _artworkElementScale = new ElementScaleInput(resolution.Edge, 1, compact: true);
        scaleGroup.Children.Add(_artworkElementScale);
        _artworkDraftPanel.Children.Add(scaleGroup);
        _artworkRotation = ArtworkRotationInput(0);
        _artworkRotation.SelectionChanged += (_, _) => {
            if (_artworkConfig is { } config)
                resolution.SetGeometry(DraftBounds(config), (int)((ComboBoxItem)_artworkRotation.SelectedItem).Tag);
        };
        _artworkDraftPanel.Children.Add(_artworkRotation);
        var densityGroup = ExportDesign.Group(EditorText("IconDensity"));
        _artworkDensity = new DraftDensityInput(stacked: true);
        densityGroup.Children.Add(_artworkDensity);
        _artworkDraftPanel.Children.Add(densityGroup);
        void Action(string key, string id, Func<Task> action) {
            var button = new Button { Content = EditorText(key), HorizontalAlignment = HorizontalAlignment.Stretch };
            AutomationProperties.SetAutomationId(button, id);
            button.Click += async (_, _) => await RunAsync(action); _artworkDraftPanel.Children.Add(button);
        }
        Action("ApplyCanvas", "editor.applyCanvas", () => UpdateArtworkConfigurationAsync());
        _artworkDraftPanel.Children.Add(new TextBlock { Text = EditorText("RangeHelp"), TextWrapping = TextWrapping.Wrap, FontSize = 12 });
        Action("SelectRange", "editor.selectRange", () => { SendViewer(new { type = "beginSelection" }); return Task.CompletedTask; });
        Action("UseSelection", "editor.useSelection", () => _selectedBounds is { } bounds
            ? UpdateArtworkConfigurationAsync(new { bounds, source = "selection" }, true)
            : Task.FromException(new InvalidOperationException(EditorText("NoSelection"))));
        Action("UseWholeCity", "editor.wholeCity", () => UpdateArtworkConfigurationAsync(null, true));
        var scroll = _artworkDraftScroll;
        if (IsTransitDraft)
        {
            _artworkTransitPanel.Children.Clear();
            var transitScroll = _artworkTransitScroll = new ScrollViewer { Content = _artworkTransitPanel, HorizontalContentAlignment = HorizontalAlignment.Stretch };
            Grid.SetRow(transitScroll, 1); ArtworkSettingsPlaceholder.Children.Add(transitScroll);
        }
        Grid.SetRow(scroll, 1); ArtworkSettingsPlaceholder.Children.Add(scroll);
        void ShowPage()
        {
            _artworkNavigationHost.Visibility = tabs.SelectedItem == style ? Visibility.Visible : Visibility.Collapsed;
            scroll.Visibility = tabs.SelectedItem == settings ? Visibility.Visible : Visibility.Collapsed;
            if (_artworkTransitScroll is { } transitScroll)
                transitScroll.Visibility = tabs.SelectedItem == transit ? Visibility.Visible : Visibility.Collapsed;
            ShowArtworkProperties();
        }
        tabs.SelectedItem = IsTransitDraft ? transit : style;
        tabs.SelectionChanged += (_, _) => ShowPage();
        ShowPage();
    }

    private static double[] DraftBounds(JsonElement config) =>
        config.GetProperty("region").GetProperty("bounds").EnumerateArray().Select(point => point.GetDouble()).ToArray();

    private void ShowArtworkProperties(ObjectInspectorPanel? facts = null)
    {
        if (_artworkEditor is not { } editor) return;
        ClearLayerPanel(InspectorPanel);
        if (facts is not null) InspectorPanel.Children.Add(WorkspaceSection.Create(EditorText("ObjectFacts"), facts, false));
        if (IsTransitDraft && _artworkNavigationHost.Visibility != Visibility.Visible)
        {
            _inspectorOpen = facts is not null;
            UpdateScaleAndLayout();
            return;
        }
        InspectorPanel.Children.Add(new TextBlock { Text = EditorText("Scope"), TextWrapping = TextWrapping.Wrap, FontSize = 12 });
        InspectorPanel.Children.Add(editor.PropertiesContent);
        InspectorPanel.Children.Add(_artworkSaveState);
        _inspectorOpen = true; UpdateArtworkSaveState(); UpdateScaleAndLayout();
    }

    private async Task FocusArtworkStyleAsync(ThemeEditorDialog editor)
    {
        var generation = ++_artworkCloseupGeneration;
        _pendingArtworkCloseup = null;
        if (OperationError.Message == EditorText("CloseupUnavailable")) OperationError.IsOpen = false;
        var section = editor.CurrentSection;
        if (!_cityArtwork || editor != _artworkEditor || section is "__basic__" or "canvas" or "watermark") return;
        var epoch = _engineDocumentEpoch;
        var roadLevel = editor.CurrentRoadLevel;
        var labelType = editor.CurrentLabelType;
        var result = await _engine.RequestAsync("getCityArtworkCloseup", new { documentEpoch = epoch, section, roadLevel, labelType });
        if (!_cityArtwork || editor != _artworkEditor || epoch != _engineDocumentEpoch || generation != _artworkCloseupGeneration ||
            section != editor.CurrentSection || roadLevel != editor.CurrentRoadLevel || labelType != editor.CurrentLabelType) return;
        if (result.GetProperty("bounds").ValueKind == JsonValueKind.Null) { ShowNotice(EditorText("CloseupUnavailable")); return; }
        _pendingArtworkCloseup = result.GetProperty("bounds").Clone();
        TryFocusArtworkStyle();
    }

    private void TryFocusArtworkStyle()
    {
        if (!_cityArtwork || _cityArtworkUpdating || _pendingArtworkCloseup is not { } bounds) return;
        SendArtworkCommand("locateArtwork", new { worldBounds = bounds });
        _pendingArtworkCloseup = null;
    }

    private void UpdateArtworkSaveState()
    {
        if (_artworkEditor is not { } editor) return;
        _artworkSaveState.Text = editor.HasUnsavedChanges ? EditorText("Unsaved") : EditorText("Saved");
        if (!string.IsNullOrEmpty(editor.StatusText)) _artworkSaveState.Text += " · " + editor.StatusText;
        if (_cityArtworkUpdating) _artworkSaveState.Text += " · " + EditorText("Updating");
    }

    internal Task PreviewEditorThemeAsync(JsonElement data)
    {
        var epoch = _engineDocumentEpoch;
        var editor = _artworkEditor;
        var frozen = data.Clone();
        var theme = editor?.WorkingThemeId;
        return QueueArtworkWriteAsync(async () => {
            if (!_cityArtwork || epoch != _engineDocumentEpoch || editor != _artworkEditor) return;
            var operation = StartCityArtworkOperation("update");
            try {
                await _engine.RequestAsync("previewCityArtworkTheme", new { documentEpoch = epoch, operationId = operation.Id, data = frozen, theme, debounce = true });
                await RefreshArtworkConfigurationAsync(loadInputs: false);
            }
            catch { if (_cityArtworkOperation?.Id == operation.Id) { _cityArtworkUpdating = false; ApplyMapTaskState(); } throw; }
        });
    }

    private Task QueueArtworkWriteAsync(Func<Task> write)
    {
        var previous = _artworkWriteTail;
        async Task Write() { await previous; await write(); }
        var task = Write();
        _artworkWriteTail = task.ContinueWith(_ => { }, TaskScheduler.Default);
        return task;
    }

    private async Task RefreshArtworkConfigurationAsync(bool loadInputs = true)
    {
        var epoch = _engineDocumentEpoch;
        var editor = _artworkEditor;
        var state = await _engine.RequestAsync("getCityArtworkState", new { documentEpoch = epoch });
        if (!_cityArtwork || editor != _artworkEditor || epoch != _engineDocumentEpoch || !state.GetProperty("active").GetBoolean()) return;
        var first = _artworkConfig is null;
        _artworkConfig = state.GetProperty("config").Clone();
        _cityArtworkRevision = state.GetProperty("revision").GetInt32();
        var config = _artworkConfig.Value;
        var bounds = DraftBounds(config);
        var rotation = config.GetProperty("rotation").GetInt32();
        var scale = config.GetProperty("elementScale").GetDouble();
        if (first || loadInputs)
        {
            _artworkRotation!.SelectedItem = _artworkRotation.Items.OfType<ComboBoxItem>().Single(item => (int)item.Tag == rotation);
            _artworkResolution!.Load(bounds, rotation, config.GetProperty("longEdge").GetInt32(),
                first ? _customResolutionAtEntry : _artworkResolution.Custom);
            _artworkElementScale!.Input.Value = scale * 100;
            _artworkDensity!.Load(config.GetProperty("poiDensity"));
            _artworkDraftScroll.IsEnabled = true;
        }
        editor?.SetElementScale(scale);
        var source = config.GetProperty("region").GetProperty("source").GetString();
        var range = source switch {
            "city" => EditorText("WholeCity"),
            "custom" => UiLanguage.Text("ExportWindow_Preview__rangeCustom_4EAFA9E925"),
            _ => EditorText("SelectedRange")
        };
        _artworkSummary.Text = $"{EditorText("Range")} · {range} · {(bounds[3] - bounds[1]) / 1000:0.###} × {(bounds[2] - bounds[0]) / 1000:0.###} km\n" +
            $"{config.GetProperty("width").GetInt32()} × {config.GetProperty("height").GetInt32()} px · {rotation}°";
    }

    private async Task UpdateArtworkConfigurationAsync(object? region = null, bool changeRegion = false)
    {
        if (!_cityArtwork || _artworkEditor is not { } editor || _artworkResolution?.IsValid != true)
            throw new InvalidOperationException(EditorText("InvalidSize"));
        if (_artworkElementScale?.IsValid != true)
            throw new InvalidOperationException(UiLanguage.Text("Pixel_Scale") + " · " + UiLanguage.Text("Pixel_InvalidScale"));
        var edge = (int)_artworkResolution.Edge.Value;
        var scale = _artworkElementScale.ElementScale;
        var density = _artworkDensity!.Snapshot();
        var rotation = (int)((ComboBoxItem)_artworkRotation!.SelectedItem).Tag;
        ++_artworkCloseupGeneration; _pendingArtworkCloseup = null;
        if (!await editor.FlushForExportAsync()) return;
        await QueueArtworkWriteAsync(async () => {
            if (!_cityArtwork || editor != _artworkEditor) return;
            var operation = StartCityArtworkOperation("update");
            var values = new Dictionary<string, object?> { ["documentEpoch"] = operation.Epoch, ["operationId"] = operation.Id,
                ["expectedRevision"] = _cityArtworkRevision, ["longEdge"] = edge, ["rotation"] = rotation,
                ["elementScale"] = scale, ["poiDensity"] = density, ["debounce"] = true };
            if (changeRegion) values["region"] = region;
            try { await _engine.RequestAsync("updateCityArtwork", values); await RefreshArtworkConfigurationAsync(); }
            catch { _cityArtworkUpdating = false; ApplyMapTaskState(); throw; }
        });
    }

    private Task SetArtworkToolAsync(string tool)
    {
        _artworkTool = tool;
        SendArtworkCommand("setArtworkTool", new { tool }); return Task.CompletedTask;
    }
    private Task ObserveArtworkAsync(string action)
    { SendArtworkCommand("artworkObserve", new { action }); return Task.CompletedTask; }
    private void SendArtworkCommand(string type, object values)
    {
        var data = JsonSerializer.SerializeToNode(values)!.AsObject();
        data["type"] = type; data["sceneId"] = _sceneId; data["revision"] = _revision;
        data["documentEpoch"] = _engineDocumentEpoch; data["artworkRevision"] = _cityArtworkRevision;
        SendViewer(data);
    }

    private async Task<bool> TryLeaveArtworkEditorAsync()
    {
        if (!_cityArtwork) return true;
        if (_leavingArtwork) return false;
        _leavingArtwork = true;
        UpdateCommandStates();
        try
        {
            await FlushContextWritesAsync();
            if (_artworkEditor is { } editor && !await editor.ConfirmLeaveAsync()) return false;
            await _artworkWriteTail; await LeaveCityArtworkAsync(); return true;
        }
        finally { _leavingArtwork = false; UpdateCommandStates(); }
    }

    private void UnmountArtworkEditor()
    {
        ++_artworkCloseupGeneration; _pendingArtworkCloseup = null;
        if (_artworkEditor is null) return;
        _artworkEditor.Invalidate(); _artworkEditor = null;
        _artworkNavigationHost.Content = null;
        _artworkStylesTab = _artworkDraftTab = null; _artworkPages = null; _customResolutionAtEntry = false;
        if (_artworkTransitScroll is { } transitScroll) { transitScroll.Content = null; _artworkTransitScroll = null; }
        _artworkTransitPanel.Children.Clear();
        UpdateThemeGalleries();
        ArtworkSettingsPlaceholder.Children.Clear();
        ClearLayerPanel(InspectorPanel);
        if (_artworkTab is not null) Ribbon.Tabs.Remove(_artworkTab);
        if (_tabBeforeArtwork is { } tab && Ribbon.Tabs.Contains(tab)) Ribbon.SelectedTab = tab;
        (_settingsOpen, _inspectorOpen) = _panelsBeforeArtwork;
        _artworkConfig = null; _artworkTool = "select";
        ShowInspectorHome(); UpdateScaleAndLayout();
    }
}
