// 初始化主窗口并连接主要界面事件。
using System.Collections.ObjectModel;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Microsoft.UI.Xaml.Navigation;
using Microsoft.UI.Xaml.Input;
using Microsoft.Web.WebView2.Core;
using RibbonSpace;
using RibbonSpace.Controls;
using Windows.System;
using Windows.Storage.Pickers;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow : Window
{
    private readonly EngineClient _engine;
    private readonly string _ribbonLayoutDirectory;
    private readonly JsonElement? _initialMetadata;
    private bool _showLanguageNotice;
    private bool _initialized;
    private readonly string[] _arguments;
    private readonly Dictionary<string, RibbonSplitButton> _layerButtons = new();
    private readonly List<Control> _documentControls = new();
    private JsonElement _snapshot;
    private int _engineDocumentEpoch;
    private JsonElement? _selectedBounds;
    private string? _selectedRegionSource;
    private string _sceneId = "";
    private int _revision;
    private bool _viewerReady;
    private bool _awaitingScene;
    private bool _closing;
    private ThemeGalleryBinding? _themeGalleries;
    private RibbonGallery? _poiGallery;
    private readonly ObservableCollection<PoiStyleChoice> _poiChoices =
        [new PoiStyleChoice("modern", UiLanguage.Text("MainWindow_File_BuildFileMenu_AA0819DFC4")),
         new PoiStyleChoice("modern-dark", UiLanguage.Text("MainWindow_File_BuildFileMenu_A6B75D0680"))];
    private readonly ObservableCollection<ThemeGalleryChoice> _themeChoices = new();
    private bool _syncingPoiGallery;
    private MenuFlyout? _recentMenu;
    private RibbonTab? _transitTab;
    private RibbonTab? _metroTab;
    private RibbonGroup? _metroOutputGroup;
    private RibbonGroup? _transitOutputGroup;
    private RibbonGroup? _transitCloseGroup;
    private RibbonGroup? _metroCloseGroup;
    private string _context = "map";
    private ThemeEditorDialog? _themeEditor;
    private string _themeLoadErrorShown = "";
    private PoiMappingDialog? _poiEditor;

    public MainWindow(string[] arguments) : this(arguments, new EngineClient(), null) { }

    internal MainWindow(string[] arguments, EngineClient engine, JsonElement? metadata, bool showLanguageNotice = false,
        string? ribbonLayoutDirectory = null)
    {
        _arguments = arguments;
        _engine = engine;
        _ribbonLayoutDirectory = ribbonLayoutDirectory ?? UserData.DirectoryPath;
        _previewQuality = UserData.ReadPreviewQuality(_ribbonLayoutDirectory);
        _initialMetadata = metadata;
        _showLanguageNotice = showLanguageNotice;
        _ = UiLanguage.Language;
        try { InitializeComponent(); }
        catch (Exception ex) { StartupTrace.Write("MainWindow XAML exception: " + ex); throw; }

        _artworkDraftScroll.Content = _artworkDraftPanel;
        Title = "CSLModernMap";
        SizeInitialWindow();
        // 主题变化通知中避免重写功能区资源。
        Root.ActualThemeChanged += (_, _) => Root.DispatcherQueue.TryEnqueue(ApplyRibbonColors);
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "app.ico"));
        SystemBackdrop = new WorkspaceBackdrop();

        BuildRibbon();
        LanguageName.Text = UiLanguage.Options.First(option => option.code == UiLanguage.Language).nativeName;
        var languageLabel = UiLanguage.Text("MainWindow_xaml_BuildRibbon_3D13868593") + " / Language";
        ToolTipService.SetToolTip(LanguageButton, languageLabel);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(LanguageButton, languageLabel);
        LanguageButton.Click += async (_, _) => await ExecuteCommandAsync("app.language");
        RegisterCommand("app.language", ChangeLanguageAsync, false, LanguageButton);
        BuildFileMenu();
        BuildWorkspaceCommands();
        ShowMapOverview();
        ShowInspectorHome();
        MapOverlayCancel.Click += async (_, _) => await RunAsync(CancelCurrentTaskAsync);
        PreviewRetry.Click += async (_, _) => await RunAsync(RetryPreviewAsync);
        PreviewCancel.Click += (_, _) => DismissPreviewNotice();
        PreviewCompatibility.Click += async (_, _) => await RunAsync(EnableCompatibilityPreviewAsync);
        CompatibilityRetry.Click += async (_, _) => await RunAsync(RetryPreviewAsync);
        CompatibilitySurface.SizeChanged += (_, _) =>
        {
            CompatibilityImage.Width = CompatibilitySurface.ActualWidth;
            CompatibilityImage.Height = CompatibilitySurface.ActualHeight;
        };
        PreviewHtml.Click += async (_, _) => await RunAsync(OpenExternalPreviewAsync);

        OperationError.RegisterPropertyChangedCallback(InfoBar.MessageProperty, (_, _) => OperationError.Content = null);
        OperationError.Closed += (_, _) => { TryShowSimplifiedRibbonIntro(); TryShowMapInteractionGuide(); };
        OperationError.RegisterPropertyChangedCallback(InfoBar.SeverityProperty, (_, _) =>
        {
            if (OperationError.Severity != InfoBarSeverity.Error) OperationError.Content = null;
        });
        _engine.EventReceived += (name, payload) => Root.DispatcherQueue.TryEnqueue(() => HandleEngineEvent(name, payload));
        _engine.Faulted += message => Root.DispatcherQueue.TryEnqueue(() =>
        {
            _rmpGeneration?.TrySetException(new IOException(message));
            _exportWindow?.HandleEngineFailure(message);
            _engineBusy = false;
            _engineUnavailable = true;
            SetError(TaskError.EngineStopped(message));
        });
        AppWindow.Closing += (sender, args) =>
        {
            if (_allowMainClose) return;
            args.Cancel = true;
            _ = RunAsync(CloseEditorsAndWindowAsync);
        };
        Closed += (_, _) => { _closing = true; CloseBuildingList(); _previewInstance++; _previewWait?.Cancel(); _compatibilityDelay?.Cancel(); _contextWriteDelay?.Cancel(); _contextWriteDelay?.Dispose(); MapView.Close(); _rmpView?.Dispose(); _ = _engine.CloseAsync(); };
        Root.Loaded += async (_, _) =>
        {
            Root.XamlRoot.Changed += (_, _) => UpdateScaleAndLayout();
            UpdateScaleAndLayout();

            await InitializeAsync();
        };
        Root.SizeChanged += (_, _) => UpdateScaleAndLayout();
    }

    private void UpdateScaleAndLayout()
    {
        var compact = Root.ActualWidth < 1080;
        ContextText.Visibility = MapToolbarHeader.ActualWidth < 900 ? Visibility.Collapsed : Visibility.Visible;
        InspectorColumn.Width = new GridLength(_immersive || _context == "metro" || compact || !_inspectorOpen ? 0 : 320);
        Grid.SetColumn(InspectorBorder, compact ? 1 : 2);
        Grid.SetColumnSpan(InspectorBorder, compact ? 2 : 1);
        InspectorBorder.HorizontalAlignment = compact ? HorizontalAlignment.Right : HorizontalAlignment.Stretch;
        InspectorBorder.Width = compact ? Math.Min(360, Math.Max(200, Root.ActualWidth - (_settingsOpen ? 260 : 40))) : double.NaN;
        InspectorBorder.Visibility = _immersive || _context == "metro" || !_inspectorOpen ? Visibility.Collapsed : Visibility.Visible;
        SettingsColumn.Width = new GridLength(!_immersive && _settingsOpen ? _cityArtwork ? 224 : Root.ActualWidth < 850 ? 240 : 290 : 0);
        SettingsBorder.Visibility = !_immersive && _settingsOpen ? Visibility.Visible : Visibility.Collapsed;
        InspectorClose.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        InspectorToggle.Visibility = _context != "metro" ? Visibility.Visible : Visibility.Collapsed;
        UpdateSidebarToggle(SettingsToggle, _settingsOpen, "left", "Settings");
        UpdateSidebarToggle(InspectorToggle, _inspectorOpen, "right", "Inspector");
        LayoutToolbar();
    }

    private static void UpdateSidebarToggle(Button button, bool open, string side, string panel)
    {
        var action = open ? "Hide" : "Show";
        var label = UiLanguage.Text($"Sidebar_{action}{panel}");
        if (ToolTipService.GetToolTip(button) as string == label) return;
        button.Content = RibbonIcons.Named($"layout-sidebar-{side}-{(open ? "collapse" : "expand")}");
        ToolTipService.SetToolTip(button, label);
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, label);
    }

    private void BuildRibbon()
    {
        // 本程序没有快速访问工具栏；默认开启时只会在左上角留下一个孤立的“自定义”箭头。
        Ribbon.IsQuickAccessVisible = false;
        var start = new RibbonTab { Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_D2BB025A2E") };
        var city = Group(start, UiLanguage.Text("MainWindow_xaml_BuildRibbon_ED84A1665F"));
        AddButton(city, UiLanguage.Text("MainWindow_xaml_BuildRibbon_706866330A"), OpenCityAsync, "file.open_city", true, requiresDocument: false);
        _recentMenu = new MenuFlyout();
        var recent = new RibbonDropDownButton { Label = UiLanguage.Text("MainWindow_xaml_BuildRibbon_B6D7CBC94D"), Icon = RibbonIcons.Create("file.recent"),
            Flyout = _recentMenu, Size = RibbonItemSize.Medium };
        city.Items.Add(recent);
        RegisterRibbonColors(recent, recent.Icon, "PRIMARY");
        var output = OutputRibbonGroup.Create(UiLanguage.Text("MainWindow_xaml_BuildRibbon_FB04ADDB4C"));
        start.Groups.Add(output);
        AddButton(output, UiLanguage.Text("MainWindow_xaml_BuildRibbon_76420433F2"), ExportPngAsync, "map.save_png", true, key: VirtualKey.S);
        AddButton(output, UiLanguage.Text("MainWindow_xaml_BuildRibbon_C4A846B549"), ExportHtmlAsync, "create.web_map");
        Ribbon.Tabs.Add(start);

        var layers = new RibbonTab { Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_F2926EB3BB") };
        _layersTab = layers;
        _layerContextGroup = Group(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_BFF8664A71"));
        _returnToMapLayers = new RibbonButton { Label = UiLanguage.Text("MainWindow_xaml_BuildRibbon_458B5F5678"), Icon = RibbonIcons.Create("view.context.close"),
            Size = RibbonItemSize.Medium };
        _returnToMapLayers.Click += async (_, _) => await RunAsync(async () =>
        {
            await LeaveContextAsync();
            Ribbon.SelectedTab = _layersTab;
        });
        _layerContextGroup.Items.Add(_returnToMapLayers);
        RegisterRibbonColors(_returnToMapLayers, _returnToMapLayers.Icon, "PRIMARY");
        _layerContextGroup.IsGroupVisible = false;
        AddLayerGroup(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_4E162D443D"), ("satellite", UiLanguage.Text("Satellite_LayerTitle")), ("terrain", UiLanguage.Text("MainWindow_xaml_BuildRibbon_51695AD45A")), ("water", UiLanguage.Text("MainWindow_xaml_BuildRibbon_207E6D1CF5")));
        AddLayerGroup(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_ED84A1665F"), ("road", UiLanguage.Text("MainWindow_xaml_BuildRibbon_A44C7BA211")), ("building", UiLanguage.Text("MainWindow_xaml_BuildRibbon_5B46860A8E")),
            ("railway", UiLanguage.Text("MainWindow_xaml_BuildRibbon_97492A0E5D")), ("built_area", UiLanguage.Text("MainWindow_xaml_BuildRibbon_433091EABE")), ("poi_area", UiLanguage.Text("MainWindow_xaml_BuildRibbon_AD68267D54")), ("traffic", UiLanguage.Text("MainWindow_xaml_BuildRibbon_18B9E17D3D")));
        AddLayerGroup(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_57F78BA1F9"), ("metro", UiLanguage.Text("MainWindow_xaml_BuildRibbon_1AB216CC0D")));
        AddLayerGroup(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_4F0C1E4BF0"), ("label", UiLanguage.Text("MainWindow_xaml_BuildRibbon_4F0C1E4BF0")));
        _facilitySelectionGroup = Group(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_4593C85D33"));
        _facilitySelectionGroup.IsGroupVisible = false;
        AddButton(_facilitySelectionGroup, UiLanguage.Text("MainWindow_xaml_BuildRibbon_803AA8753F"), ShowFacilitiesAsync, "layer.poi.facilities");
        AddLayerGroup(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_62AEEB8B65"), ("district", UiLanguage.Text("MainWindow_xaml_BuildRibbon_0983E2CCDD")), ("zone", UiLanguage.Text("MainWindow_xaml_BuildRibbon_8592878D7E")),
            ("land_use", UiLanguage.Text("MainWindow_xaml_BuildRibbon_D84EBADC9F")));
        AddLayerGroup(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_D2909F1647"), ("procedural_objects", UiLanguage.Text("MainWindow_xaml_BuildRibbon_87FC97C6B8")), ("watermark", UiLanguage.Text("MainWindow_xaml_BuildRibbon_AA9F217C81")));
        Ribbon.Tabs.Add(layers);

        var style = new RibbonTab { Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_C8FFBDF157") };
        _styleTab = style;
        _themeGalleries = new ThemeGalleryBinding(_themeChoices,
            theme => RunAsync(() => SetMapThemeAsync(theme)), () => _artworkEditor);
        Group(start, UiLanguage.Text("MainWindow_xaml_BuildRibbon_8A0BB16307")).Items.Add(CreateThemeGallery());
        AddButton(Group(start, UiLanguage.Text("MainWindow_xaml_BuildRibbon_CA556628F1")), UiLanguage.Text("MainWindow_xaml_BuildRibbon_74F45217E6"), ChooseThemeEditorAsync, "style.edit_theme", true);
        var quickCreate = Group(start, UiLanguage.Text("MainWindow_xaml_BuildRibbon_A0709CF187"));
        AddButton(quickCreate, UiLanguage.Text("MainWindow_xaml_BuildRibbon_5A85707DE5"), EnterTransitAsync, "view.transit", true);
        AddButton(quickCreate, UiLanguage.Text("MainWindow_xaml_BuildRibbon_FA5D23884E"), EnterMetroAsync, "view.metro", true);
        AddButton(quickCreate, UiLanguage.Text("MainWindow_xaml_BuildRibbon_485DDBB48A"), PickRandomRegionAsync, "create.random_spot", true);
        start.Groups.Remove(output);
        start.Groups.Add(output);
        Group(style, UiLanguage.Text("MainWindow_xaml_BuildRibbon_8A0BB16307")).Items.Add(CreateThemeGallery());
        var styleEditing = Group(style, UiLanguage.Text("MainWindow_xaml_BuildRibbon_AB98197175"));
        AddButton(styleEditing, UiLanguage.Text("Artwork_Enter"), CreateCityArtworkAsync, "map.edit_city", true);
        AddButton(styleEditing, UiLanguage.Text("MainWindow_xaml_BuildRibbon_74F45217E6"), ChooseThemeEditorAsync, "style.edit_theme", true);
        _poiGallery = new RibbonGallery { ItemWidth = 88, ItemHeight = 82, MaxColumns = 1,
            Width = 96, HorizontalAlignment = HorizontalAlignment.Left,
            Label = UiLanguage.Text("MainWindow_xaml_BuildRibbon_5DFB5B81F2"),
            Size = RibbonItemSize.Large,
            ItemTemplate = (DataTemplate)Root.Resources["PoiStylePreviewTemplate"], ItemsSource = _poiChoices };
        ConfigureRibbonGalleryLayout(_poiGallery);
        ToolTipService.SetToolTip(_poiGallery, UiLanguage.Text("MainWindow_xaml_BuildRibbon_5DFB5B81F2"));
        _poiGallery.ItemClick += async (_, args) =>
        {
            if (_syncingPoiGallery || args.Item is not PoiStyleChoice choice) return;
            await UpdateFieldAsync("poi.set", choice.Id);
        };
        var poiSettings = Group(style, UiLanguage.Text("MainWindow_xaml_BuildRibbon_0BF8C392DA"));
        poiSettings.Items.Add(_poiGallery);
        AddButton(poiSettings, UiLanguage.Text("MainWindow_xaml_BuildRibbon_8CA4689CF5"), EditPoiMappingAsync, "tools.poi_mapping");
        AddButton(poiSettings, UiLanguage.Text("MainWindow_xaml_BuildRibbon_4F0C1E4BF0"), ShowMapPoiSettingsAsync, "settings.map_poi");
        Ribbon.Tabs.Add(style);

        var create = new RibbonTab { Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_CDE2CD071D") };
        AddButton(Group(create, UiLanguage.Text("MainWindow_xaml_BuildRibbon_1AB216CC0D")), UiLanguage.Text("MainWindow_xaml_BuildRibbon_5A85707DE5"), EnterTransitAsync, "view.transit", true);
        AddButton(Group(create, UiLanguage.Text("MainWindow_xaml_BuildRibbon_FA5D23884E")), UiLanguage.Text("MainWindow_xaml_BuildRibbon_FA5D23884E"), EnterMetroAsync, "view.metro", true);
        var createGroup = Group(create, UiLanguage.Text("MainWindow_xaml_BuildRibbon_6BEB3C5ED7"));
        AddButton(createGroup, UiLanguage.Text("MainWindow_xaml_BuildRibbon_485DDBB48A"), PickRandomRegionAsync, "create.random_spot", true);
        AddButton(createGroup, UiLanguage.Text("MainWindow_xaml_BuildRibbon_8BB46E9B58"), () => { ShowInterchanges(); return Task.CompletedTask; }, "create.interchange_collection");

        create.Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_7D3F1EFCF2");
        var tools = create;
        var external = Group(tools, UiLanguage.Text("MainWindow_xaml_BuildRibbon_F6848EA38A"));
        AddButton(external, RmpText("GenerateRmg"), () => ShowExportAsync("RMG", "metro"), "view.metro.export_rmg");
        var utility = Group(tools, UiLanguage.Text("MainWindow_xaml_BuildRibbon_21138F1B06"));
        AddButton(utility, UiLanguage.Text("MainWindow_xaml_BuildRibbon_8CA4689CF5"), EditPoiMappingAsync, "tools.poi_mapping");
        AddButton(utility, UiLanguage.Text("MainWindow_xaml_BuildRibbon_9167EE8B67"), ShowFullDiagnosticsAsync, "tools.diagnostics", requiresDocument: false);
        AddButton(utility, UiLanguage.Text("MainWindow_xaml_BuildRibbon_B2FD2E2A88"), async () => { await SafeRequestAsync("clearCache"); ShowNotice(UiLanguage.Text("MainWindow_xaml_BuildRibbon_0F0D83A17D")); }, "tools.clear_cache", requiresDocument: false);
        AddButton(utility, UiLanguage.Text("MainWindow_xaml_BuildRibbon_52D25A9E30"), ShowAboutAsync, "app.about", requiresDocument: false);
        AddButton(utility, UiLanguage.Text("MainWindow_xaml_BuildRibbon_498E1D59B4"), CloseEditorsAndWindowAsync, "app.quit", requiresDocument: false, key: VirtualKey.Q);
        AddButton(utility, UiLanguage.Text("MainWindow_xaml_BuildRibbon_A57CFCB842"), ShowHelpAsync, "tools.help", requiresDocument: false);
        AddButton(utility, UiLanguage.Text("MainWindow_xaml_BuildRibbon_3D13868593"), ChangeLanguageAsync, "app.language", requiresDocument: false);
        Ribbon.Tabs.Add(tools);
        _transitTab = new RibbonTab { Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_5A85707DE5") };
        var transit = Group(_transitTab, UiLanguage.Text("MainWindow_xaml_BuildRibbon_1C5C067138"));
        AddButton(transit, UiLanguage.Text("MainWindow_xaml_BuildRibbon_81B0834AA6"), ShowTransitSettingsAsync, "view.transit.settings");
        AddButton(transit, UiLanguage.Text("MainWindow_xaml_BuildRibbon_5060977906"), ShowTransitLinesAsync, "view.transit.lines");
        _transitOutputGroup = OutputRibbonGroup.Create(UiLanguage.Text("ContextRibbon_Output"));
        _transitTab.Groups.Add(_transitOutputGroup);
        AddButton(_transitOutputGroup, UiLanguage.Text("ContextRibbon_Export"), () => ChooseExportAsync("PNG", "transit"), "view.transit.export", true);
        _transitCloseGroup = Group(_transitTab, UiLanguage.Text("ContextRibbon_Close"));
        AddButton(_transitCloseGroup, UiLanguage.Text("MainWindow_xaml_BuildRibbon_71B67FAAD9"), LeaveContextAsync, "view.transit.close", true);
        _metroTab = new RibbonTab { Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_FA5D23884E") };
        BuildRmpRibbon();
        BuildArtworkRibbon();
        Ribbon.SelectedTab = start;
        ConfigureRibbonLayout();
        SetDocumentEnabled(false);
    }

    private static RibbonGroup Group(RibbonTab tab, string name)
    {
        var group = new RibbonGroup { Header = name };
        tab.Groups.Add(group);
        return group;
    }

    private void UpdateThemeGalleries()
    {
        if (_themeGalleries is null) return;
        var themes = _artworkEditor?.ThemeGalleryChoices ?? _snapshot.GetProperty("themes").EnumerateArray()
            .Select(theme => (Id: theme.GetProperty("id").GetString()!, Name: theme.GetProperty("name").GetString()!,
                Preview: theme.GetProperty("preview").GetString()));
        _themeGalleries.Synchronize(_artworkEditor?.SelectedThemeId ?? _snapshot.GetProperty("theme").GetString(), () =>
        {
            var index = 0;
            foreach (var (id, name, preview) in themes)
            {
                if (index >= _themeChoices.Count) _themeChoices.Add(new ThemeGalleryChoice(id, name, preview));
                else if (_themeChoices[index].Id != id || _themeChoices[index].Name != name || _themeChoices[index].PreviewId != preview)
                    _themeChoices[index] = new ThemeGalleryChoice(id, name, preview);
                index++;
            }
            while (_themeChoices.Count > index) _themeChoices.RemoveAt(_themeChoices.Count - 1);
        });
        UpdateThemeGalleryCommands();
    }

    private RibbonGallery CreateThemeGallery()
    {
        var gallery = new RibbonGallery { ItemWidth = 88, ItemHeight = 82, MaxColumns = 5,
            Width = 472, HorizontalAlignment = HorizontalAlignment.Left,
            Label = UiLanguage.Text("MainWindow_xaml_BuildRibbon_8A0BB16307"),
            Size = RibbonItemSize.Large,
            ItemTemplate = (DataTemplate)Root.Resources["ThemePreviewTemplate"] };
        ConfigureRibbonGalleryLayout(gallery);
        ToolTipService.SetToolTip(gallery, UiLanguage.Text("MainWindow_xaml_CreateThemeGallery_8C82CE6C56"));
        _themeGalleries!.Bind(gallery);
        AddThemeGalleryCommands(gallery);
        _documentControls.Add(gallery);
        return gallery;
    }

    private void AddButton(RibbonGroup group, string title, Func<Task> run, string id,
        bool large = false, bool requiresDocument = true, VirtualKey? key = null)
    {
        var button = new RibbonButton { Label = title, Icon = RibbonIcons.Create(id, large),
            Size = large ? RibbonItemSize.Large : RibbonItemSize.Medium };
        if (id is "map.save_png" or "map.edit_city")
        {
            if (id == "map.save_png") button.Name = "PrimaryExportButton";
            button.MinWidth = 112;
            button.Background = (Brush)Root.Resources["PrimaryExportBackground"];
            button.SimplifiedVisibility = RibbonSimplifiedVisibility.Pinned;
        }
        RegisterRibbonColors(button, button.Icon, CommandDomain(id), large);
        RegisterCommand(id, run, requiresDocument, button, key);
        button.Click += async (_, _) => await ExecuteCommandAsync(id);
        group.Items.Add(button);
        if (requiresDocument) _documentControls.Add(button);
    }

    private void AddLayerGroup(RibbonTab tab, string title, params (string key, string label)[] layers)
    {
        var group = Group(tab, title);
        foreach (var (key, label) in layers)
        {
            var large = key is not ("built_area" or "poi_area" or "traffic" or "land_use" or "procedural_objects" or "watermark");
            var button = LayerRibbonButton.Create(label, IconForLayer(key, large), large,
                () => ToggleLayerAsync(key), error => SetError(error));
            var flyout = new MenuFlyout();
            var details = new MenuFlyoutItem { Text = UiLanguage.Text("MainWindow_xaml_AddLayerGroup_4358ED3763"), Icon = new SymbolIcon(Symbol.Setting) };
            details.Click += async (_, _) => await RunAsync(async () =>
            {
                if (key is "district" or "zone") await ShowRegionsAsync(key == "district" ? "districts" : "parks");
                else if (key == "label") await ShowMapPoiSettingsAsync();
                else if (key == "metro" && !_cityArtwork) { _settingsOpen = true; UpdateScaleAndLayout(); await ShowTransitSettingsAsync(); }
                else ShowLayerStylePage(key);
            });
            flyout.Items.Add(details);
            button.Flyout = flyout;
            group.Items.Add(button);
            _layerButtons[key] = button;
            RegisterRibbonColors(button, button.Icon, "PRIMARY", large, () => LayerVisible(key));
            ToolTipService.SetToolTip(button, label + UiLanguage.Text("MainWindow_xaml_AddLayerGroup_9AE3E30C84"));
            _documentControls.Add(button);
            if (key == "label") AddRibbonLabelChildren(group);
            if (key == "metro")
            {
                AddLineLabelToggle(group, "map");
                var lines = new RibbonButton { Label = UiLanguage.Text("MainWindow_xaml_AddLayerGroup_5060977906"), Icon = RibbonIcons.Create("view.transit.lines"),
                    Size = RibbonItemSize.Medium };
                lines.Click += async (_, _) => await RunAsync(ShowTransitLinesAsync);
                group.Items.Add(lines);
                _mapLayerSettingsButtons.Add(lines);
                RegisterRibbonColors(lines, lines.Icon, "PRIMARY");
            }
        }
    }


    private static IconElement IconForLayer(string key, bool large = false) => RibbonIcons.Create("layer." + (key == "satellite" ? "terrain" : key), large);

    private async Task InitializeAsync()
    {
        if (_initialized || _closing) return;
        _initialized = true;
        BeginMapTask(MapTaskPhase.Loading, UiLanguage.Text("MainWindow_xaml_InitializeAsync_656C56EE98"), canCancel: false, waitForScene: false);
        try
        {
            if (_initialMetadata is null) await _engine.StartAsync(_arguments);
            await _engine.RequestAsync("hello", new { protocolVersion = 1 });
            await RefreshSnapshotAsync();
            var metadata = _initialMetadata ?? await _engine.RequestAsync("getUiMetadata");
            if (metadata.GetProperty("language").GetString() != UiLanguage.Language)
            {
                await ReloadLanguageAsync(metadata, _showLanguageNotice);
                return;
            }
            UiLanguage.Configure(metadata); ApplyCommandMetadata(metadata); ConfigureAppearance(metadata);
            SendViewer(new { type = "setLanguage", language = UiLanguage.Language });
            await BuildTransitRibbonAsync();
            _mapTask.Reset(false);
            ApplyMapTaskState();
            if (UserData.ReadAutoPreview(_ribbonLayoutDirectory)) _ = InitializePreviewAsync();
            await ShowLanguageNoticeAsync();
            await ShowThemeUpdateAsync();
            var file = _arguments.FirstOrDefault(arg => !arg.Equals("gui", StringComparison.OrdinalIgnoreCase));
            if (!string.IsNullOrWhiteSpace(file) && File.Exists(file))
                await OpenPathAsync(file);
            PrepareSimplifiedRibbonIntro(typeof(MainWindow).Assembly.GetName().Version!);
        }
        catch (Exception error)
        {
            StartupTrace.Write("Workspace initialization failed: " + error);
            SetError(error);
        }
    }

    private async Task RefreshSnapshotAsync()
    {
        _snapshot = await _engine.RequestAsync("getSnapshot");
        ApplySharedRotation();
        SetDocumentEnabled(_snapshot.GetProperty("hasDocument").GetBoolean() &&
            !_engineBusy);
        if (_recentMenu is not null)
        {
            _recentMenu.Items.Clear();
            foreach (var item in _snapshot.GetProperty("recentFiles").EnumerateArray())
            {
                var path = item.GetString();
                if (string.IsNullOrWhiteSpace(path)) continue;
                var entry = new MenuFlyoutItem { Text = Path.GetFileName(path) };
                entry.Click += async (_, _) => await RunAsync(() => OpenPathAsync(path));
                _recentMenu.Items.Add(entry);
            }
            if (_recentMenu.Items.Count == 0)
                _recentMenu.Items.Add(new MenuFlyoutItem { Text = UiLanguage.Text("MainWindow_xaml_RefreshSnapshotAsync_C3FE933AA4"), IsEnabled = false });
        }
        RefreshFileRecent();
        foreach (var (key, button) in _layerButtons)
        {
            var enabled = _snapshot.GetProperty("layers").TryGetProperty(key, out var raw) && raw.GetBoolean();
            button.IsChecked = enabled;
            var label = button.Label ?? key;
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(button, label + (enabled ? UiLanguage.Text("MainWindow_xaml_RefreshSnapshotAsync_645432A5BF") : UiLanguage.Text("MainWindow_xaml_RefreshSnapshotAsync_96E5815C15")));
        }
        UpdateThemeGalleries();
        var themeError = _snapshot.TryGetProperty("themeLoadError", out var errorValue) ? errorValue.GetString() ?? "" : "";
        if (themeError != _themeLoadErrorShown)
        {
            _themeLoadErrorShown = themeError;
            if (!string.IsNullOrEmpty(themeError))
            {
                OperationError.Severity = InfoBarSeverity.Warning;
                OperationError.Message = UiLanguage.Text("ThemeEditor_CurrentUnreadable");
                OperationError.IsOpen = true;
            }
            else if (OperationError.Message == UiLanguage.Text("ThemeEditor_CurrentUnreadable")) OperationError.IsOpen = false;
        }
        if (_poiGallery is not null)
        {
            _syncingPoiGallery = true;
            try { _poiGallery.SelectedItem = _poiChoices.FirstOrDefault(item => item.Id == _snapshot.GetProperty("fields").GetProperty("poi.set").GetString()); }
            finally { _syncingPoiGallery = false; }
        }
        RefreshMapOverview();
        ApplyPendingSettingsSnapshot();
        await RefreshBuildingCatalogAsync();
        if (_exportWindow is { } export) await export.RefreshSharedSnapshotAsync();
    }

    private void SetDocumentEnabled(bool enabled)
    {
        enabled = enabled && !_engineUnavailable;
        enabled &= !_loadingDocument;
        foreach (var control in _documentControls) control.IsEnabled = enabled;
        foreach (var (key, button) in _layerButtons) button.IsEnabled = enabled && _context == "map" &&
            (key != "satellite" || (_snapshot.TryGetProperty("satelliteAvailable", out var available) && available.GetBoolean()));
        foreach (var button in _mapLayerSettingsButtons) button.IsEnabled = enabled && _context == "map";
        if (_poiGallery is not null) _poiGallery.IsEnabled = enabled && _context == "map";
        _documentEnabled = enabled;
        BuildingListButton.IsEnabled = enabled && _context == "map";
        _buildingList?.SetAvailable(enabled && _context == "map");
        if (_context != "map") CloseBuildingList();
        if (_layerContextGroup is not null)
        {
            _layerContextGroup.IsGroupVisible = _context != "map";
            _layerContextGroup.Header = _context == "metro" ? UiLanguage.Text("MainWindow_xaml_SetDocumentEnabled_0E097C11F5") : UiLanguage.Text("MainWindow_xaml_SetDocumentEnabled_336CD8FA47");
            _returnToMapLayers!.IsEnabled = enabled;
        }
        RefreshLayerBindings();
        _objectInspector?.SetAvailable(enabled && _context is "map" or "transit");
        RefreshContextRibbon();
        UpdateCommandStates();
        SendPreviewUiState();
        ApplyPreviewStatus();
    }

    private async Task OpenCityAsync()
    {
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        picker.FileTypeFilter.Add(".mmap");
        picker.FileTypeFilter.Add(".cslmap");
        picker.FileTypeFilter.Add(".cmm");
        picker.FileTypeFilter.Add(".gz");
        picker.FileTypeFilter.Add(".json");
        var file = await picker.PickSingleFileAsync();
        if (file is not null) await OpenPathAsync(file.Path);
    }

    private async Task OpenPathAsync(string path)
    {
        if (_cityArtwork && !await TryLeaveArtworkEditorAsync()) return;
        if (_exportWindow is { } export && !await export.RequestCloseAsync()) return;
        if (_themeEditor is { } theme && !await theme.RequestCloseAsync()) return;
        if (_poiEditor is { } poi && !await poi.RequestCloseAsync()) return;
        if (!await ReleaseRmpAsync()) return;
        CloseBuildingList();
        await FlushContextWritesAsync();
        _documentGeneration++;
        ResetCompatibilityImage();
        _previewNoticeDismissed = false;
        _loadingDocument = true;
        _sceneId = ""; _revision = 0;
        _mapTask.Reset(false);
        _context = "map"; _generationSettingsChanged = false;
        if (_transitTab is not null) Ribbon.Tabs.Remove(_transitTab);
        if (_metroTab is not null) Ribbon.Tabs.Remove(_metroTab);
        RmpWorkspace.Visibility = Visibility.Collapsed; Ribbon.SelectedTab = Ribbon.Tabs[0];
        ShowMapOverview();
        ShowInspectorHome();
        _selectedBounds = null;
        _selectedRegionSource = null;
        BeginMapTask(MapTaskPhase.Loading, UiLanguage.Text("MainWindow_xaml_OpenPathAsync_3AD0FA9F8D"));
        var result = await _engine.RequestAsync("openDocument", new { path });
        if (!result.GetProperty("started").GetBoolean())
        {
            _awaitingScene = false;
            SetError(UiLanguage.Text("MainWindow_xaml_OpenPathAsync_949301A19E"));
        }
    }

    private Task ToggleLayerAsync(string key)
    {
        var generation = _documentGeneration;
        return QueueLayerActionAsync(() => SetLayerVisibleAsync(key, !LayerVisible(key)),
            () => !_closing && _documentEnabled && generation == _documentGeneration);
    }

    private async Task EnterTransitAsync()
    {
        if (RmpBlocked) return;
        if (_cityArtwork && !await TryLeaveArtworkEditorAsync()) return;
        _awaitingScene = _waitingSceneReady = false;
        _mapTask.Reset(!string.IsNullOrEmpty(_sceneId));
        await FlushContextWritesAsync();
        _context = "transit";
        if (_metroTab is not null) Ribbon.Tabs.Remove(_metroTab);
        RmpWorkspace.Visibility = Visibility.Collapsed;
        if (_transitTab is not null && !Ribbon.Tabs.Contains(_transitTab)) Ribbon.Tabs.Add(_transitTab);
        Ribbon.SelectedTab = Ribbon.Tabs[^1];
        await RefreshSnapshotAsync();
        await ApplyContextAsync();
        await RefreshLivePreviewAsync();
        await ShowTransitSettingsAsync();
        ApplyMapTaskState();
    }

    private async Task EnterMetroAsync()
    {
        if (RmpBlocked) return;
        if (_cityArtwork && !await TryLeaveArtworkEditorAsync()) return;
        await FlushContextWritesAsync();
        _context = "metro";
        _selectedBounds = null;
        _selectedRegionSource = null;
        SendViewer(new { type = "clearSelection" });
        if (_transitTab is not null) Ribbon.Tabs.Remove(_transitTab);
        if (_metroTab is not null && !Ribbon.Tabs.Contains(_metroTab)) Ribbon.Tabs.Add(_metroTab);
        Ribbon.SelectedTab = Ribbon.Tabs[^1];
        RmpWorkspace.Visibility = Visibility.Visible;
        _mapTask.Reset(_rmpView?.IsReady == true);
        await ShowMetroSettingsAsync();
        await ApplyContextAsync(refresh: false);
        UpdateScaleAndLayout();
        RefreshRmpRibbon();
        if (_rmpView?.IsReady != true) await GenerateRmpAsync();
        else if (_generationSettingsChanged) ShowNotice(RmpText("SettingsChanged"));
    }

    private async Task LeaveContextAsync()
    {
        if (RmpBlocked) return;
        if (_cityArtwork && !await TryLeaveArtworkEditorAsync()) return;
        await FlushContextWritesAsync();
        _context = "map";
        if (_transitTab is not null) Ribbon.Tabs.Remove(_transitTab);
        if (_metroTab is not null) Ribbon.Tabs.Remove(_metroTab);
        RmpWorkspace.Visibility = Visibility.Collapsed;
        _mapTask.Reset(!string.IsNullOrEmpty(_sceneId)); ApplyMapTaskState();
        Ribbon.SelectedTab = Ribbon.Tabs[0];
        await RefreshSnapshotAsync();
        await ApplyContextAsync();
        await RefreshLivePreviewAsync();
        ShowMapOverview();
        ApplyMapTaskState();
    }

    private async Task ApplyContextAsync(bool refresh = true)
    {
        var state = refresh ? await _engine.RequestAsync("getSnapshot") : _snapshot;
        _snapshot = state;
        ApplySharedRotation();
        ContextText.Text = _context switch { "transit" => UiLanguage.Text("MainWindow_xaml_ApplyContextAsync_5A85707DE5"), "metro" => UiLanguage.Text("MainWindow_xaml_ApplyContextAsync_FA5D23884E") + " · RMP", _ => UiLanguage.Text("MainWindow_xaml_ApplyContextAsync_2E9A956FC6") };
        SetDocumentEnabled(state.GetProperty("hasDocument").GetBoolean() && !_engineBusy && !_engineUnavailable);
        UpdateScaleAndLayout();
        ViewerToolbar.Visibility = _context == "metro" ? Visibility.Collapsed : Visibility.Visible;
        MapView.Visibility = _context == "metro" || _compatibilityPreview ? Visibility.Collapsed : Visibility.Visible;
        RmpWorkspace.Visibility = _context == "metro" ? Visibility.Visible : Visibility.Collapsed;
        RefreshRmpRibbon();
        RefreshWorkspaceFileMenu();
        if (!_cityArtwork && _context != "metro")
            SendViewer(new { type = "setContextMode", mode = _context,
                options = ContextOptions(false), sceneId = _sceneId, revision = _revision });
        SendViewer(LayerVisibilityCommand.Create("poi",
            (_context != "map" || state.GetProperty("layers").GetProperty("label").GetBoolean()) &&
            state.GetProperty("poiVisibility").GetProperty("icons").EnumerateObject().Any(item => item.Value.GetBoolean()),
            _sceneId, _revision));
        if (_inspectorSelection is { } selected && _inspectorContext != _context)
        {
            if (_context == "metro") ShowInspectorHome();
            else await ShowObjectInspectorAsync(selected);
        }
        if (_compatibilityPreview && _compatibilityContext != _context) QueueCompatibilityPreview();
        ApplyPreviewStatus();
    }

    private Task ShowTransitSettingsAsync() => ShowContextParametersAsync(false);

    private Task ShowMetroSettingsAsync() => ShowContextParametersAsync(true);

    private Task EditThemeAsync() => EditThemeAsync(null);

    private Task EditThemeAsync(string? section) => OpenThemeEditorAsync(section, false);

    private async Task OpenThemeEditorAsync(string? section, bool create)
    {
        if (_artworkEditor is { } embedded)
        {
            if (create) await embedded.NewAsync();
            if (section is not null) embedded.FocusStyle(section);
            _artworkPages!.SelectedItem = _artworkStylesTab;
            _settingsOpen = _inspectorOpen = true;
            Ribbon.SelectedTab = _artworkTab; ShowArtworkProperties(); UpdateScaleAndLayout(); return;
        }
        if (_themeEditor is { } existing) { if (section is not null) existing.FocusSection(section); existing.Activate(); if (create) await existing.NewAsync(); return; }
        if (!_openingEditors.Add("theme")) return;
        try
        {
        var draft = await _engine.RequestAsync("getThemeDraft");
        if (create)
        {
            var basis = await ThemeEditorDialog.ChooseBaseAsync(Root.XamlRoot, draft.GetProperty("themes"), draft.GetProperty("baseTheme").GetString());
            if (basis is null) return;
            draft = await _engine.RequestAsync("newThemeDraft", new { baseTheme = basis });
        }
        if (_closing || _allowMainClose) return;
        var editor = new ThemeEditorDialog(this, _engine, Root.XamlRoot, draft);
        if (section is not null) editor.FocusSection(section);
        _themeEditor = editor;
        editor.StateChanged += (_, _) => UpdateThemeGalleryCommands();
        try
        {
            if (await editor.ShowAsync())
            {
                await RefreshLivePreviewAsync();
                await RefreshSnapshotAsync();
            }
        }
        finally { if (_themeEditor == editor) _themeEditor = null; }
        }
        finally { _openingEditors.Remove("theme"); }
    }

    private async Task EditPoiMappingAsync()
    {
        if (_poiEditor is { } existing) { existing.Activate(); return; }
        if (!_openingEditors.Add("poi")) return;
        try
        {
        var data = await _engine.RequestAsync("getPoiEditor");
        if (_closing || _allowMainClose) return;
        var editor = new PoiMappingDialog(_engine, Root.XamlRoot, data);
        editor.Changed = ApplyEditorChangesAsync;
        _poiEditor = editor;
        try
        {
            if (await editor.ShowAsync())
            {
                await RefreshLivePreviewAsync();
                await RefreshSnapshotAsync();
            }
        }
        finally { if (_poiEditor == editor) _poiEditor = null; }
        }
        finally { _openingEditors.Remove("poi"); }
    }

    private bool _engineBusy;
    private bool _engineUnavailable;
    private Task CancelCurrentTaskAsync() => CancelCurrentTaskCoreAsync(true);

    private async Task CancelCurrentTaskCoreAsync(bool showStatus)
    {
        if (_cityArtwork && !_cityArtworkExporting) { await TryLeaveArtworkEditorAsync(); return; }
        if (_generatingRmp) _cancellingRmp = true;
        var result = await _engine.RequestAsync("cancel");
        if (result.TryGetProperty("cancelRequested", out var requested) && requested.GetBoolean())
        {
            if (showStatus) ShowNotice(UiLanguage.Text("MainWindow_xaml_CancelCurrentTaskCoreAsync_E8E08B0F61"));
            return;
        }
        if (result.GetProperty("cancelled").GetBoolean())
        {
            _previewWait?.Cancel();
            _fullSceneCompiled = _previewSlow = false;
            _rmpGeneration?.TrySetCanceled();
            _awaitingScene = _waitingSceneReady = false;
            _discardSceneResults = true;
            if (showStatus) _mapTask.Cancel(_mapTask.Generation);
            ApplyMapTaskState();
            _engineBusy = false;
            SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object &&
                _snapshot.GetProperty("hasDocument").GetBoolean());
        }
        if (showStatus && !result.GetProperty("cancelled").GetBoolean())
            ShowNotice(result.GetProperty("message").GetString() ?? UiLanguage.Text("MainWindow_xaml_CancelCurrentTaskCoreAsync_BD768D3131"));
    }

    private async Task UpdateFieldAsync(string key, object value)
    {
        var artworkStarted = _cityArtwork ? System.Diagnostics.Stopwatch.GetTimestamp() : (long?)null;
        _pendingFieldWrites++;
        try
        {
            await RunAsync(async () =>
            {
                await _engine.RequestAsync("setField", new { key, value });
                await RefreshLivePreviewAsync(artworkStarted: artworkStarted); await RefreshSnapshotAsync();
            });
        }
        finally { _pendingFieldWrites--; ApplyPendingSettingsSnapshot(); }
    }

    private async Task SetMapThemeAsync(string theme)
    {
        if (_artworkEditor is { } embedded) { await embedded.SelectThemeByIdAsync(theme); return; }
        var previousTheme = _snapshot.GetProperty("theme").GetString();
        if (_themeEditor is { } editor && !await editor.RequestCloseAsync())
        {
            _updatingOverview = true;
            try
            {
                if (_overviewTheme is not null)
                    _overviewTheme.SelectedItem = _overviewTheme.Items.OfType<ComboBoxItem>().FirstOrDefault(item => item.Tag is string id && id == _snapshot.GetProperty("theme").GetString());
            }
            finally { _updatingOverview = false; }
            return;
        }
        await _engine.RequestAsync("setTheme", new { theme });
        await RefreshLivePreviewAsync();
        await RefreshSnapshotAsync();
        if (theme == "satellite" && previousTheme != "satellite" &&
            _snapshot.GetProperty("theme").GetString() == "satellite")
            await ShowSatelliteGuidanceAsync();
        else if (theme != previousTheme)
            await ShowThemeUpdateAsync();
    }

    private async Task RefreshLivePreviewAsync(bool backgroundRoadUpdate = false, long? artworkStarted = null)
    {
        if (_cityArtwork) { await RefreshCityArtworkCoreAsync(artworkStarted); return; }
        if (_compatibilityPreview) { QueueCompatibilityPreview(); return; }
        if (!_viewerReady) return;
        _discardSceneResults = false;
        var previousScene = _sceneId;
        var previousRevision = _revision;
        var background = backgroundRoadUpdate || !string.IsNullOrEmpty(previousScene);
        var result = await _engine.RequestAsync("refreshPreview", new { map = _context == "map" });
        if (result.TryGetProperty("started", out var started) && started.GetBoolean() && !_mapTask.Active &&
            _sceneId == previousScene && _revision == previousRevision)
        {
            BeginMapTask(MapTaskPhase.Generating,
                backgroundRoadUpdate ? UiLanguage.Text("MainWindow_xaml_RefreshLivePreviewAsync_8CDBE2C406") : _context == "transit" ? UiLanguage.Text("MainWindow_xaml_RefreshLivePreviewAsync_058E698B71") : UiLanguage.Text("MainWindow_xaml_RefreshLivePreviewAsync_A8A3F1C48A"),
                canCancel: !background, background: background);
        }
        if (result.TryGetProperty("theme", out var theme))
            SendViewer(new { type = "applyTheme", theme, sceneId = _sceneId, revision = _revision });
    }

    private async Task ExportSelectionAsync()
    {
        if (_context == "metro")
        {
            ShowNotice(UiLanguage.Text("MainWindow_xaml_ExportSelectionAsync_CF3DE91DF4"));
            return;
        }
        if (_selectedBounds is null)
        {
            ShowNotice(UiLanguage.Text("MainWindow_xaml_ExportSelectionAsync_A48B887C5C"));
            return;
        }
        var source = _selectedRegionSource == "random_spot" ? "random_spot" : "selection";
        var bounds = JsonSerializer.Deserialize<double[]>(_selectedBounds.Value.GetRawText());
        await ShowExportAsync(null, source, new { bounds, source });
    }

    private Task ExportPngAsync() => _context == "metro" ? RunRmpCommandAsync("save") : ChooseExportAsync("PNG");
    private Task ExportHtmlAsync() => ShowExportAsync("HTML");


    private async Task ShowAboutAsync()
    {
        await new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            Title = "CSLModernMap",
            Content = UiLanguage.Text("MainWindow_xaml_ShowAboutAsync_F5BEA9F4E5"),
            CloseButtonText = UiLanguage.Text("MainWindow_xaml_ShowAboutAsync_3FD47EDCE4"),
        }.ShowAsync();
    }

    private void HandleEngineEvent(string name, JsonElement payload)
    {
        if (_closing) return;
        if (name == "artworkProgress") {
            if (_cityArtwork && _cityArtworkOperation is { } current &&
                payload.GetProperty("documentEpoch").GetInt32() == current.Epoch &&
                payload.GetProperty("operationId").ValueKind == JsonValueKind.Number &&
                payload.GetProperty("operationId").GetInt64() == current.Id) {
                double? percent = payload.TryGetProperty("percent", out var rawPercent) && rawPercent.ValueKind == JsonValueKind.Number && rawPercent.TryGetDouble(out var value) ? value : null;
                UpdateCityArtworkProgress(payload.GetProperty("stage").GetString()!, percent);
            }
            return;
        }
        if (name == "artworkRequested") {
            if (_cityArtwork && _cityArtworkOperation is { } operation &&
                payload.GetProperty("documentEpoch").GetInt32() == operation.Epoch &&
                payload.GetProperty("operationId").GetInt64() == operation.Id) {
                _cityArtworkRevision = payload.GetProperty("artworkRevision").GetInt32();
                _cityArtworkOperation = operation with { Revision = _cityArtworkRevision,
                    SceneId = payload.GetProperty("sceneId").GetString(), SceneRevision = payload.GetProperty("revision").GetInt32() };
                _cityArtworkUpdating = true;
                UpdateCityArtworkProgress("layout");
                SendViewer(new { type = "beginArtwork", sceneId = payload.GetProperty("sceneId").GetString(),
                revision = payload.GetProperty("revision").GetInt32(), documentEpoch = payload.GetProperty("documentEpoch").GetInt32(),
                artworkRevision = payload.GetProperty("artworkRevision").GetInt32() });
            }
            return;
        }
        if (name == "artworkReady") {
            if (MatchesCityArtwork(payload.GetProperty("artwork")) &&
                payload.GetProperty("operationId").GetInt64() == _cityArtworkOperation!.Id) {
                _pendingCityArtwork = payload.Clone(); UpdateCityArtworkProgress("delivering"); TryPresentCityArtwork(); }
            return;
        }
        if (name == "artworkFailed") {
            if (!MatchesCityArtwork(payload) && !(_cityArtwork && _cityArtworkOperation is { Revision: 0 } pending &&
                payload.GetProperty("documentEpoch").GetInt32() == pending.Epoch &&
                payload.TryGetProperty("operationId", out var failedId) && failedId.ValueKind == JsonValueKind.Number &&
                failedId.GetInt64() == pending.Id)) return;
            _cityArtworkUpdating = false; ApplyMapTaskState();
            if (_cityArtworkOperation?.Kind == "initial") _ = RunAsync(LeaveCityArtworkAsync);
            SetError(payload.GetProperty("message").GetString()!); return;
        }
        if (name is "artworkExported" or "artworkExportFailed")
        {
            if (payload.GetProperty("documentEpoch").GetInt32() != _engineDocumentEpoch) return;
            if (_exportWindow is { } window && payload.TryGetProperty("requestId", out _))
                _ = RunAsync(() => window.HandleEventAsync(name == "artworkExported" ? "exportDone" : "error", payload));
            else if (name == "artworkExportFailed") SetError(payload.GetProperty("message").GetString()!);
            else ShowNotice(payload.GetProperty("path").GetString()!);
            return;
        }
        if (_exportWindow is { } export) _ = RunAsync(() => export.HandleEventAsync(name, payload));
        switch (name)
        {
            case "documentOpening":
                UnmountArtworkEditor();
                _cityArtwork = _cityArtworkUpdating = false; _pendingCityArtwork = null;
                _cityArtworkOperation = null;
                _engineDocumentEpoch = payload.GetProperty("documentEpoch").GetInt32();
                break;
            case "progress":
                UpdateMapProgress(payload);
                break;
            case "busy":
                if (_engineUnavailable) break;
                _engineBusy = payload.GetProperty("value").GetBoolean();
                if (!_engineBusy) _compatibilityRendering = false;
                SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object &&
                    _snapshot.GetProperty("hasDocument").GetBoolean() &&
                    !payload.GetProperty("value").GetBoolean());
                ApplyMapTaskState();
                if (!_engineBusy && _compatibilityPending) _ = RunAsync(StartCompatibilityPreviewAsync);
                break;
            case "meta":
                if (payload.GetProperty("source").GetString() == "open")
                {
                    _loadingDocument = false;
                    if (!_viewerReady)
                    {
                        _awaitingScene = _waitingSceneReady = false;
                        _mapTask.Reset(false);
                    }
                    ApplyMapTaskState();
                }
                if (payload.GetProperty("source").GetString() == "open" &&
                    payload.GetProperty("info").TryGetProperty("fileWarnings", out var fileWarnings) &&
                    fileWarnings.GetArrayLength() > 0)
                    ShowNotice(string.Join("\n", fileWarnings.EnumerateArray().Select(warning => warning.GetString())));
                _ = RunAsync(RefreshSnapshotAsync);
                if (payload.GetProperty("source").GetString() == "open" && _compatibilityPreview) QueueCompatibilityPreview();
                break;
            case "sceneCompiled":
                if (_discardSceneResults || !_viewerReady ||
                    payload.GetProperty("documentEpoch").GetInt32() != _engineDocumentEpoch) break;
                if (!_mapTask.Active)
                    BeginMapTask(MapTaskPhase.Generating, UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_B02EAEF59F"), background: !string.IsNullOrEmpty(_sceneId));
                _fullSceneCompiled = true;
                ObservePreviewWait();
                ApplyMapTaskState();
                break;
            case "compatibilityPreviewReady":
            case "compatibilityPreviewFailed":
            case "compatibilityPreviewCancelled":
                _ = RunAsync(() => HandleCompatibilityPreviewAsync(name, payload));
                break;
            case "sceneDetailError":
                if (_discardSceneResults || payload.GetProperty("documentEpoch").GetInt32() != _engineDocumentEpoch ||
                    payload.GetProperty("sceneId").GetString() != _sceneId || payload.GetProperty("revision").GetInt32() != _revision) break;
                CompleteMapTask(_mapTask.Generation);
                OperationError.Severity = InfoBarSeverity.Warning;
                OperationError.Message = payload.GetProperty("notice").GetString();
                OperationError.Content = ErrorFeedback.Actions(TaskError.FromPayload(payload), Root);
                OperationError.IsOpen = true;
                break;
            case "sceneReady":
                if (_discardSceneResults || !_viewerReady) break;
                if (payload.GetProperty("sceneId").GetString() == _sceneId && payload.GetProperty("revision").GetInt32() < _revision) break;
                if (!_mapTask.Active)
                {
                    var incremental = !string.IsNullOrEmpty(_sceneId) && payload.GetProperty("sceneId").GetString() == _sceneId;
                    BeginMapTask(MapTaskPhase.Generating, UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_B02EAEF59F"),
                        canCancel: !incremental, background: incremental);
                }
                _waitingSceneReady = false; _awaitingScene = true;
                _sceneId = payload.GetProperty("sceneId").GetString() ?? "";
                _revision = payload.GetProperty("revision").GetInt32();
                StartupTrace.Write($"Preview loadScene: epoch={_engineDocumentEpoch}, scene={_sceneId}, revision={_revision}, completion={payload.GetProperty("completion")}");
                if (payload.TryGetProperty("warnings", out var poiWarnings) && poiWarnings.GetArrayLength() > 0)
                    ShowNotice(string.Join("\n", poiWarnings.EnumerateArray().Select(warning => warning.GetString())));
                if (payload.GetProperty("completion").GetString() != "base")
                    _mapTask.Progress(95, UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_284E5114E0"));
                ApplyMapTaskState();
                SendViewer(new { type = "loadScene", manifestUrl = payload.GetProperty("manifestUrl").GetString(),
                    sceneId = _sceneId, revision = _revision });
                break;
            case "exportDone":
                if (_browserPreviewRequestId is not null && payload.TryGetProperty("requestId", out var browserId) && browserId.GetString() == _browserPreviewRequestId)
                    _ = RunAsync(() => OpenBrowserPreviewResultAsync(payload));
                ApplyMapTaskState();
                break;
            case "exportCancelled":
                if (_browserPreviewRequestId is not null && payload.TryGetProperty("requestId", out var cancelledId) && cancelledId.GetString() == _browserPreviewRequestId)
                { _browserPreviewRequestId = null; _externalExporting = false; }
                ShowNotice(UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_A37778F17C"));
                ApplyMapTaskState();
                break;
            case "rmpProjectReady":
            case "rmpProjectError":
                if (payload.GetProperty("requestId").GetString() != _rmpGenerationId) break;
                if (name == "rmpProjectReady") _rmpGeneration?.TrySetResult(payload.GetProperty("draft").Clone());
                else if (_cancellingRmp) _rmpGeneration?.TrySetCanceled();
                else _rmpGeneration?.TrySetException(TaskError.FromPayload(payload));
                break;
            case "themePreviewReady":
            case "themePreviewFailed":
                _themeEditor?.HandlePreviewEvent(name, payload);
                break;
            case "themeThumbnailReady":
                _ = RunAsync(() => RefreshThemeThumbnailsAsync(payload.GetProperty("theme").GetString()!));
                break;
            case "themeThumbnailFailed":
                ShowNotice(UiLanguage.Text("ThemeEditor_ThumbnailFailed") + " " + payload.GetProperty("message").GetString());
                break;
            case "externalExportDone":
                _externalExporting = false; ApplyMapTaskState();
                ShowNotice(UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_507F381756") + payload.GetProperty("directory").GetString());
                break;
            case "error":
            case "sceneError":
            case "protocolError":
                if (_browserPreviewRequestId is not null && payload.TryGetProperty("requestId", out var failedBrowserId) && failedBrowserId.GetString() == _browserPreviewRequestId)
                {
                    _browserPreviewRequestId = null; _externalExporting = false;
                    SetError(TaskError.FromPayload(payload));
                    break;
                }
                if (payload.TryGetProperty("context", out _) && payload.TryGetProperty("format", out _)) break;
                if (payload.TryGetProperty("documentEpoch", out var errorEpoch) &&
                    errorEpoch.GetInt32() != _engineDocumentEpoch) break;
                if (payload.TryGetProperty("requestId", out var errorId) && errorId.ValueKind == JsonValueKind.String && errorId.GetString() != _mapTask.RequestId) break;
                if (name == "sceneError") _ = FailPreviewAsync(TaskError.FromPayload(payload), "sceneCompile");
                else SetError(TaskError.FromPayload(payload));
                break;
        }
    }

    private void HandleViewerEvent(string text)
    {
        try
        {
            using var document = JsonDocument.Parse(text);
            var frame = document.RootElement;
            var name = frame.GetProperty("type").GetString();
            if (name == "artworkSnapshotNeeded")
            {
                if (!MatchesCityArtwork(frame) || _cityArtworkOperation is not { } current) return;
                _ = RunAsync(async () => {
                    var artwork = await _engine.RequestAsync("getCityArtworkSnapshot", new {
                        documentEpoch = current.Epoch, artworkRevision = current.Revision });
                    if (artwork.ValueKind == JsonValueKind.Object && _cityArtworkOperation?.Id == current.Id &&
                        MatchesCityArtwork(artwork) && current.SceneId == _sceneId && current.SceneRevision == _revision)
                        SendViewer(new { type = "loadArtwork", sceneId = _sceneId, revision = _revision, artwork });
                });
                return;
            }
            if (name == "artworkFailed")
            {
                if (!MatchesCityArtwork(frame)) return;
                _cityArtworkUpdating = false; ApplyMapTaskState();
                if (_cityArtworkOperation?.Kind == "initial") _ = RunAsync(LeaveCityArtworkAsync);
                SetError(frame.GetProperty("message").GetString()!);
                return;
            }
            if (name == "artworkPresented")
            {
                var artworkFrame = frame.Clone();
                if (!MatchesCityArtwork(artworkFrame) || _cityArtworkOperation is not { } operation ||
                    operation.SceneId != _sceneId || operation.SceneRevision != _revision) return;
                _cityArtworkUpdating = false; ApplyMapTaskState();
                _ = SetArtworkToolAsync(_artworkTool);
                RestoreArtworkView();
                TryFocusArtworkStyle();
                {
                    var elapsed = System.Diagnostics.Stopwatch.GetElapsedTime(operation.Started).TotalMilliseconds;
                    var delivery = new { artworkRevision = operation.Revision,
                        serializeMs = operation.SerializeMs, jsonChars = operation.JsonChars,
                        sendToAckMs = System.Diagnostics.Stopwatch.GetElapsedTime(operation.Sent).TotalMilliseconds };
                    _ = RunAsync(async () => { await _engine.RequestAsync("artworkPresented", new {
                        documentEpoch = artworkFrame.GetProperty("documentEpoch").GetInt32(),
                        artworkRevision = artworkFrame.GetProperty("artworkRevision").GetInt32(),
                        operationId = operation.Id, operationKind = operation.Kind,
                        performance = artworkFrame.GetProperty("performance"), hostElapsedMs = elapsed,
                        hostDelivery = delivery }); });
                }
                return;
            }
            if (name == "artworkLeft")
            {
                if (_awaitingScene) return;
                if (MatchesCityArtwork(frame)) {
                    _ = RunAsync(async () => { await TryLeaveArtworkEditorAsync(); });
                }
                return;
            }
            if (name == "requestLeaveArtwork")
            {
                if (MatchesCityArtwork(frame)) _ = RunAsync(async () => { await TryLeaveArtworkEditorAsync(); });
                return;
            }
            if (name == "artworkViewChanged")
            {
                if (MatchesCityArtwork(frame)) {
                    var scale = frame.GetProperty("scale").GetDouble();
                    _artworkObservation.Text = $"{scale*100:0.#}%";
                    _artworkView = new ArtworkObservation(scale, frame.GetProperty("x").GetDouble(), frame.GetProperty("y").GetDouble(),
                        frame.GetProperty("viewportWidth").GetDouble(), frame.GetProperty("viewportHeight").GetDouble());
                }
                return;
            }
            if (name == "navigationOpened") { ShowImmersiveSuggestion(); return; }
            if (name == "exitImmersive") { SetImmersiveMode(false); return; }
            if (name == "previewQualityApplied")
            {
                if (_previewQualityRequestId is not null && frame.TryGetProperty("requestId", out var qualityRequest) &&
                    qualityRequest.GetString() == _previewQualityRequestId)
                    _previewQualityCompletion?.TrySetResult(frame.Clone());
                return;
            }
            if (name == "requestLayerVisibility")
            {
                _ = RunAsync(() => RequestPreviewLayerAsync(frame.Clone()));
                return;
            }
            if (name == "mapInteractionGuideOpened") { _mapGuideOpen = true; return; }
            if (name == "mapInteractionGuideAcknowledged")
            {
                UserData.WriteMapInteractionGuideSeen(_ribbonLayoutDirectory);
                _mapInteractionGuideSeen = true;
                return;
            }
            if (name == "mapInteractionGuideClosed")
            {
                _mapGuideOpen = false; TryShowSimplifiedRibbonIntro(); return;
            }
            if (name == "ready")
            {
                if (_viewerReady) return;
                _viewerReady = true;
                _previewStarting = false;
                if (!_awaitingScene)
                {
                    if (_mapTask.Phase == MapTaskPhase.Loading && _snapshot.ValueKind == JsonValueKind.Object && !_snapshot.GetProperty("hasDocument").GetBoolean())
                    {
                        _mapTask.Reset(false); ApplyMapTaskState();
                    }
                }
                SendViewer(new { type = "setLanguage", language = UiLanguage.Language });
                SendViewer(new { type = "setPreviewQuality", quality = new { pixelScale = _previewQuality.PixelScale,
                    lineDetail = _previewQuality.LineDetail, polygonDetail = _previewQuality.PolygonDetail } });
                var readyInstance = _previewInstance;
                _ = RunAsync(async () =>
                {
                    await _engine.RequestAsync("setPreviewEnabled", new { enabled = true });
                    if (_closing || readyInstance != _previewInstance) return;
                    await RefreshSnapshotAsync();
                    if (_closing || readyInstance != _previewInstance) return;
                    if (!_loadingDocument && _snapshot.GetProperty("hasDocument").GetBoolean()) {
                        if (_cityArtwork && _restoreCityArtworkPreview) {
                            _restoreCityArtworkPreview = false;
                            BeginMapTask(MapTaskPhase.Generating, EditorText("ResetCanvas"), canCancel: false);
                            await _engine.RequestAsync("restoreCityArtworkPreview", new { documentEpoch = _engineDocumentEpoch });
                        }
                        else await CompilePreviewAsync();
                    }
                });
                ApplyMapTaskState();
                return;
            }
            if (name is "sceneLoaded" or "scenePresentationReady" or "selectionChanged" or "selectionCleared" or "objectSelected" or "objectSelectionCleared")
            {
                if (frame.GetProperty("sceneId").GetString() != _sceneId || frame.GetProperty("revision").GetInt32() != _revision)
                {
                    StartupTrace.Write($"Ignored viewer event: {name}, expected={_sceneId}/{_revision}, received={frame}");
                    return;
                }
            }
            switch (name)
            {
                case "sceneLoaded":
                    if (_waitingSceneReady || _discardSceneResults || !_mapTask.Active)
                    {
                        StartupTrace.Write($"Ignored sceneLoaded: scene={_sceneId}, revision={_revision}, waiting={_waitingSceneReady}, discarded={_discardSceneResults}, active={_mapTask.Active}");
                        break;
                    }
                    StartupTrace.Write($"Preview sceneLoaded: scene={_sceneId}, revision={_revision}, presentation={frame.GetProperty("performance").GetProperty("completion")}");
                    if (frame.TryGetProperty("bundleId", out var bundleId))
                        _ = RunAsync(() => _engine.RequestAsync("previewPresented", new { bundleId = bundleId.GetString(),
                            revision = frame.GetProperty("revision").GetInt32(),
                            completion = frame.GetProperty("completion").GetString(), performance = frame.GetProperty("performance").Clone() }));
                    _awaitingScene = false;
                    if (frame.GetProperty("performance").GetProperty("completion").GetString() == "timeout")
                    {
                        if (_fullSceneCompiled && !_previewWaitDismissed) _previewSlow = true;
                        ApplyMapTaskState();
                        break;
                    }
                    if (_context != "metro")
                    {
                        if (frame.GetProperty("completion").GetString() == "base")
                        {
                            _mapTask.PresentBase(UiLanguage.Text("MainWindow_xaml_HandleViewerEvent_4C810816D7"));
                            SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object &&
                                _snapshot.GetProperty("hasDocument").GetBoolean() && !_engineBusy && !_engineUnavailable);
                            ApplyMapTaskState();
                        }
                        else CompleteMapTask(_mapTask.Generation);
                    }
                    if (frame.GetProperty("completion").GetString() == "complete" &&
                        frame.GetProperty("performance").GetProperty("completion").GetString() == "idle")
                        _presentedGuideScene = (_sceneId, _revision);
                    var presentedScene = _sceneId;
                    var presentedRevision = _revision;
                    _ = RunAsync(async () => {
                        await ApplyPresentedSceneStateAsync(presentedScene, presentedRevision);
                        TryPresentCityArtwork(); });
                    RestoreInspectorSelection();
                    RestoreBuildingHighlight();
                    break;
                case "scenePresentationReady":
                    if (frame.GetProperty("completion").GetString() == "complete")
                    {
                        if (!_discardSceneResults && _mapTask.Active) CompleteMapTask(_mapTask.Generation);
                        _presentedGuideScene = (_sceneId, _revision);
                        TryShowMapInteractionGuide();
                    }
                    break;
                case "selectionChanged":
                    _selectedBounds = frame.GetProperty("bounds").Clone();
                    if (_selectedRegionSource != "random_spot") _selectedRegionSource = "selection";
                    UpdateCommandStates();
                    ApplyMapTaskState();
                    break;
                case "selectionCleared":
                    _selectedBounds = null;
                    _selectedRegionSource = null;
                    UpdateCommandStates();
                    break;
                case "objectSelected":
                    _ = RunAsync(() => ShowObjectInspectorAsync(frame));
                    break;
                case "objectSelectionCleared":
                    ShowInspectorHome();
                    break;
                case "renderError":
                case "sceneDiscarded":
                    StartupTrace.Write("Viewer event: " + frame);
                    if (name == "sceneDiscarded" && frame.GetProperty("outcome").GetString() == "stale") break;
                    if (frame.TryGetProperty("fatal", out var fatal) && !fatal.GetBoolean())
                    {
                        OperationError.Severity = InfoBarSeverity.Warning;
                        OperationError.Message = frame.GetProperty("message").GetString();
                        OperationError.IsOpen = true;
                        break;
                    }
                    if (frame.TryGetProperty("sceneId", out var failedScene) && failedScene.GetString() != _sceneId ||
                        frame.TryGetProperty("revision", out var failedRevision) && failedRevision.GetInt32() != _revision) break;
                    _ = FailPreviewAsync(new InvalidOperationException(frame.GetProperty("message").GetString()), "sceneLoad");
                    break;
                case "previewContextLost":
                    if (_fullSceneCompiled && !_previewWaitDismissed) _previewSlow = true;
                    ApplyMapTaskState();
                    break;
                case "previewContextRestored":
                    _previewSlow = false; ApplyMapTaskState();
                    break;
            }
        }
        catch (Exception error) { _ = FailPreviewAsync(error, "viewerMessage"); }
    }

    private async Task ApplySceneStateAsync()
    {
        var state = await _engine.RequestAsync("getSnapshot");
        _snapshot = state;
        var theme = await _engine.RequestAsync("getPreviewTheme");
        SendViewer(new { type = "applyTheme", theme, sceneId = _sceneId, revision = _revision });
        foreach (var item in state.GetProperty("layers").EnumerateObject())
            SendViewer(LayerVisibilityCommand.Create(item.Name, item.Value.GetBoolean(), _sceneId, _revision));
        await ApplyContextAsync();
        await RefreshBuildingCatalogAsync();
        if (SettingsPanel.Children.Count == 0) ShowMapOverview();
    }

    private void SendViewer(object command)
    {
        if (!_viewerReady || MapView.CoreWebView2 is null) return;
        MapView.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(command));
    }

    private async Task SafeRequestAsync(string method, object? parameters = null)
    {
        try { await _engine.RequestAsync(method, parameters); }
        catch (Exception error) { SetError(error); }
    }

    private async Task RunAsync(Func<Task> run)
    {
        try { await run(); }
        catch (Exception error) { if (!_closing) SetError(error); }
    }

    private void SetError(Exception error)
    {
        var failure = TaskError.FromException(error,
            _context == "metro" ? UiLanguage.Text("Failure_Metro") : UiLanguage.Text("Failure_Operation"), _mapTask.Phase.ToString());
        if (error is not TaskError)
            failure = new TaskError(failure.Message, failure.Details + $"\nRequestId: {_mapTask.RequestId}\nDocumentGeneration: {_documentGeneration}");
        SetError(failure);
    }

    private void SetError(TaskError error)
    {
        SetError(error.Message, error.Details);
        OperationError.Content = ErrorFeedback.Actions(error, Root);
    }

    private void SetError(string message, string? details = null)
    {
        _previewWait?.Cancel();
        _fullSceneCompiled = _previewSlow = false;
        _loadingDocument = false;
        OperationError.Content = null;
        _recentErrors.Enqueue(DateTime.Now.ToString("HH:mm:ss") + " " + (details ?? message));
        while (_recentErrors.Count > 30) _recentErrors.Dequeue();
        StartupTrace.Write("Error: " + (details ?? message));
        _externalExporting = false;
        _awaitingScene = _waitingSceneReady = false;
        _discardSceneResults = true;
        _mapTask.Fail(message, _mapTask.Generation);
        SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object && _snapshot.GetProperty("hasDocument").GetBoolean() && !_engineBusy && !_engineUnavailable);
        ApplyMapTaskState();
        OperationError.Severity = InfoBarSeverity.Error;
        OperationError.Message = message;
        OperationError.IsOpen = true;
    }
}
