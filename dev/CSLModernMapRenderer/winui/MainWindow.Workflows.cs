// 协调主窗口的地图加载与输出工作流。
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;

namespace CSLModernMapWinUI;

public sealed partial class MainWindow
{
    private string _randomScene = "overall", _randomSeed = "";
    private Task PickRandomRegionAsync()
    {
        ClearLayerPanel(SettingsPanel);
        SettingsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_485DDBB48A"), FontSize = 18 });
        var options = LayerSection(SettingsPanel, UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_1D48993136"));
        var scenes = new ComboBox { Header = UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_58CC643C3C") };
        foreach (var (label, key) in new[] { (UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_06BEF4021D"), "overall"), (UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_808A219E24"), "buildings"), (UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_1AB216CC0D"), "transports"), (UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_C14BE6C40C"), "water"), (UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_92C194237A"), "poi"), (UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_A44C7BA211"), "roads") })
        {
            var item = new ComboBoxItem { Content = label, Tag = key }; scenes.Items.Add(item); if (_randomScene == key) scenes.SelectedItem = item;
        }
        scenes.SelectionChanged += (_, _) => _randomScene = (string)((ComboBoxItem)scenes.SelectedItem).Tag;
        var seed = new TextBox { Header = UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_E9F2425B02"), Text = _randomSeed };
        seed.TextChanged += (_, _) => _randomSeed = seed.Text;
        var pick = new Button { Content = UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_E9B45A8940") };
        pick.Click += async (_, _) => await RunAsync(async () =>
        {
            var result = await _engine.RequestAsync("pickRandomRegion", new { scene = _randomScene, seed = string.IsNullOrWhiteSpace(_randomSeed) ? null : _randomSeed });
            if (result.GetProperty("bounds").ValueKind == JsonValueKind.Null) { SetError(UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_AE3F8C8A07")); return; }
            _selectedBounds = result.GetProperty("bounds").Clone();
            _selectedRegionSource = "random_spot";
            var b = _selectedBounds.Value.EnumerateArray().Select(x => x.GetDouble()).ToArray();
            SendViewer(new { type = "setSelection", worldBounds = b, sceneId = _sceneId, revision = _revision });
            SendViewer(new { type = "focusGeoBounds", bounds = new[] { new[] { -b[3] / 111195.0, b[0] / 111195.0 }, new[] { -b[1] / 111195.0, b[2] / 111195.0 } } });
            ShowNotice(UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_9AFF286573")); UpdateCommandStates();
        });
        var export = new Button { Content = UiLanguage.Text("MainWindow_Workflows_PickRandomRegionAsync_9B8845539A") }; export.Click += async (_, _) => await RunAsync(ExportSelectionAsync);
        foreach (var control in new UIElement[] { scenes, seed, pick }) options.Children.Add(control);
        SettingsPanel.Children.Add(export);
        return Task.CompletedTask;
    }
    private bool _changingLanguage;
    private async Task ChangeLanguageAsync()
    {
        if (_changingLanguage) return;
        _changingLanguage = true;
        UpdateCommandStates();
        try { await SelectLanguageAsync(); }
        finally { _changingLanguage = false; UpdateCommandStates(); }
    }
    private async Task SelectLanguageAsync()
    {
        var language = new ComboBox { HorizontalAlignment = HorizontalAlignment.Stretch };
        foreach (var option in UiLanguage.Options)
        {
            var item = new ComboBoxItem { Content = option.DisplayName, Tag = option.code };
            language.Items.Add(item);
            if (option.code == UiLanguage.Language) language.SelectedItem = item;
        }
        var content = new StackPanel { Spacing = 12, MaxWidth = 420 };
        content.Children.Add(language);
        if (await new ContentDialog { XamlRoot = Root.XamlRoot, Title = UiLanguage.Text("MainWindow_Workflows_ChangeLanguageAsync_6545AF163B"), Content = content,
            PrimaryButtonText = UiLanguage.Text("MainWindow_Workflows_ChangeLanguageAsync_0647047FC1"), CloseButtonText = UiLanguage.Text("MainWindow_Workflows_ChangeLanguageAsync_EEA063A21A") }.ShowAsync() != ContentDialogResult.Primary) return;
        var selectedLanguage = (string)((ComboBoxItem)language.SelectedItem).Tag;
        if (selectedLanguage == UiLanguage.Language || !await CloseEditorsAsync() || !await ReleaseRmpAsync()) return;
        var metadata = await _engine.RequestAsync("setLanguage", new { language = selectedLanguage });
        await ReloadLanguageAsync(metadata, showLanguageNotice: true);
    }
    private async Task ShowLanguageNoticeAsync()
    {
        if (!_showLanguageNotice || _closing) return;
        _showLanguageNotice = false;
        var content = new StackPanel { Spacing = 12, MaxWidth = 420 };
        content.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_Workflows_ChangeLanguageAsync_TranslationNotice"), TextWrapping = TextWrapping.Wrap });
        content.Children.Add(new HyperlinkButton
        {
            Content = new TextBlock { Text = UiLanguage.Text("MainWindow_Workflows_ChangeLanguageAsync_ContributeTranslation"), TextWrapping = TextWrapping.Wrap },
            NavigateUri = new Uri("https://github.com/Liuiuera/CSLModernMap-CodeMod/issues"),
            Padding = new Thickness(0), HorizontalAlignment = HorizontalAlignment.Stretch, HorizontalContentAlignment = HorizontalAlignment.Left,
        });
        await new ContentDialog
        {
            XamlRoot = Root.XamlRoot, Title = UiLanguage.Text("LanguageNotice_Title"), Content = content,
            CloseButtonText = UiLanguage.Text("LanguageNotice_Close"), DefaultButton = ContentDialogButton.Close,
        }.ShowAsync();
    }
    private async Task ReloadLanguageAsync(JsonElement metadata, bool showLanguageNotice)
    {
        var sourceFile = _snapshot.ValueKind == JsonValueKind.Object && _snapshot.TryGetProperty("sourceFile", out var file)
            ? file.GetString() : null;
        var arguments = string.IsNullOrEmpty(sourceFile) ? _arguments : new[] { sourceFile };
        _closing = true;
        await _engine.CloseAsync();
        UiLanguage.Configure(metadata);
        Environment.SetEnvironmentVariable("CSLMM_LANG", UiLanguage.Language);
        // 通过资源键重建控件；XAML x:Uid 仅在构造时应用。
        var replacement = new MainWindow(arguments, new EngineClient(), null, showLanguageNotice);
        ((App)Application.Current).ShowWindow(replacement);
        _allowMainClose = true;
        Close();
    }
    private readonly Queue<string> _recentErrors = new();
    private void AddOptionControl(StackPanel panel, string label, JsonElement value, Func<object, Task> commit, JsonElement? descriptor = null)
    {
        if (descriptor is { } schema && schema.GetProperty("choices").GetArrayLength() > 0)
        {
            var combo = new ComboBox { Header = label, HorizontalAlignment = HorizontalAlignment.Stretch };
            foreach (var choice in schema.GetProperty("choices").EnumerateArray())
            {
                var item = new ComboBoxItem { Content = choice.ToString(), Tag = choice.Clone() }; combo.Items.Add(item);
                if (choice.GetRawText() == value.GetRawText()) combo.SelectedItem = item;
            }
            combo.SelectionChanged += async (_, _) => { if (combo.SelectedItem is ComboBoxItem item) await RunAsync(() => commit((JsonElement)item.Tag)); };
            panel.Children.Add(combo);
        }
        else if (value.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            var toggle = AppearanceControls.Toggle(label, value.GetBoolean());
            toggle.Toggled += async (_, _) => await RunAsync(() => commit(toggle.IsOn)); panel.Children.Add(toggle);
        }
        else if (value.ValueKind == JsonValueKind.Number)
        {
            var number = new NumberBox { Header = label, Value = value.GetDouble(), SmallChange = .1 };
            if (descriptor is { } bounds) { number.Minimum = bounds.GetProperty("low").GetDouble(); number.Maximum = bounds.GetProperty("high").GetDouble(); }
            var saved = number.Value;
            number.LostFocus += async (_, _) =>
            {
                if (!panel.Children.Contains(number) || number.Value == saved) return;
                if (!double.IsFinite(number.Value)) { SetError(label + UiLanguage.Text("MainWindow_Workflows_AddOptionControl_6A2DEA0DF7")); return; }
                await RunAsync(async () => { await commit(number.Value); saved = number.Value; });
            };
            panel.Children.Add(number);
        }
        else if (value.ValueKind == JsonValueKind.String)
        {
            var box = new TextBox { Header = label, Text = value.GetString() }; var saved = box.Text;
            box.LostFocus += async (_, _) => { if (panel.Children.Contains(box) && box.Text != saved) await RunAsync(async () => { await commit(box.Text ?? ""); saved = box.Text; }); };
            panel.Children.Add(box);
        }
    }
    private int _regionPageRequest;
    private async Task ShowRegionsAsync(string group)
    {
        var request = ++_regionPageRequest;
        var generation = _documentGeneration;
        await _engine.RequestAsync("getRegionCatalog");
        if (request != _regionPageRequest || generation != _documentGeneration) return;
        ClearLayerPanel(SettingsPanel);
        var layer = group == "districts" ? "district" : "zone";
        SettingsPanel.Children.Add(new TextBlock { Text = LayerTitle(layer), FontSize = 18 });
        AddLayerVisibility(SettingsPanel, SettingsPanel, layer);
        var body = LayerSection(SettingsPanel, UiLanguage.Text("MainWindow_Workflows_ShowRegionsAsync_AD75C5DFE5"));
        var choose = new Button { Content = group == "districts" ? UiLanguage.Text("MainWindow_Workflows_ShowRegionsAsync_DB722F226E") : UiLanguage.Text("MainWindow_Workflows_ShowRegionsAsync_382B8E281A") };
        body.Children.Add(choose);
        BindLayerControl("region-selector:" + group, choose, SettingsPanel, () => { });
        choose.Click += async (_, _) => await RunAsync(() => SelectRegionsAsync(group));
        AddLayerField(LayerSection(SettingsPanel, UiLanguage.Text("MainWindow_Workflows_ShowRegionsAsync_E55E6F2BAC")), SettingsPanel, layer + ".opacity");
    }
    private void ShowInterchanges()
    {
        ClearLayerPanel(SettingsPanel);
        SettingsPanel.Children.Add(new TextBlock { Text = UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_8BB46E9B58"), FontSize = 18 });
        var layout = LayerSection(SettingsPanel, UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_FAA2F2ED67"));
        var count = new NumberBox { Header = UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_5E29D121DD"), Minimum = 1, Maximum = 24, Value = 3 };
        var tile = new NumberBox { Header = UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_3FCAF620E9"), Minimum = 256, Maximum = 4096, Value = 1024, SmallChange = 256 };
        layout.Children.Add(count); layout.Children.Add(tile);
        var options = new Dictionary<string, CheckBox>();
        var content = LayerSection(SettingsPanel, UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_7A68830642"));
        foreach (var (key, label) in new[] { ("poi_names", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_7BF97D3A48")), ("labels", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_655D4E9C75")) })
        {
            var check = new CheckBox { Content = label, IsChecked = true }; options[key] = check; content.Children.Add(check);
        }
        var layers = new Dictionary<string, CheckBox>();
        var layerGroup = LayerSection(SettingsPanel, UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_F2926EB3BB"));
        foreach (var (key, label) in new[] { ("terrain", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_51695AD45A")), ("water", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_207E6D1CF5")), ("road", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_A44C7BA211")), ("building", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_5B46860A8E")), ("poi", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_9FC7D0CF1D")), ("label", UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_1D0FD5F933")) })
        {
            var check = new CheckBox { Content = label, IsChecked = true }; layers[key] = check; layerGroup.Children.Add(check);
        }
        var run = new Button { Content = UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_0074BFB973") };
        run.Click += async (_, _) => await RunAsync(async () =>
        {
            if (!double.IsFinite(count.Value) || !double.IsFinite(tile.Value)) throw new ArgumentException(UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_FEE4029EC6"));
            var picker = new FileSavePicker { SuggestedFileName = "interchanges" }; picker.FileTypeChoices.Add("PNG", new List<string> { ".png" });
            WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(this));
            var file = await picker.PickSaveFileAsync(); if (file is null) return;
            var result = await _engine.RequestAsync("renderInterchanges", new { target = file.Path, request = new { top_count = (int)count.Value, tile_size = (int)tile.Value, poi_names = options["poi_names"].IsChecked == true, labels = options["labels"].IsChecked == true, layers = layers.Where(x => x.Value.IsChecked == true).Select(x => x.Key).ToArray() } });
            if (!result.GetProperty("started").GetBoolean()) SetError(UiLanguage.Text("MainWindow_Workflows_ShowInterchanges_2A55669135"));
        });
        SettingsPanel.Children.Add(run);
    }
    private Task ShowHelpAsync()
    {
        ClearLayerPanel(SettingsPanel);
        foreach (var text in new[] { UiLanguage.Text("MainWindow_Workflows_ShowHelpAsync_B6BEA3CC3D"), UiLanguage.Text("MainWindow_Workflows_ShowHelpAsync_06D721E478"),
            UiLanguage.Text("MainWindow_Workflows_ShowHelpAsync_22944584D4"),
            UiLanguage.Text("MainWindow_Workflows_ShowHelpAsync_A15A32B360"),
            UiLanguage.Text("MainWindow_Workflows_ShowHelpAsync_08D4511AE5"),
            UiLanguage.Text("MainWindow_Workflows_ShowHelpAsync_4B61589458"),
            UiLanguage.Text("MainWindow_Workflows_ShowHelpAsync_E96FF21AC3") })
            SettingsPanel.Children.Add(new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap });
        return Task.CompletedTask;
    }
    private async Task ShowFullDiagnosticsAsync()
    {
        var data = await _engine.RequestAsync("getDiagnostics");
        ClearLayerPanel(SettingsPanel);
        var logs = new Button { Content = UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_2E765B0E76") };
        logs.Click += async (_, _) => await RunAsync(async () => { await Windows.System.Launcher.LaunchFolderPathAsync(System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "CSLModernMap", "logs")); });
        var map = new StackPanel { Spacing = 8 };
        map.Children.Add(new TextBlock { Text = UiLanguage.Format("MainWindow_Workflows_ShowFullDiagnosticsAsync_1EB5F39D42", _context, (_awaitingScene ? UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_D04FCBDA73") : string.IsNullOrEmpty(_sceneId) ? UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_E5C84C9AA7") : UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_B073DB9C0B"))), TextWrapping = TextWrapping.Wrap });
        if (_snapshot.ValueKind == JsonValueKind.Object)
            map.Children.Add(new TextBlock { Text = JsonSerializer.Serialize(_snapshot.GetProperty("cityInfo")), TextWrapping = TextWrapping.Wrap });
        var refresh = new Button { Content = UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_6EDFD8773A") };
        refresh.Click += async (_, _) => await ExecuteCommandAsync("map.refresh_preview"); map.Children.Add(refresh);
        var errors = new StackPanel { Spacing = 8 };
        errors.Children.Add(logs);
        map.Children.Add(new TextBlock { Text = JsonSerializer.Serialize(data.GetProperty("details"), new JsonSerializerOptions { WriteIndented = true }), TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true });
        var errorText = new TextBlock { Text = _recentErrors.Count == 0 ? UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_8E32E45D05") : string.Join("\n", _recentErrors), TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true };
        var clear = new Button { Content = UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_C569EEBE8E") };
        clear.Click += (_, _) => { _recentErrors.Clear(); errorText.Text = UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_8E32E45D05"); };
        errors.Children.Add(clear); errors.Children.Add(errorText);
        var tabs = new TabView { IsAddTabButtonVisible = false, Height = Math.Max(280, Root.ActualHeight - 350) };
        foreach (var (name, page) in new[] { (UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_41AB2E0ECB"), map), (UiLanguage.Text("MainWindow_Workflows_ShowFullDiagnosticsAsync_0BC1FB72AE"), errors) })
            tabs.TabItems.Add(new TabViewItem { Header = name, IsClosable = false, Content = new ScrollViewer { Content = page, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled } });
        SettingsPanel.Children.Add(tabs);
    }

}
