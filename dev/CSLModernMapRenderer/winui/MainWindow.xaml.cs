// 初始化主窗口并连接主要界面事件。
using System.Collections.ObjectModel;
using System.Reflection;
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
    private readonly JsonElement? _initialMetadata;
    private bool _showLanguageNotice;
    private bool _initialized;
    private readonly string[] _arguments;
    private readonly Dictionary<string, RibbonSplitButton> _layerButtons = new();
    private readonly List<Control> _documentControls = new();
    private readonly Queue<object> _viewerCommands = new();
    private JsonElement _snapshot;
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
    private PoiMappingDialog? _poiEditor;

    public MainWindow(string[] arguments) : this(arguments, new EngineClient(), null) { }

    internal MainWindow(string[] arguments, EngineClient engine, JsonElement? metadata, bool showLanguageNotice = false)
    {
        _arguments = arguments;
        _engine = engine;
        _initialMetadata = metadata;
        _showLanguageNotice = showLanguageNotice;
        _ = UiLanguage.Language;
        try { InitializeComponent(); }
        catch (Exception ex) { StartupTrace.Write("MainWindow XAML exception: " + ex); throw; }

        Title = "CSLModernMap";
        SizeInitialWindow();
        // 主题变化通知中避免重写功能区资源。
        Root.ActualThemeChanged += (_, _) => Root.DispatcherQueue.TryEnqueue(ApplyRibbonColors);
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        var iconPath = Path.Combine(AppContext.BaseDirectory, "app.ico");
        try
        {
            if (File.Exists(iconPath)) AppWindow.SetIcon(iconPath);
            else StartupTrace.Write("App icon missing: " + iconPath);
        }
        catch (Exception error) { StartupTrace.Write("App icon failed: " + error.Message); }
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

        _engine.EventReceived += (name, payload) => Root.DispatcherQueue.TryEnqueue(() => HandleEngineEvent(name, payload));
        _engine.Faulted += message => Root.DispatcherQueue.TryEnqueue(() =>
        {
            _rmpGeneration?.TrySetException(new IOException(message));
            _exportWindow?.HandleEngineFailure(message);
            SetError(message);
        });
        MapView.CoreWebView2Initialized += (_, args) =>
        {
            if (args.Exception is { } error)
            {
                StartupTrace.Write("Map WebView2 initialization failed: " + error);
                SetError(UiLanguage.Text("MainWindow_xaml_View_526F5B284F") + $"{error.Message} (0x{error.HResult:X8})");
            }
            else if (MapView.CoreWebView2 is { } core)
            {
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.AreDevToolsEnabled = false;
                core.WebMessageReceived += (_, eventArgs) =>
                    Root.DispatcherQueue.TryEnqueue(() => HandleViewerEvent(eventArgs.TryGetWebMessageAsString()));
            }
        };
        AppWindow.Closing += (sender, args) =>
        {
            if (_allowMainClose) return;
            args.Cancel = true;
            _ = RunAsync(CloseEditorsAndWindowAsync);
        };
        Closed += (_, _) => { _closing = true; _rmpView?.Dispose(); _ = _engine.CloseAsync(); };
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
        var scale = Root.XamlRoot?.RasterizationScale ?? 1.0;
        var compact = Root.ActualWidth < 1080;
        InspectorColumn.Width = new GridLength(_context == "metro" || compact ? 0 : 320);
        Grid.SetColumn(InspectorBorder, compact ? 1 : 2);
        Grid.SetColumnSpan(InspectorBorder, compact ? 2 : 1);
        InspectorBorder.HorizontalAlignment = compact ? HorizontalAlignment.Right : HorizontalAlignment.Stretch;
        InspectorBorder.Width = compact ? Math.Min(360, Math.Max(200, Root.ActualWidth - 260)) : double.NaN;
        InspectorBorder.Visibility = _context == "metro" || compact && !_inspectorOpen ? Visibility.Collapsed : Visibility.Visible;
        SettingsColumn.Width = new GridLength(Root.ActualWidth < 850 ? 240 : 290);
        InspectorClose.Visibility = compact ? Visibility.Visible : Visibility.Collapsed;
        InspectorToggle.Visibility = _context != "metro" && compact ? Visibility.Visible : Visibility.Collapsed;
        LayoutToolbar();
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
        AddLayerGroup(layers, UiLanguage.Text("MainWindow_xaml_BuildRibbon_4E162D443D"), ("terrain", UiLanguage.Text("MainWindow_xaml_BuildRibbon_51695AD45A")), ("water", UiLanguage.Text("MainWindow_xaml_BuildRibbon_207E6D1CF5")));
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
        _themeGalleries = new ThemeGalleryBinding(_themeChoices, async theme =>
        {
            await RunAsync(async () =>
            {
                await _engine.RequestAsync("setTheme", new { theme });
                await RefreshLivePreviewAsync();
                await RefreshSnapshotAsync();
            });
        });
        Group(start, UiLanguage.Text("MainWindow_xaml_BuildRibbon_8A0BB16307")).Items.Add(CreateThemeGallery());
        AddButton(Group(start, UiLanguage.Text("MainWindow_xaml_BuildRibbon_CA556628F1")), UiLanguage.Text("MainWindow_xaml_BuildRibbon_74F45217E6"), EditThemeAsync, "style.edit_theme", true);
        var quickCreate = Group(start, UiLanguage.Text("MainWindow_xaml_BuildRibbon_A0709CF187"));
        AddButton(quickCreate, UiLanguage.Text("MainWindow_xaml_BuildRibbon_5A85707DE5"), EnterTransitAsync, "view.transit", true);
        AddButton(quickCreate, UiLanguage.Text("MainWindow_xaml_BuildRibbon_FA5D23884E"), EnterMetroAsync, "view.metro", true);
        AddButton(quickCreate, UiLanguage.Text("MainWindow_xaml_BuildRibbon_485DDBB48A"), PickRandomRegionAsync, "create.random_spot", true);
        start.Groups.Remove(output);
        start.Groups.Add(output);
        Group(style, UiLanguage.Text("MainWindow_xaml_BuildRibbon_8A0BB16307")).Items.Add(CreateThemeGallery());
        AddButton(Group(style, UiLanguage.Text("MainWindow_xaml_BuildRibbon_AB98197175")), UiLanguage.Text("MainWindow_xaml_BuildRibbon_74F45217E6"), EditThemeAsync, "style.edit_theme", true);
        _poiGallery = new RibbonGallery { ItemWidth = 88, ItemHeight = 82, MaxColumns = 1,
            Width = 96, HorizontalAlignment = HorizontalAlignment.Left,
            Size = RibbonItemSize.Large,
            ItemTemplate = (DataTemplate)Root.Resources["PoiStylePreviewTemplate"], ItemsSource = _poiChoices };
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
        AddButton(_transitOutputGroup, UiLanguage.Text("ContextRibbon_Export"), () => ShowExportAsync("PNG", "transit"), "view.transit.export", true);
        _transitCloseGroup = Group(_transitTab, UiLanguage.Text("ContextRibbon_Close"));
        AddButton(_transitCloseGroup, UiLanguage.Text("MainWindow_xaml_BuildRibbon_71B67FAAD9"), LeaveContextAsync, "view.transit.close", true);
        _metroTab = new RibbonTab { Header = UiLanguage.Text("MainWindow_xaml_BuildRibbon_FA5D23884E") };
        BuildRmpRibbon();
        Ribbon.SelectedTab = start;
        SetDocumentEnabled(false);
    }

    private static RibbonGroup Group(RibbonTab tab, string name)
    {
        var group = new RibbonGroup { Header = name };
        tab.Groups.Add(group);
        return group;
    }

    private RibbonGallery CreateThemeGallery()
    {
        var gallery = new RibbonGallery { ItemWidth = 88, ItemHeight = 82, MaxColumns = 5,
            Width = 472, HorizontalAlignment = HorizontalAlignment.Left,
            Size = RibbonItemSize.Large,
            ItemTemplate = (DataTemplate)Root.Resources["ThemePreviewTemplate"] };
        ToolTipService.SetToolTip(gallery, UiLanguage.Text("MainWindow_xaml_CreateThemeGallery_8C82CE6C56"));
        _themeGalleries!.Bind(gallery);
        _documentControls.Add(gallery);
        return gallery;
    }

    private void AddButton(RibbonGroup group, string title, Func<Task> run, string id,
        bool large = false, bool requiresDocument = true, VirtualKey? key = null)
    {
        var button = new RibbonButton { Label = title, Icon = RibbonIcons.Create(id, large),
            Size = large ? RibbonItemSize.Large : RibbonItemSize.Medium };
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
                () => ToggleLayerAsync(key), error => SetError(error.Message));
            var flyout = new MenuFlyout();
            var details = new MenuFlyoutItem { Text = UiLanguage.Text("MainWindow_xaml_AddLayerGroup_4358ED3763") };
            details.Click += async (_, _) => await RunAsync(async () =>
            {
                if (key is "district" or "zone") await ShowRegionsAsync(key == "district" ? "districts" : "parks");
                else if (key == "label") await ShowMapPoiSettingsAsync();
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
                var lines = new RibbonButton { Label = UiLanguage.Text("MainWindow_xaml_AddLayerGroup_5060977906"), Icon = RibbonIcons.Create("view.transit.lines"),
                    Size = RibbonItemSize.Medium };
                lines.Click += async (_, _) => await RunAsync(ShowTransitLinesAsync);
                group.Items.Add(lines);
                _mapLayerSettingsButtons.Add(lines);
                RegisterRibbonColors(lines, lines.Icon, "PRIMARY");
            }
        }
    }


    private static IconElement IconForLayer(string key, bool large = false) => RibbonIcons.Create("layer." + key, large);

    private async Task InitializeAsync()
    {
        if (_initialized || _closing) return;
        _initialized = true;
        BeginMapTask(MapTaskPhase.Loading, UiLanguage.Text("MainWindow_xaml_InitializeAsync_656C56EE98"), canCancel: false, waitForScene: false);
        try
        {
            if (_initialMetadata is null) await _engine.StartAsync(_arguments);
            var hello = await _engine.RequestAsync("hello", new { protocolVersion = 1 });
            var url = hello.GetProperty("viewerUrl").GetString() ?? throw new InvalidOperationException(UiLanguage.Text("MainWindow_xaml_InitializeAsync_946C4DFD41"));
            var profile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "CSLModernMap", "WebView2-WinUI");
            Directory.CreateDirectory(profile);
            var browserEnvironment = await CoreWebView2Environment.CreateWithOptionsAsync(
                null, profile, new CoreWebView2EnvironmentOptions());
            await MapView.EnsureCoreWebView2Async(browserEnvironment);
            if (MapView.CoreWebView2 is null) return;
            MapView.Source = new Uri(url);
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
            if (_viewerReady) _mapTask.Reset(false);
            else _mapTask.Progress(null, UiLanguage.Text("MainWindow_xaml_InitializeAsync_1ACE87A3AA"));
            ApplyMapTaskState();
            await ShowLanguageNoticeAsync();
            var file = _arguments.FirstOrDefault(arg => !arg.Equals("gui", StringComparison.OrdinalIgnoreCase));
            if (!string.IsNullOrWhiteSpace(file) && File.Exists(file))
                await OpenPathAsync(file);
        }
        catch (Exception error)
        {
            StartupTrace.Write("Map preview initialization failed: " + error);
            SetError($"{error.Message} (0x{error.HResult:X8})");
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
        if (_themeGalleries is not null)
        {
            _themeGalleries.Synchronize(_snapshot.GetProperty("theme").GetString(), () =>
            {
                var themes = _snapshot.GetProperty("themes").EnumerateArray().ToArray();
                for (var i = 0; i < themes.Length; i++)
                {
                    var id = themes[i].GetProperty("id").GetString()!;
                    var name = themes[i].GetProperty("name").GetString()!;
                    if (i >= _themeChoices.Count) _themeChoices.Add(new ThemeGalleryChoice(id, name));
                    else if (_themeChoices[i].Id != id || _themeChoices[i].Name != name)
                        _themeChoices[i] = new ThemeGalleryChoice(id, name);
                }
                while (_themeChoices.Count > themes.Length) _themeChoices.RemoveAt(_themeChoices.Count - 1);
            });
        }
        if (_poiGallery is not null)
        {
            _syncingPoiGallery = true;
            try { _poiGallery.SelectedItem = _poiChoices.FirstOrDefault(item => item.Id == _snapshot.GetProperty("fields").GetProperty("poi.set").GetString()); }
            finally { _syncingPoiGallery = false; }
        }
        RefreshMapOverview();
        ApplyPendingSettingsSnapshot();
        if (_exportWindow is { } export) await export.RefreshSharedSnapshotAsync();
    }

    private void SetDocumentEnabled(bool enabled)
    {
        enabled &= !_mapTask.Active || _mapTask.Background;
        foreach (var control in _documentControls) control.IsEnabled = enabled;
        foreach (var button in _layerButtons.Values) button.IsEnabled = enabled && _context == "map";
        foreach (var button in _mapLayerSettingsButtons) button.IsEnabled = enabled && _context == "map";
        if (_poiGallery is not null) _poiGallery.IsEnabled = enabled && _context == "map";
        _documentEnabled = enabled;
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
    }

    private async Task OpenCityAsync()
    {
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
        picker.FileTypeFilter.Add(".mmap");
        picker.FileTypeFilter.Add(".gz");
        picker.FileTypeFilter.Add(".json");
        var file = await picker.PickSingleFileAsync();
        if (file is not null) await OpenPathAsync(file.Path);
    }

    private async Task OpenPathAsync(string path)
    {
        if (_exportWindow is { } export && !await export.RequestCloseAsync()) return;
        if (_themeEditor is { } theme && !await theme.RequestCloseAsync()) return;
        if (_poiEditor is { } poi && !await poi.RequestCloseAsync()) return;
        if (!await ReleaseRmpAsync()) return;
        _documentGeneration++;
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
        _awaitingScene = _waitingSceneReady = false;
        _mapTask.Reset(!string.IsNullOrEmpty(_sceneId));
        _context = "transit";
        if (_metroTab is not null) Ribbon.Tabs.Remove(_metroTab);
        RmpWorkspace.Visibility = Visibility.Collapsed;
        if (_transitTab is not null && !Ribbon.Tabs.Contains(_transitTab)) Ribbon.Tabs.Add(_transitTab);
        Ribbon.SelectedTab = Ribbon.Tabs[^1];
        await RefreshSnapshotAsync();
        await ApplyContextAsync();
        await ShowTransitSettingsAsync();
        ApplyMapTaskState();
    }

    private async Task EnterMetroAsync()
    {
        if (RmpBlocked) return;
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
        _context = "map";
        if (_transitTab is not null) Ribbon.Tabs.Remove(_transitTab);
        if (_metroTab is not null) Ribbon.Tabs.Remove(_metroTab);
        RmpWorkspace.Visibility = Visibility.Collapsed;
        _mapTask.Reset(!string.IsNullOrEmpty(_sceneId)); ApplyMapTaskState();
        Ribbon.SelectedTab = Ribbon.Tabs[0];
        await RefreshSnapshotAsync();
        await ApplyContextAsync();
        ShowMapOverview();
        ApplyMapTaskState();
    }

    private async Task ApplyContextAsync(bool refresh = true)
    {
        var state = refresh ? await _engine.RequestAsync("getSnapshot") : _snapshot;
        _snapshot = state;
        ApplySharedRotation();
        ContextText.Text = _context switch { "transit" => UiLanguage.Text("MainWindow_xaml_ApplyContextAsync_5A85707DE5"), "metro" => UiLanguage.Text("MainWindow_xaml_ApplyContextAsync_FA5D23884E") + " · RMP", _ => UiLanguage.Text("MainWindow_xaml_ApplyContextAsync_2E9A956FC6") };
        SetDocumentEnabled(state.GetProperty("hasDocument").GetBoolean() && !_engineBusy);
        UpdateScaleAndLayout();
        ViewerToolbar.Visibility = _context == "metro" ? Visibility.Collapsed : Visibility.Visible;
        MapView.Visibility = _context == "metro" ? Visibility.Collapsed : Visibility.Visible;
        RmpWorkspace.Visibility = _context == "metro" ? Visibility.Visible : Visibility.Collapsed;
        RefreshRmpRibbon();
        RefreshWorkspaceFileMenu();
        if (_context == "transit")
            SendViewer(new { type = "setContextMode", mode = "transit",
                options = state.GetProperty("transitOptions"), sceneId = _sceneId, revision = _revision });
        else
            SendViewer(new { type = "setContextMode", mode = "map",
                options = new { types = state.GetProperty("mapTransitTypes"), line_ids = state.GetProperty("mapTransitLineIds"),
                    stations = state.GetProperty("mapStations"), station_names_enabled = state.GetProperty("mapStationNames") },
                sceneId = _sceneId, revision = _revision });
        SendViewer(LayerVisibilityCommand.Create("poi",
            (_context != "map" || state.GetProperty("layers").GetProperty("label").GetBoolean()) &&
            state.GetProperty("poiVisibility").GetProperty("icons").EnumerateObject().Any(item => item.Value.GetBoolean()),
            _sceneId, _revision));
        if (_inspectorSelection is { } selected && _inspectorContext != _context)
        {
            if (_context == "metro") ShowInspectorHome();
            else await ShowObjectInspectorAsync(selected);
        }
    }

    private Task ShowTransitSettingsAsync() => ShowContextParametersAsync(false);

    private Task ShowMetroSettingsAsync() => ShowContextParametersAsync(true);

    private Task EditThemeAsync() => EditThemeAsync(null);

    private async Task EditThemeAsync(string? section)
    {
        if (_themeEditor is { } existing) { if (section is not null) existing.FocusSection(section); existing.Activate(); return; }
        if (!_openingEditors.Add("theme")) return;
        try
        {
        var draft = await _engine.RequestAsync("getThemeDraft");
        if (_closing || _allowMainClose) return;
        var editor = new ThemeEditorDialog(this, _engine, Root.XamlRoot, draft);
        if (section is not null) editor.FocusSection(section);
        _themeEditor = editor;
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
    private Task CancelCurrentTaskAsync() => CancelCurrentTaskCoreAsync(true);

    private async Task CancelCurrentTaskCoreAsync(bool showStatus)
    {
        if (_generatingRmp) _cancellingRmp = true;
        var result = await _engine.RequestAsync("cancel");
        if (result.TryGetProperty("cancelRequested", out var requested) && requested.GetBoolean())
        {
            if (showStatus) ShowNotice(UiLanguage.Text("MainWindow_xaml_CancelCurrentTaskCoreAsync_E8E08B0F61"));
            return;
        }
        if (result.GetProperty("cancelled").GetBoolean())
        {
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
        _pendingFieldWrites++;
        try
        {
            await RunAsync(async () =>
            {
                await _engine.RequestAsync("setField", new { key, value });
                await RefreshLivePreviewAsync(); await RefreshSnapshotAsync();
            });
        }
        finally { _pendingFieldWrites--; ApplyPendingSettingsSnapshot(); }
    }

    private async Task RefreshLivePreviewAsync(bool backgroundRoadUpdate = false)
    {
        _discardSceneResults = false;
        var previousScene = _sceneId;
        var previousRevision = _revision;
        var result = await _engine.RequestAsync("refreshPreview");
        if (result.TryGetProperty("started", out var started) && started.GetBoolean() && !_mapTask.Active &&
            _sceneId == previousScene && _revision == previousRevision)
        {
            BeginMapTask(MapTaskPhase.Generating,
                backgroundRoadUpdate ? UiLanguage.Text("MainWindow_xaml_RefreshLivePreviewAsync_8CDBE2C406") : _context == "transit" ? UiLanguage.Text("MainWindow_xaml_RefreshLivePreviewAsync_058E698B71") : UiLanguage.Text("MainWindow_xaml_RefreshLivePreviewAsync_A8A3F1C48A"),
                canCancel: !backgroundRoadUpdate, background: backgroundRoadUpdate);
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

    private Task ExportPngAsync() => _context == "metro" ? RunRmpCommandAsync("save") : ShowExportAsync("PNG");
    private Task ExportHtmlAsync() => ShowExportAsync("HTML");


    private async Task ShowAboutAsync()
    {
        var version = typeof(App).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!
            .InformationalVersion.Split('+')[0];
        await new ContentDialog
        {
            XamlRoot = Root.XamlRoot,
            Title = $"CSLModernMap v{version}",
            Content = UiLanguage.Text("MainWindow_xaml_ShowAboutAsync_F5BEA9F4E5"),
            CloseButtonText = UiLanguage.Text("MainWindow_xaml_ShowAboutAsync_3FD47EDCE4"),
        }.ShowAsync();
    }

    private void HandleEngineEvent(string name, JsonElement payload)
    {
        if (_closing) return;
        if (_exportWindow is { } export) _ = RunAsync(() => export.HandleEventAsync(name, payload));
        switch (name)
        {
            case "progress":
                UpdateMapProgress(payload);
                break;
            case "busy":
                _engineBusy = payload.GetProperty("value").GetBoolean();
                SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object &&
                    _snapshot.GetProperty("hasDocument").GetBoolean() &&
                    !payload.GetProperty("value").GetBoolean());
                ApplyMapTaskState();
                break;
            case "meta":
                _ = RunAsync(RefreshSnapshotAsync);
                break;
            case "sceneReady":
                if (_discardSceneResults) break;
                if (payload.GetProperty("sceneId").GetString() == _sceneId && payload.GetProperty("revision").GetInt32() < _revision) break;
                if (!_mapTask.Active) BeginMapTask(MapTaskPhase.Generating, UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_B02EAEF59F"));
                _waitingSceneReady = false; _awaitingScene = true;
                _sceneId = payload.GetProperty("sceneId").GetString() ?? "";
                _revision = payload.GetProperty("revision").GetInt32();
                if (payload.TryGetProperty("warnings", out var poiWarnings) && poiWarnings.GetArrayLength() > 0)
                    ShowNotice(string.Join("\n", poiWarnings.EnumerateArray().Select(warning => warning.GetString())));
                if (payload.GetProperty("completion").GetString() != "base")
                    _mapTask.Progress(95, UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_284E5114E0"));
                ApplyMapTaskState();
                SendViewer(new { type = "loadScene", manifestUrl = payload.GetProperty("manifestUrl").GetString(),
                    sceneId = _sceneId, revision = _revision });
                break;
            case "exportDone":
                ApplyMapTaskState();
                break;
            case "exportCancelled":
                ShowNotice(UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_A37778F17C"));
                ApplyMapTaskState();
                break;
            case "rmpProjectReady":
            case "rmpProjectError":
                if (payload.GetProperty("requestId").GetString() != _rmpGenerationId) break;
                if (name == "rmpProjectReady") _rmpGeneration?.TrySetResult(payload.GetProperty("draft").Clone());
                else if (_cancellingRmp) _rmpGeneration?.TrySetCanceled();
                else _rmpGeneration?.TrySetException(new InvalidOperationException(payload.GetProperty("message").GetString()));
                break;
            case "themePreviewReady":
            case "themePreviewFailed":
                _themeEditor?.HandlePreviewEvent(name, payload);
                break;
            case "externalExportDone":
                _externalExporting = false; ApplyMapTaskState();
                ShowNotice(UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_507F381756") + payload.GetProperty("directory").GetString());
                break;
            case "error":
            case "sceneError":
            case "protocolError":
                SetError(payload.GetProperty("message").GetString() ?? UiLanguage.Text("MainWindow_xaml_HandleEngineEvent_13A46616E1"));
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
            if (name == "ready")
            {
                _viewerReady = true;
                if (!_awaitingScene)
                {
                    if (_mapTask.Phase == MapTaskPhase.Loading && _snapshot.ValueKind == JsonValueKind.Object && !_snapshot.GetProperty("hasDocument").GetBoolean())
                    {
                        _mapTask.Reset(false); ApplyMapTaskState();
                    }
                }
                while (_viewerCommands.TryDequeue(out var command)) SendViewer(command);
                return;
            }
            if (name is "sceneLoaded" or "selectionChanged" or "selectionCleared" or "objectSelected" or "objectSelectionCleared")
            {
                if (frame.GetProperty("sceneId").GetString() != _sceneId || frame.GetProperty("revision").GetInt32() != _revision)
                    return;
            }
            switch (name)
            {
                case "sceneLoaded":
                    if (_waitingSceneReady || _discardSceneResults || !_mapTask.Active) break;
                    if (frame.TryGetProperty("bundleId", out var bundleId))
                        _ = RunAsync(() => _engine.RequestAsync("previewPresented", new { bundleId = bundleId.GetString(),
                            revision = frame.GetProperty("revision").GetInt32(),
                            completion = frame.GetProperty("completion").GetString() }));
                    _awaitingScene = false;
                    if (_context != "metro")
                    {
                        if (frame.GetProperty("completion").GetString() == "base")
                        {
                            _mapTask.PresentBase(UiLanguage.Text("MainWindow_xaml_HandleViewerEvent_4C810816D7"));
                            SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object &&
                                _snapshot.GetProperty("hasDocument").GetBoolean() && !_engineBusy);
                            ApplyMapTaskState();
                        }
                        else CompleteMapTask(_mapTask.Generation);
                    }
                    _ = RunAsync(ApplySceneStateAsync);
                    RestoreInspectorSelection();
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
                    SetError(frame.GetProperty("message").GetString() ?? UiLanguage.Text("MainWindow_xaml_HandleViewerEvent_D71EC6FC43"));
                    break;
            }
        }
        catch (Exception error) { SetError(UiLanguage.Text("MainWindow_xaml_HandleViewerEvent_E38A717AE8") + error.Message); }
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
        if (SettingsPanel.Children.Count == 0) ShowMapOverview();
    }

    private void SendViewer(object command)
    {
        if (!_viewerReady || MapView.CoreWebView2 is null) { _viewerCommands.Enqueue(command); return; }
        MapView.CoreWebView2.PostWebMessageAsJson(JsonSerializer.Serialize(command));
    }

    private async Task SafeRequestAsync(string method, object? parameters = null)
    {
        try { await _engine.RequestAsync(method, parameters); }
        catch (Exception error) { SetError(error.Message); }
    }

    private async Task RunAsync(Func<Task> run)
    {
        try { await run(); }
        catch (Exception error) { if (!_closing) SetError(error.Message); }
    }

    private void SetError(string message)
    {
        _recentErrors.Enqueue(DateTime.Now.ToString("HH:mm:ss") + " " + message);
        while (_recentErrors.Count > 30) _recentErrors.Dequeue();
        StartupTrace.Write("Error: " + message);
        _externalExporting = false;
        _awaitingScene = _waitingSceneReady = false;
        _discardSceneResults = true;
        _mapTask.Fail(message, _mapTask.Generation);
        SetDocumentEnabled(_snapshot.ValueKind == JsonValueKind.Object && _snapshot.GetProperty("hasDocument").GetBoolean() && !_engineBusy);
        ApplyMapTaskState();
        OperationError.Severity = InfoBarSeverity.Error;
        OperationError.Message = message;
        OperationError.IsOpen = true;
    }
}
